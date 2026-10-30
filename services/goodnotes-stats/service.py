"""Low-priority, read-only PDF analysis in a separate, bounded container."""
import fcntl,json,os,signal,subprocess,sys,threading,time,tempfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import settings as s,store
WAKE=threading.Event();STOP=threading.Event()

def critical_busy():
 if not s.ROOT.is_dir() or not s.CRITICAL.is_dir():return 'Waiting for storage'
 if (s.CRITICAL/'quartz-site/build-required').exists():return 'Waiting for web build'
 for name,label in [('ocr.lock','OCR'),('model.lock','indexing'),('fallback.lock','essential processing')]:
  path=s.CRITICAL/'lecture-search'/name
  try:
   with path.open('rb') as handle:fcntl.flock(handle,fcntl.LOCK_SH|fcntl.LOCK_NB)
  except FileNotFoundError:continue
  except BlockingIOError:return 'Waiting for '+label
  except OSError:return 'Waiting for storage'
 try:
  available=next(int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:'))
  if available<int(os.environ.get('GOODNOTES_STATS_MIN_FREE_MB','768'))*1024:return 'Waiting for memory'
  if os.getloadavg()[0]>float(os.environ.get('GOODNOTES_STATS_MAX_LOAD','1.5')):return 'Waiting for server load'
 except (OSError,StopIteration):pass
 return ''

def stage(text):
 if store.setting('stage')!=text:store.set_setting('stage',text)

def analyze(path,fp,page):
 file=store.safe_file(path)
 if not file.is_file() or store.fingerprint(file)!=fp:raise InterruptedError('Source changed')
 if file.stat().st_size>s.MAX_FILE_BYTES:return {'error':'File exceeds configured analysis limit'}
 # The request server never imports MuPDF; child RAM is released after each page.
 with tempfile.TemporaryFile() as output, subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('analyze.py')),str(file),str(page)],stdout=output,stderr=subprocess.DEVNULL) as child:
  started=time.monotonic()
  while child.poll() is None:
   reason=critical_busy()
   if reason or store.setting('paused',False) or STOP.is_set():
    child.terminate()
    try:child.wait(timeout=2)
    except subprocess.TimeoutExpired:child.kill();child.wait()
    raise InterruptedError(reason or 'Paused')
   if time.monotonic()-started>s.PAGE_TIMEOUT:
    child.kill();child.wait();return {'error':'Page analysis exceeded configured time limit'}
   time.sleep(.25)
  output.seek(0);raw=output.read(262145)
  if len(raw)>262144:return {'error':'Page result exceeded configured limit'}
  try:value=json.loads(raw)
  except ValueError:return {'error':'PDF analysis unavailable'}
  if store.fingerprint(file)!=fp:raise InterruptedError('Source changed')
  return value

def step():
 if store.setting('paused',False):stage('Paused');return False
 reason=critical_busy()
 if reason:stage(reason);return False
 if store.setting('scan',False):store.inventory()
 with store.connect() as db:
  row=db.execute("SELECT path,fp,total FROM files WHERE status='pending' ORDER BY (path LIKE 'Books/%'),path LIMIT 1").fetchone()
 if not row:stage('Up to date');return False
 path,fp,total=row;stage('Analyzing '+path)
 if not total:
  meta=analyze(path,fp,0)
  with store.connect() as db:
   if meta.get('error'):
    db.execute("UPDATE files SET status='error',stats=?,updated=? WHERE path=? AND fp=?",(json.dumps({'error_pages':1}),int(time.time()),path,fp));return True
   total=int(meta['pages']);db.execute('UPDATE files SET total=? WHERE path=? AND fp=?',(total,path,fp))
 with store.connect() as db:done={r[0] for r in db.execute('SELECT page FROM pages WHERE path=?',(path,))}
 page=next((n for n in range(1,total+1) if n not in done),None)
 if page is None:store.finish(path);return True
 value=analyze(path,fp,page)
 with store.connect() as db:
  if db.execute('SELECT fp FROM files WHERE path=?',(path,)).fetchone()!=(fp,):return True
  db.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?)',(path,page,json.dumps(value)))
 if len(done)+1==total:store.finish(path)
 return True

def worker():
 last_marker=None
 while not STOP.is_set():
  try:
   marker=s.CRITICAL/'lecture-search/pdf-changes'
   current=marker.stat().st_mtime_ns if marker.exists() else None
   if s.AUTO and current!=last_marker:store.set_setting('scan',True);last_marker=current
   if step():continue
  except InterruptedError as error:stage(str(error));store.set_setting('scan',True)
  except Exception as error:stage('Waiting · '+type(error).__name__)
  WAKE.wait(s.POLL);WAKE.clear()

class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def reply(self,status,data):
  raw=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
 def do_GET(self):
  try:
   url=urlsplit(self.path);q=parse_qs(url.query);arg=lambda k,default='':q.get(k,[default])[0]
   offset=max(0,int(arg('offset','0')))
   if url.path=='/health':return self.reply(200,{'ready':True,'stage':store.setting('stage','Starting')})
   if url.path=='/summary':return self.reply(200,store.summary(arg('folder')))
   if url.path=='/files':return self.reply(200,store.files(arg('q')[:200],arg('folder')[:1000],offset))
   if url.path=='/file':return self.reply(200,store.detail(arg('path'),offset))
   self.reply(404,{'error':'Not found'})
  except (ValueError,KeyError):self.reply(400,{'error':'Invalid request'})
  except Exception:self.reply(503,{'error':'Statistics temporarily unavailable'})
 def do_POST(self):
  try:
   if self.path!='/control':return self.reply(404,{'error':'Not found'})
   length=int(self.headers.get('Content-Length',0))
   if not 0<length<=1024:return self.reply(413,{'error':'Request too large'})
   body=json.loads(self.rfile.read(length));action=body.get('action')
   if action in ('pause','resume'):store.set_setting('paused',action=='pause')
   elif action=='scan':store.set_setting('scan',True)
   elif action=='retry-errors':
    with store.connect() as db:
     db.execute("DELETE FROM pages WHERE json_extract(data,'$.error') IS NOT NULL")
     db.execute("UPDATE files SET status='pending' WHERE status IN ('partial','error')")
   else:return self.reply(400,{'error':'Unknown action'})
   WAKE.set();self.reply(200,{'ok':True})
  except (ValueError,TypeError):self.reply(400,{'error':'Invalid request'})

if __name__=='__main__':
 os.umask(0o077);os.nice(19);store.initialize()
 threading.Thread(target=worker,daemon=True).start()
 signal.signal(signal.SIGTERM,lambda *_:(STOP.set(),sys.exit(0)))
 server=ThreadingHTTPServer(('0.0.0.0',s.PORT),Handler)
 server.daemon_threads=True
 server.serve_forever()
