#!/usr/bin/env python3
"""Private CPU helpers. One job at a time; all writable state is external."""
import base64,contextlib,hashlib,hmac,http.server,json,os,re,shutil,signal,sqlite3,subprocess,tarfile,tempfile,threading,time
from pathlib import Path
WORK=Path('/work');SOURCES=WORK/'sources';JOBS=WORK/'jobs';BUSY=threading.Lock();MODEL=None;STOP=threading.Event();CURRENT={'kind':None,'started':None};CHILDREN=set()
TOKEN=json.loads(Path('/run/helper.json').read_text())['token']
def vector_db():
 import sqlite_vec
 db=sqlite3.connect(WORK/'vectors.sqlite');db.enable_load_extension(True);sqlite_vec.load(db);db.enable_load_extension(False)
 db.execute('PRAGMA cache_size=-65536');db.execute('PRAGMA mmap_size=268435456')
 db.executescript('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE IF NOT EXISTS chunks(path TEXT,id INTEGER,embedding BLOB,PRIMARY KEY(path,id));')
 return db
def vector_epoch():
 if not (WORK/'vectors.sqlite').exists():return None
 with sqlite3.connect(WORK/'vectors.sqlite') as db:
  r=db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone();return r[0] if r else None

def vectors(file):
 with file.open() as stream,vector_db() as db:
  header=json.loads(stream.readline(8192));epoch=header.get('epoch');path=header.get('path')
  if not isinstance(epoch,str) or not re.fullmatch('[a-f0-9]{32}',epoch) or not isinstance(path,str) or not path or len(path)>2048:raise ValueError('Invalid vector source')
  # A derived mirror only: replace one changed document atomically.
  current=db.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()
  if current is None or current[0]!=epoch:
   db.execute('DELETE FROM chunks');db.execute("INSERT OR REPLACE INTO meta VALUES('epoch',?)",(epoch,))
  db.execute('DELETE FROM chunks WHERE path=?',(path,));count=0
  while line:=stream.readline(8192):
   if not admitted():raise InterruptedError('Server tasks off')
   value=json.loads(line)
   if not isinstance(value,list) or len(value)!=2 or type(value[0]) is not int:raise ValueError('Invalid vector')
   raw=base64.b64decode(value[1],validate=True)
   if len(raw)!=1024:raise ValueError('Invalid vector size')
   db.execute('INSERT INTO chunks VALUES(?,?,?)',(path,value[0],raw));count+=1
  db.commit();return {'count':count,'epoch':epoch}

def semantic_search(body):
 text=body.get('query');prefix=body.get('folder','');epoch=body.get('epoch')
 if not isinstance(text,str) or not 1<=len(text)<=1500 or not isinstance(prefix,str) or len(prefix)>2048 or epoch!=vector_epoch():raise InterruptedError('Vector mirror pending')
 pattern=prefix.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
 filename_ids=body.get('filenameIds',[])
 if not isinstance(filename_ids,list) or len(filename_ids)>20000 or any(type(i) is not int for i in filename_ids):raise ValueError('Invalid filename identifiers')
 excluded=body.get('excludePaths',[])
 if not isinstance(excluded,list) or len(excluded)>100 or any(not isinstance(p,str) or len(p)>1000 for p in excluded):raise ValueError('Invalid exclusions')
 raw=base64.b64decode(embed({'texts':[text]})['vectors'])
 skip=(' AND path NOT IN ('+','.join('?' for _ in excluded)+')') if excluded else ''
 with vector_db() as db:
  if filename_ids:
   slots=','.join('?' for _ in filename_ids)
   rows=db.execute("SELECT id,path,embedding,vec_distance_cosine(embedding,?) AS distance FROM chunks WHERE path LIKE ? ESCAPE '\\' AND id NOT IN ("+slots+")"+skip+" ORDER BY distance LIMIT 24",(raw,pattern,*filename_ids,*excluded)).fetchall()
   titles=db.execute("SELECT id,path,embedding,vec_distance_cosine(embedding,?) AS distance FROM chunks WHERE path LIKE ? ESCAPE '\\' AND id IN ("+slots+")"+skip+" ORDER BY distance LIMIT 8",(raw,pattern,*filename_ids,*excluded)).fetchall()
   rows=sorted(rows+titles,key=lambda r:r[3])
  else:
   rows=db.execute("SELECT id,path,embedding,vec_distance_cosine(embedding,?) AS distance FROM chunks WHERE path LIKE ? ESCAPE '\\'"+skip+" ORDER BY distance LIMIT 32",(raw,pattern,*excluded)).fetchall()
 return {'hits':[{'id':i,'path':p,'hash':hashlib.sha256(v).hexdigest(),'distance':d} for i,p,v,d in rows]}
