"""Optional private Cyborg CPU helpers; stdlib only, streamed NAS artifacts."""
import base64,hashlib,http.client,json,math,os,shutil,struct,subprocess,tarfile,tempfile,time,fcntl
from pathlib import Path
from urllib.parse import urlsplit
CONFIG=Path.home()/'.config/cyborg-services/helper.json'
DATA=Path('/srv/tamestorage/.tamestorage_state/lecture-search')
class Paused(RuntimeError):pass

def config():
 try:
  value=json.loads(CONFIG.read_text())
  return value if value.get('enabled') is True else None
 except FileNotFoundError:return None

def enabled():return bool(config())
def ensure():
 c=config()
 if not c:raise Paused('Cyborg helpers not configured')
 key=Path.home()/'.config/cyborg-services/control_ed25519';known=key.with_name('known_hosts')
 try:
  p=subprocess.run(['/usr/bin/ssh','-T','-i',str(key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known),'-o','ConnectTimeout=4','tameszaza@100.66.32.22','ensure'],capture_output=True,text=True,timeout=35)
  if p.returncode or not json.loads(p.stdout).get('ok'):raise ValueError()
 except (OSError,ValueError,subprocess.SubprocessError):raise Paused('Waiting for Cyborg / Server tasks On') from None

def request(route,body=None,file=None,destination=None,timeout=300,method='POST',retry=True,connect_timeout=4):
 c=config()
 if not c:raise Paused('Cyborg helpers not configured')
 url=urlsplit(c['url']);connection=http.client.HTTPConnection(url.hostname,url.port or 80,timeout=connect_timeout)
 payload=json.dumps(body).encode() if file is None and body is not None else None
 headers={'Authorization':'Bearer '+c['token'],'Content-Type':'application/json','Connection':'close'}
 stream=None
 try:
  if file:
   stream=Path(file).open('rb');headers['Content-Type']='application/octet-stream';headers['Content-Length']=str(Path(file).stat().st_size);payload=stream
  connection.connect();connection.sock.settimeout(timeout)
  connection.request(method,route,body=payload,headers=headers);response=connection.getresponse()
  if response.status!=200:
   response.read(2000)
   if response.status==404:raise FileNotFoundError('Cyborg source not cached')
   raise Paused('Cyborg is busy, paused or offline')
  if destination:
   total=0
   with Path(destination).open('wb') as out:
    while part:=response.read(65536):
     total+=len(part)
     if total>128*1024*1024:raise ValueError('Artifact too large')
     out.write(part)
   return
  raw=response.read(32*1024*1024+1)
  if len(raw)>32*1024*1024:raise ValueError('Helper response too large')
  return json.loads(raw)
 except (OSError,http.client.HTTPException) as error:
  if retry:
   ensure();return request(route,body,file,destination,timeout,method,False,connect_timeout)
  raise Paused('Waiting for Cyborg / Server tasks On') from error
 finally:
  if stream:stream.close()
  connection.close()

# One cheap readiness check per batch, never SSH/start Cyborg for fallback work.
_BACKGROUND={'until':0.,'ready':False}
def background_remote():
 if not enabled():return False
 now=time.monotonic()
 if now<_BACKGROUND['until']:return _BACKGROUND['ready']
 try:
  health=request('/health',method='GET',timeout=1,retry=False,connect_timeout=.5)
  ready=health.get('accepting') is True and not health.get('busy')
 except (Paused,ValueError):ready=False
 _BACKGROUND.update(until=now+15,ready=ready)
 return ready

def local_background():
 _BACKGROUND.update(until=time.monotonic()+15,ready=False)

def background_call(remote,local):
 if background_remote():
  try:return remote()
  except Paused:local_background()
 return local_call(local)

def local_call(operation):
 # Reuse one on-demand slot so the small server never builds and OCRs together.
 # Kernel flock releases automatically on exit/interruption; no resident worker.
 with (DATA/'fallback.lock').open('a') as guard:
  try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise Paused('Main server background slot busy; work remains queued') from None
  return operation()


def local_available():
 with (DATA/'fallback.lock').open('a') as guard:
  try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return False
  return True


def image(file,force=False):
 from image_ocr_quality import POLICY,MAX_TEXT
 value=request('/jobs/image',file=file,retry=False)
 # Do not accept old helpers' unfiltered text during a staggered deployment.
 if value.get('policy')!=POLICY:raise Paused('OCR helper update pending')
 text=value.get('rawText' if force else 'text')
 if not isinstance(text,str) or len(text)>MAX_TEXT:raise ValueError('Invalid OCR result')
 return text
