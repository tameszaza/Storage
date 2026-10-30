"""Small, durable page checkpoints. Requests never open source PDFs."""
import json,os,sqlite3,time
from pathlib import Path
import settings as s

def connect():
 s.STATE.mkdir(parents=True,exist_ok=True)
 db=sqlite3.connect(s.STATE/'statistics.sqlite',timeout=2)
 db.execute('PRAGMA cache_size=-1024');db.execute('PRAGMA mmap_size=0');db.execute('PRAGMA temp_store=FILE')
 return db

def initialize():
 with connect() as db:
  db.execute('PRAGMA journal_mode=WAL')
  db.executescript('CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY,fp TEXT,total INTEGER DEFAULT 0,status TEXT,updated INTEGER DEFAULT 0,stats TEXT);CREATE TABLE IF NOT EXISTS pages(path TEXT,page INTEGER,data TEXT,PRIMARY KEY(path,page));CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);')
  db.execute("INSERT OR IGNORE INTO meta VALUES('paused','false')")
  db.execute("INSERT OR IGNORE INTO meta VALUES('scan','true')")
  policy='pdf-ink-and-rounded-vector-paths-v1'
  old=db.execute("SELECT value FROM meta WHERE key='policy'").fetchone()
  if old and json.loads(old[0])!=policy:
   db.execute('DELETE FROM pages');db.execute("UPDATE files SET status='pending',stats=NULL,total=0")
  db.execute("INSERT OR REPLACE INTO meta VALUES('policy',?)",(json.dumps(policy),))

def setting(key,default=None):
 with connect() as db:r=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
 return json.loads(r[0]) if r else default

def set_setting(key,value):
 with connect() as db:db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value)))

def fingerprint(file):
 st=file.stat();return f'{st.st_ino}:{st.st_size}:{st.st_mtime_ns}'

def safe_file(path):
 p=Path(path)
 if p.is_absolute() or any(x in ('','..','.') for x in str(path).split('/')) or p.suffix.lower()!='.pdf':raise ValueError('Invalid library file')
 file=s.ROOT/p
 if file.is_symlink() or not file.resolve().is_relative_to(s.ROOT.resolve()):raise ValueError('Invalid source')
 return file

def inventory():
 if not s.ROOT.is_dir():raise OSError('Library unavailable')
 seen=[];now=time.time();settle=int(os.environ.get('GOODNOTES_STATS_SETTLE_SECONDS','30'))
 # os.walk raises rather than treating a disconnected source as a deletion.
 def error(exc):raise exc
 with connect() as db:
  for directory,dirs,files in os.walk(s.ROOT,followlinks=False,onerror=error):
   dirs[:]=[d for d in dirs if not d.startswith('.') and not (Path(directory)/d).is_symlink()]
   for name in files:
    if not name.lower().endswith('.pdf') or name.startswith('.'):continue
    file=Path(directory)/name
    if file.is_symlink() or not file.is_file():continue
    path=file.relative_to(s.ROOT).as_posix();seen.append(path);st=file.stat()
    if now-st.st_mtime<settle:continue
    fp=fingerprint(file);old=db.execute('SELECT fp FROM files WHERE path=?',(path,)).fetchone()
    if old and old[0]==fp:continue
    db.execute('DELETE FROM pages WHERE path=?',(path,))
    db.execute("INSERT OR REPLACE INTO files VALUES(?,?,0,'pending',0,NULL)",(path,fp))
  # Prune only after a complete, readable inventory, in the same transaction.
  db.execute('CREATE TEMP TABLE current_paths(path TEXT PRIMARY KEY)')
  db.executemany('INSERT INTO current_paths VALUES(?)',[(p,) for p in seen])
  db.execute('DELETE FROM pages WHERE path NOT IN (SELECT path FROM current_paths)')
  db.execute('DELETE FROM files WHERE path NOT IN (SELECT path FROM current_paths)')
 set_setting('last_inventory',int(now));set_setting('scan',False)

def merge(values):
 out={'recorded_strokes':0,'estimated_strokes':0,'length_pt':0.,'ink_pages':0,'scanned_pages':0,'error_pages':0,'colors':{},'widths':{}}
 for value in values:
  if value.get('error'):out['error_pages']+=1;continue
  for key in ['recorded_strokes','estimated_strokes','length_pt']:out[key]+=value.get(key,0)
  out['ink_pages']+=int(value.get('recorded_strokes',0)+value.get('estimated_strokes',0)>0)
  out['scanned_pages']+=1
  for key in ['colors','widths']:
   for k,n in value.get(key,{}).items():out[key][k]=out[key].get(k,0)+n
 return out

def finish(path):
 with connect() as db:
  result=merge(json.loads(row[0]) for row in db.execute('SELECT data FROM pages WHERE path=? ORDER BY page',(path,)))
  db.execute('UPDATE files SET stats=?,status=?,updated=? WHERE path=?',(json.dumps(result),'partial' if result['error_pages'] else 'ready',int(time.time()),path))

def summary(folder=''):
 with connect() as db:
  rows=list(db.execute('SELECT path,total,status,updated,stats FROM files ORDER BY path'))
 rows=[r for r in rows if not folder or r[0].startswith(folder.rstrip('/')+'/')]
 out={'files':len(rows),'total_pages':sum(r[1] for r in rows),'pending_files':sum(r[2]=='pending' for r in rows),'partial_files':sum(r[2] in ('partial','error') for r in rows),'updated':max((r[3] for r in rows),default=0),**merge([])}
 for row in rows:
  if not row[4]:continue
  value=json.loads(row[4])
  for key in ['recorded_strokes','estimated_strokes','length_pt','ink_pages','scanned_pages','error_pages']:out[key]+=value.get(key,0)
  for key in ['colors','widths']:
   for k,n in value.get(key,{}).items():out[key][k]=out[key].get(k,0)+n
 count=out['recorded_strokes']+out['estimated_strokes'];out['distance_m']=out['length_pt']*25.4/72000;out['average_mm']=out['length_pt']*25.4/72/count if count else 0
 out['folders']=sorted({r[0].rsplit('/',1)[0] for r in rows if '/' in r[0]})
 out['paused']=setting('paused',False);out['stage']=setting('stage','Idle');out['last_inventory']=setting('last_inventory',0)
 return out

def files(query='',folder='',offset=0):
 with connect() as db:rows=list(db.execute('SELECT path,total,status,updated,stats FROM files ORDER BY path'))
 rows=[r for r in rows if (not query or query.casefold() in r[0].casefold()) and (not folder or r[0].startswith(folder.rstrip('/')+'/'))]
 return {'count':len(rows),'offset':offset,'limit':s.MAX_LIST,'files':[{'path':p,'pages':n,'status':status,'updated':at,'stats':json.loads(stats) if stats else None} for p,n,status,at,stats in rows[offset:offset+s.MAX_LIST]]}

def detail(path,offset):
 with connect() as db:
  row=db.execute('SELECT total,status,stats FROM files WHERE path=?',(path,)).fetchone()
  if not row:raise KeyError('Notebook not found')
  pages=[{'page':n,**json.loads(d)} for n,d in db.execute('SELECT page,data FROM pages WHERE path=? ORDER BY page LIMIT ? OFFSET ?',(path,s.MAX_LIST,offset))]
 return {'path':path,'total_pages':row[0],'status':row[1],'stats':json.loads(row[2]) if row[2] else None,'pages':pages,'offset':offset,'limit':s.MAX_LIST}