os.environ.update(TMPDIR='/work/tmp',HOME='/work/home',XDG_CACHE_HOME='/work/cache',OMP_NUM_THREADS='4',OMP_THREAD_LIMIT='4',OPENBLAS_NUM_THREADS='4',TOKENIZERS_PARALLELISM='false',HF_HUB_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1')
def admitted():
 try:return not STOP.is_set() and json.loads(Path('/control/state.json').read_text()).get('accepting') is True
 except (OSError,ValueError,AttributeError):return False

def run(args,*,cwd=None,timeout=300,log=None,stderr=subprocess.STDOUT):
 if not admitted():raise InterruptedError('Server tasks off')
 with subprocess.Popen(args,cwd=cwd,stdout=log or subprocess.DEVNULL,stderr=stderr,start_new_session=True) as p:
  CHILDREN.add(p.pid);end=time.monotonic()+timeout
  try:
   while p.poll() is None:
    if not admitted() or time.monotonic()>end:
     os.killpg(p.pid,signal.SIGTERM)
     try:p.wait(timeout=2)
     except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
     raise InterruptedError('Server tasks off or job deadline')
    time.sleep(.2)
   if p.returncode:raise RuntimeError('Helper subprocess failed')
  finally:CHILDREN.discard(p.pid)

def extract(archive,destination):
 total=0
 with tarfile.open(archive,'r:gz') as tar:
  members=tar.getmembers()
  if len(members)>20000:raise ValueError('Too many archive entries')
  for member in members:
   path=Path(member.name)
   if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):raise ValueError('Unsafe archive')
   total+=member.size
   if total>128*1024*1024:raise ValueError('Archive too large')
  tar.extractall(destination,members=members,filter='data')

def image(input_file):
 with tempfile.TemporaryDirectory(dir=JOBS,prefix='image-') as directory:
  d=Path(directory);base=d/'image'
  # Cyborg has spare RAM; do not spill large JPEG decoder caches onto the server NAS.
  env=os.environ;env['MAGICK_TEMPORARY_PATH']=directory
  run(['/usr/bin/convert','-limit','thread','4','-limit','memory','512MiB','-limit','map','1GiB','-limit','disk','2GiB','-define','jpeg:size=3600x3600',str(input_file)+'[0]','-auto-orient','-resize','3600x3600>','-background','white','-alpha','remove','-alpha','off',str(base)+'.png'],timeout=120)
  run(['/usr/bin/tesseract',str(base)+'.png',str(base),'-l','eng','--oem','1','--psm','11'],timeout=120)
  return {'text':Path(str(base)+'.txt').read_text()[:100000]}

def prune_sources(keep):
    # Bound the external reusable cache; no scheduled sweep and no primary data.
    files=list(SOURCES.glob('*.pdf'));total=sum(p.stat().st_size for p in files)
    for file in sorted(files,key=lambda p:(p.with_suffix('.used').stat().st_mtime if p.with_suffix('.used').exists() else p.stat().st_mtime)):
        if total<=2*1024**3:break
        if file.stem==keep:continue
        size=file.stat().st_size;file.unlink();file.with_suffix('.used').unlink(missing_ok=True);file.with_suffix('.inventory.json').unlink(missing_ok=True);total-=size
        layer=WORK/'pdf-layers'/hashlib.sha256(('PDF Library/'+file.stem+'.pdf').encode()).hexdigest()
        if layer.is_dir():shutil.rmtree(layer)

