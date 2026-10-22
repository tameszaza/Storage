#!/usr/bin/env python3
"""Block on CouchDB changes. Only export/build after relevant vault changes."""
import base64,json,os,queue,shutil,subprocess,threading,time,urllib.request,urllib.parse
from pathlib import Path
import sys
sys.path.insert(0,str(Path.home()/'.local/lib/lecture-search'))
import helper_client
from types import SimpleNamespace
BASE=Path.home()/'.local/lib/quartz-site'
DATA=Path('/srv/tamestorage/.tamestorage_state/quartz-site')
NODE=Path.home()/'.local/lib/goodnotes-links/node_modules/node/bin/node'
options=json.loads((Path.home()/'.config/goodnotes-links/livesync.json').read_text())
DB='http://127.0.0.1:5984/'+urllib.parse.quote(options['database'],safe='')
AUTH='Basic '+base64.b64encode((options['username']+':'+options['password']).encode()).decode()
events=queue.Queue();latest=None;exported=None;export_info=None

def request(url,body=None):
 return urllib.request.urlopen(urllib.request.Request(url,data=body,headers={'Authorization':AUTH,'Content-Type':'application/json'}),timeout=125)

def interested(doc):
 identifier=doc.get('_id','')
 # Remote paths are encrypted. Upstream exporter decrypts and filters them.
 return doc.get('type') in ('plain','newnote') and not identifier.startswith(('i:','ix:','ps:','h:'))

def feed():
 global latest
 delay=5
 while True:
  try:
   query=urllib.parse.urlencode({'feed':'continuous','heartbeat':60000,'include_docs':'true','filter':'_selector','since':latest})
   with request(DB+'/_changes?'+query,json.dumps({'selector':{'type':{'$ne':'leaf'}}}).encode()) as response:
    for line in response:
     if not line.strip():continue
     item=json.loads(line)
     if 'seq' in item:
      latest=item['seq']
      if interested(item.get('doc',{})) or item.get('deleted'):events.put(latest)
   delay=5
  except Exception:
   print('Change connection interrupted; retrying',flush=True);time.sleep(delay);delay=min(60,delay*2)

def update():
 global exported,export_info
 if not os.path.ismount('/srv/tamestorage'):raise RuntimeError('NAS mount unavailable; preserving published site')
 checkpoint=latest
 if export_info is None or exported!=checkpoint:
  result=subprocess.run([str(NODE),'--max-old-space-size=384',str(BASE/'export.mjs')],capture_output=True,text=True,timeout=240)
  if result.returncode:raise RuntimeError('Vault export failed; preserving published site')
  export_info=json.loads(result.stdout.strip().splitlines()[-1]);exported=checkpoint
 info=export_info
 if info['changed'] or (DATA/'build-required').exists() or not (DATA/'public').exists():
  (DATA/'build-required').touch()
  build=DATA/'public-build'
  if build.exists():shutil.rmtree(build)
  with open(DATA/'last-build.log','w') as log:
   if helper_client.enabled():
    try:helper_client.quartz(DATA/'source',BASE/'quartz',build,DATA)
    except helper_client.Paused:helper_client.defer('quartz');raise
    r=SimpleNamespace(returncode=0);log.write('Cyborg Quartz build completed\n')
   else:
    r=subprocess.run([str(NODE),'--max-old-space-size=512','quartz/bootstrap-cli.mjs','build','--concurrency=1','--directory',str(DATA/'source'),'--output',str(build)],cwd=BASE/'quartz',stdout=log,stderr=subprocess.STDOUT,timeout=600)
  build_log=(DATA/'last-build.log').read_text()
  if r.returncode or not (build/'index.html').is_file() or 'Failed to instantiate plugin' in build_log:raise RuntimeError('Quartz build failed; preserving published site')
  # Keep only current output; atomic symlink replacement avoids partial builds.
  target=DATA/('site-b' if (DATA/'public').is_symlink() and os.readlink(DATA/'public')=='site-a' else 'site-a')
  if target.exists():shutil.rmtree(target)
  build.rename(target)
  for directory,dirs,files in os.walk(target):
   os.chmod(directory,0o755)
   for name in files:os.chmod(Path(directory)/name,0o644)
  temp=DATA/'public.next';temp.unlink(missing_ok=True);temp.symlink_to(target.name);temp.replace(DATA/'public')
  old=DATA/('site-a' if target.name=='site-b' else 'site-b')
  if old.exists():shutil.rmtree(old)
  (DATA/'build-required').unlink(missing_ok=True)
  print('Quartz updated: '+str(info['files'])+' files',flush=True)
 else:print('No published content changes; no build needed',flush=True)
 (DATA/'checkpoint.tmp').write_text(json.dumps(checkpoint));(DATA/'checkpoint.tmp').replace(DATA/'checkpoint.json')

DATA.mkdir(exist_ok=True,mode=0o755)
try:latest=json.loads((DATA/'checkpoint.json').read_text())
except FileNotFoundError:
 with request(DB) as response:latest=json.load(response)['update_seq']
threading.Thread(target=feed,daemon=True).start()
print('Quartz watcher ready; blocks on database changes',flush=True)
pending=True
while True:
 if pending:
  try:update();pending=False
  except Exception as e:
   print(str(e),flush=True);time.sleep(30);continue
 events.get();due=time.monotonic()+20;limit=time.monotonic()+120
 while True:
  try:events.get(timeout=max(0,min(due,limit)-time.monotonic()));due=time.monotonic()+20
  except queue.Empty:break
 pending=True