def source(file,path,fp):
 key=hashlib.sha256((path+'\0'+fp).encode()).hexdigest()
 if not request('/sources/'+key,method='GET',timeout=15,retry=False)['ready']:
  request('/sources/'+key,file=file,timeout=300,retry=False)
 return key

def pdf_inventory(file,path,fp,total):
 if type(total) is not int or not 1<=total<=20000:raise ValueError('Invalid PDF length')
 key=source(file,path,fp);through=0
 while True:
  try:result=request('/jobs/pdf',{'key':key,'inventory':True,'pages':total},timeout=150,retry=False)
  except FileNotFoundError:raise Paused('Inventory source cache changed; retry') from None
  pages=result.get('pages');current=result.get('through')
  if result.get('total')!=total or type(current) is not int or not through<current<=total or not isinstance(pages,list) or len(pages)>total or any(type(n) is not int or not 1<=n<=current for n in pages) or type(result.get('complete')) is not bool or result['complete']!=(current==total):raise ValueError('Invalid image inventory response')
  if result['complete']:return set(pages)
  through=current

def pdf_page(file,path,fp,number,mixed):
 key=source(file,path,fp)
 return request('/jobs/pdf',{'key':key,'page':number,'mixed':bool(mixed)},timeout=300,retry=False)

class Vector:
 def __init__(self,data):self.data=data
 def astype(self,dtype):
  if dtype!='float32':raise ValueError('Unsupported embedding dtype')
  return self
 def tobytes(self):return self.data
class Encoder:
 def __init__(self,interactive=False):self.interactive=interactive;self.local=None
 def encode(self,texts,**kwargs):
  texts=list(texts)
  def remote():
   result=request('/jobs/embed',{'texts':texts},timeout=5 if self.interactive else 120,retry=False)
   raw=base64.b64decode(result['vectors'],validate=True)
   if result['dimensions']!=256 or result['count']!=len(texts) or len(raw)!=len(texts)*1024:raise ValueError('Invalid embedding response')
   return [Vector(raw[i:i+1024]) for i in range(0,len(raw),1024)]
  def local():
   # Same already-installed 8M/256d model; only background index processes load it.
   if self.local is None:
    from model2vec import StaticModel
    self.local=StaticModel.from_pretrained(str(DATA/'models'))
   return self.local.encode(texts,**kwargs)
  return remote() if self.interactive else background_call(remote,local)

def _sync_vectors(db):
 if not enabled():return
 epoch=db.execute("SELECT value FROM meta WHERE key='vector_epoch'").fetchone()[0]
 health=request('/health',method='GET',timeout=5,retry=False)
 if not health.get('accepting') or health.get('busy'):raise Paused('Cyborg vector mirror waiting')
 if health.get('vector_epoch')!=epoch:
  db.execute('INSERT OR IGNORE INTO vector_pending SELECT path,1 FROM docs');db.commit()
 while row:=db.execute('SELECT path,version FROM vector_pending LIMIT 1').fetchone():
  path,version=row
  with tempfile.NamedTemporaryFile(mode='w',prefix='helper-vectors-',dir=DATA) as stream:
   stream.write(json.dumps({'path':path,'epoch':epoch})+'\n')
   for ident,vector in db.execute('SELECT id,embedding FROM chunks WHERE path=?',(path,)):
    stream.write(json.dumps([ident,base64.b64encode(vector).decode()])+'\n')
   stream.flush();request('/jobs/vectors',file=stream.name,timeout=180,retry=False)
  # A concurrent edit remains dirty and is sent again; never clear newer work.
  db.execute('DELETE FROM vector_pending WHERE path=? AND version=?',(path,version));db.commit()

def sync_vectors(db):
 if not enabled():return
 # Native text/OCR commits must not wait for a derived remote mirror.
 if not db.execute('SELECT 1 FROM vector_pending LIMIT 1').fetchone():return
 try:
  if not background_remote():raise Paused('Mirror offline')
  _sync_vectors(db)
 except Paused:
  local_background();defer('vectors')