def pdf_inventory(body):
 key=body.get('key');total=body.get('pages')
 if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key) or type(total) is not int or not 1<=total<=20000:raise ValueError('Invalid PDF inventory')
 file=SOURCES/(key+'.pdf')
 if not file.is_file():raise FileNotFoundError('Upload source first')
 file.with_suffix('.used').touch()
 from pdf_inventory import scan
 return scan(file,total,run,JOBS)

def pdf_page(body):
 if body.get('inventory') is True:return pdf_inventory(body)
 key=str(body.get('key',''));page=body.get('page');mixed=body.get('mixed')
 if not re.fullmatch('[a-f0-9]{64}',key) or type(page) is not int or not 1<=page<=20000 or type(mixed) is not bool:raise ValueError('Invalid PDF page')
 file=SOURCES/(key+'.pdf')
 if not file.is_file():raise FileNotFoundError('Upload source first')
 file.with_suffix('.used').touch()
 import pdf_local as pdf
 pdf.DATA=WORK;pdf.ROOT=WORK/'pdf-layers';pdf.run=lambda args,timeout:run(args,timeout=timeout)
 # Reuse the maintained OCRmyPDF/Tesseract geometry and native-text preservation.
 path='PDF Library/'+key+'.pdf';fp=pdf.fingerprint(file)
 text=pdf.recognize_page(file,path,fp,page,mixed)
 layer=pdf.prepare(path,fp)/(str(page)+'.pdf')
 if layer.is_file() and layer.stat().st_size>12*1024*1024:raise ValueError('Layer too large')
 return {'text':text,'layer':base64.b64encode(layer.read_bytes()).decode() if layer.is_file() else None}

def embed(body):
 global MODEL
 texts=body.get('texts')
 if not isinstance(texts,list) or not 1<=len(texts)<=64 or any(not isinstance(t,str) or len(t)>20000 for t in texts):raise ValueError('Invalid embedding batch')
 if MODEL is None:
  from model2vec import StaticModel
  MODEL=StaticModel.from_pretrained('/models')
 vectors=MODEL.encode(texts,use_multiprocessing=False).astype('float32')
 return {'vectors':base64.b64encode(vectors.tobytes()).decode(),'count':len(texts),'dimensions':256}

def quartz(archive):
 project=WORK/'quartz'
 if not (project/'quartz/bootstrap-cli.mjs').is_file():
  shutil.copytree('/opt/quartz',project,ignore=shutil.ignore_patterns('node_modules'),dirs_exist_ok=True)
  (project/'node_modules').symlink_to('/opt/quartz/node_modules',target_is_directory=True)
 with tempfile.TemporaryDirectory(dir=JOBS,prefix='quartz-') as directory:
  d=Path(directory);source=d/'source';source.mkdir();extract(archive,source)
  for name in ['quartz.config.yaml','quartz.config.ts','quartz.layout.ts','obsidian-graph.json']:
   if (source/name).is_file():shutil.copy2(source/name,project/name)
  output=d/'public'
  with (d/'build.log').open('w') as log:
   run(['/usr/local/bin/node','--max-old-space-size=6144','quartz/bootstrap-cli.mjs','build','--concurrency=6','--directory',str(source/'content'),'--output',str(output)],cwd=project,timeout=600,log=log)
  if not (output/'index.html').is_file() or 'Failed to instantiate plugin' in (d/'build.log').read_text():raise RuntimeError('Quartz build failed')
  fd,name=tempfile.mkstemp(prefix='site-',suffix='.tar.gz',dir=JOBS);os.close(fd);result=Path(name)
  try:
   with tarfile.open(result,'w:gz',compresslevel=3) as tar:
    for item in output.iterdir():tar.add(item,arcname=item.name,recursive=True)
   return result
  except:result.unlink(missing_ok=True);raise

class Handler(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def reply(self,status,value):
  raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Connection','close');self.end_headers();self.wfile.write(raw);self.close_connection=True
 def auth(self):return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+TOKEN)
 def do_GET(self):
  if self.path=='/health':return self.reply(200,{'ready':True,'accepting':admitted(),'busy':BUSY.locked(),'job':CURRENT['kind'],'vector_epoch':vector_epoch()})
  if not self.auth():return self.reply(401,{'error':'Unauthorized'})
  m=re.fullmatch('/sources/([a-f0-9]{64})',self.path)
  if m:return self.reply(200,{'ready':(SOURCES/(m[1]+'.pdf')).is_file()})
  return self.reply(404,{'error':'Not found'})
 def do_POST(self):
  if not self.auth():return self.reply(401,{'error':'Unauthorized'})
  if not admitted():return self.reply(503,{'error':'Server tasks off','paused':True})
  if not BUSY.acquire(blocking=False):return self.reply(503,{'error':'Helper busy','paused':True})
  input_path=output=None
  try:
   source=re.fullmatch('/sources/([a-f0-9]{64})',self.path)
   if not source and self.path not in ['/jobs/image','/jobs/pdf','/jobs/embed','/jobs/quartz','/jobs/vectors','/jobs/search']:return self.reply(404,{'error':'Not found'})
   size=int(self.headers.get('Content-Length','0'));maximum=2*1024**3 if source else 512*1024*1024 if self.path=='/jobs/vectors' else 32*1024*1024
   if not 0<size<=maximum:raise ValueError('Invalid request size')
   fd,name=tempfile.mkstemp(dir=JOBS,prefix='input-');input_path=Path(name)
   self.connection.settimeout(90);remaining=size
   with os.fdopen(fd,'wb') as out:
    while remaining:
     if not admitted():raise InterruptedError('Server tasks off')
     data=self.rfile.read(min(1024*1024,remaining))
     if not data:raise ValueError('Incomplete upload')
     out.write(data);remaining-=len(data)
   kind='source' if source else self.path.removeprefix('/jobs/');CURRENT.update(kind=kind,started=time.time())
   if source:
    # Only complete, bounded uploads become reusable source files.
    if input_path.open('rb').read(5)!=b'%PDF-':raise ValueError('PDF required')
    os.replace(input_path,SOURCES/(source[1]+'.pdf'));(SOURCES/(source[1]+'.used')).touch();prune_sources(source[1]);return self.reply(200,{'ready':True})
   if kind=='image':value=image(input_path)
   elif kind=='pdf':value=pdf_page(json.loads(input_path.read_text()))
   elif kind=='embed':value=embed(json.loads(input_path.read_text()))
   elif kind=='vectors':value=vectors(input_path)
   elif kind=='search':value=semantic_search(json.loads(input_path.read_text()))
   else:
    output=quartz(input_path);self.send_response(200);self.send_header('Content-Type','application/gzip');self.send_header('Content-Length',str(output.stat().st_size));self.send_header('Connection','close');self.end_headers()
    with output.open('rb') as stream:shutil.copyfileobj(stream,self.wfile,64*1024)
    self.close_connection=True;return
   if not admitted():raise InterruptedError('Server tasks off')
   return self.reply(200,value)
  except (ValueError,TypeError,json.JSONDecodeError):self.reply(400,{'error':'Invalid helper request'})
  except FileNotFoundError:self.reply(404,{'error':'Source unavailable'})
  except InterruptedError:self.reply(503,{'error':'Server tasks off or interrupted','paused':True})
  except Exception as error:
   print(json.dumps({'helper_error':type(error).__name__,'kind':CURRENT['kind']}),flush=True);self.reply(503,{'error':'Helper work interrupted; retry later','paused':True})
  finally:
   if input_path:input_path.unlink(missing_ok=True)
   if output:output.unlink(missing_ok=True)
   CURRENT.update(kind=None,started=None);BUSY.release()

if __name__=='__main__':
 for folder in [JOBS,SOURCES,WORK/'tmp',WORK/'cache',WORK/'home']:folder.mkdir(parents=True,exist_ok=True)
 # Only this container's abandoned job scratch; preserve sources and checkpoints.
 for item in list(JOBS.iterdir())+list(WORK.glob('ocr-*')):
  if item.is_dir() and not item.is_symlink():shutil.rmtree(item)
  else:item.unlink(missing_ok=True)
 def stop(*args):
  STOP.set()
  for pid in list(CHILDREN):
   with contextlib.suppress(ProcessLookupError):os.killpg(pid,signal.SIGTERM)
  raise SystemExit(0)
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 server=http.server.ThreadingHTTPServer(('0.0.0.0',9020),Handler);server.daemon_threads=True;server.serve_forever()