def search(db,text,prefix,excluded=()):
 row=db.execute("SELECT value FROM meta WHERE key='vector_epoch'").fetchone()
 if not row:
  defer('index');raise Paused('Vector mirror updating')
 dirty=[r[0] for r in db.execute('SELECT path FROM vector_pending LIMIT 101')]
 if dirty:
  defer('index')
  if len(dirty)>100:raise Paused('Vector mirror updating')
 # Valid synced files remain searchable while another document is being OCR'd.
 excluded=list(dict.fromkeys(dirty+list(excluded)))[:100]
 filename_ids=[r[0] for r in db.execute('SELECT id FROM chunks WHERE page=-1')]
 try:result=request('/jobs/search',{'query':text,'folder':prefix,'epoch':row[0],'filenameIds':filename_ids,'excludePaths':list(excluded)},timeout=5,retry=False)
 except Paused:
  try:
   health=request('/health',method='GET',timeout=2,retry=False)
   if health.get('vector_epoch')!=row[0]:defer('index')
  except Paused:defer('wake')
  raise
 hits=result.get('hits')
 if not isinstance(hits,list) or len(hits)>32:raise ValueError('Invalid search response')
 for hit in hits:
  if type(hit.get('id')) is not int or not isinstance(hit.get('path'),str) or not isinstance(hit.get('hash'),str) or len(hit['hash'])!=64 or not isinstance(hit.get('distance'),(int,float)) or not math.isfinite(hit['distance']):raise ValueError('Invalid search hit')
 return hits

def quartz(source,engine,output,state):
 health=request('/health',method='GET',timeout=5,retry=False)
 if not health.get('accepting') or health.get('busy'):raise Paused('Waiting for Cyborg publishing slot')
 with tempfile.TemporaryDirectory(prefix='helper-build-',dir=state) as directory:
  directory=Path(directory);archive=directory/'source.tar.gz';result=directory/'public.tar.gz'
  with tarfile.open(archive,'w:gz',compresslevel=3) as tar:
   tar.add(source,arcname='content')
   for name in ['quartz.config.yaml','quartz.config.ts','quartz.layout.ts','obsidian-graph.json']:
    p=Path(engine)/name
    if p.is_file():tar.add(p,arcname=name)
  request('/jobs/quartz',file=archive,destination=result,timeout=660,retry=False)
  with tarfile.open(result,'r:gz') as tar:
   total=0;members=tar.getmembers()
   if len(members)>20000:raise ValueError('Too many result files')
   for member in members:
    path=Path(member.name);total+=member.size
    if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()) or total>256*1024*1024:raise ValueError('Invalid build artifact')
   tar.extractall(output,members=members,filter='data')


PENDING=DATA/'helper-pending.json'
WAKE=Path('/srv/tamestorage/.tamestorage_state/transcription/wake')
def defer(kind):
 if kind not in ('index','ocr','quartz','wake','vectors'):raise ValueError('Unknown helper work')
 with (DATA/'helper-pending.lock').open('a') as guard:
  fcntl.flock(guard,fcntl.LOCK_EX)
  try:value=json.loads(PENDING.read_text())
  except FileNotFoundError:value={}
  if value.get(kind):return
  value[kind]=True;temporary=PENDING.with_suffix('.next');temporary.write_text(json.dumps(value));temporary.replace(PENDING)
 WAKE.touch()

def pending():
 try:return bool(json.loads(PENDING.read_text()))
 except FileNotFoundError:return False

def resume_pending():
 if not pending():return False
 # Essential work always wakes locally; each worker prefers a ready helper.
 # Remote-only vector refresh remains pending without blocking OCR/publication.
 ready=background_remote()
 with (DATA/'helper-pending.lock').open('a') as guard:
  fcntl.flock(guard,fcntl.LOCK_EX)
  try:value=json.loads(PENDING.read_text())
  except FileNotFoundError:return False
  # Preserve primary flags while another local task holds the slot. Repeated
  # touch/clear cycles would otherwise relaunch an indexer on every defer wake.
  if not ready and not local_available():return True
  if value.get('index') or value.get('ocr') or (ready and value.get('vectors')):
   (DATA/'pdf-changes').touch()
  if value.get('quartz'):Path('/srv/tamestorage/.tamestorage_state/quartz-site/build-required').touch()
  remaining={k:v for k,v in value.items() if k in ('vectors','wake') and not ready}
  if remaining:
   temporary=PENDING.with_suffix('.next');temporary.write_text(json.dumps(remaining));temporary.replace(PENDING)
  else:PENDING.unlink(missing_ok=True)
 return bool(remaining)
