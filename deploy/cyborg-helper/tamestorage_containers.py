"""Restricted Docker actions and a loopback-only, read-only monitoring adapter."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time
from urllib.parse import quote, urlsplit

ALLOWED={
 'tamestorage':'Tamestorage', 'tamestorage-playlist-worker':'Music sync',
 'tamestorage-calendar-worker':'Calendar sync', 'tamestorage-video-worker':'Video downloads',
 'tamestorage-calendar':'Calendar', 'tamestorage-webdav':'Goodnotes WebDAV',
 'livesync-couchdb':'Obsidian LiveSync','livesync-init':'LiveSync setup',
 'languagetool':'LanguageTool','navidrome':'Navidrome','uptime-kuma':'Uptime Kuma',
 'tamestorage-control-check':'Control verification',
}
NAS={'tamestorage','tamestorage-playlist-worker','tamestorage-calendar-worker',
     'tamestorage-video-worker','tamestorage-calendar','tamestorage-webdav','livesync-couchdb'}
REMOTE={'cyborg-helper':'Cyborg helpers','cyborg-digital':'Digital circuit simulator'}
REMOTE_IDENTITY=Path('/home/tameszaza/.config/cyborg-services/control_ed25519')
POLICY=Path('/var/lib/tamestorage-control/stopped.json')
LOCK=threading.RLock()
JOBS={}
EXECUTOR=ThreadPoolExecutor(max_workers=2,thread_name_prefix='container-action')

class ControlError(ValueError): pass
class UnixHTTP(http.client.HTTPConnection):
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect('/var/run/docker.sock')

def docker(method,path,timeout=5):
 connection=UnixHTTP('localhost',timeout=timeout)
 try:
  connection.request(method,path)
  response=connection.getresponse();raw=response.read(4*1024*1024+1)
  if len(raw)>4*1024*1024: raise ControlError('Docker response is too large.')
  if response.status>=400: raise ControlError('Docker could not complete the request.' if response.status!=404 else 'Container no longer exists.')
  return json.loads(raw) if raw else {}
 finally: connection.close()

def valid(name):
 if name not in ALLOWED and name not in REMOTE: raise ControlError('Unknown managed container.')
 return quote(name,safe='')

def stopped():
 try:
  value=json.loads(POLICY.read_text())
  if not isinstance(value,list) or any(not isinstance(n,str) for n in value):raise ValueError()
  return set(value)
 except FileNotFoundError:return set()
 except (OSError,ValueError):raise ControlError('Saved stop preferences are unavailable.')

def write_stopped(names):
 POLICY.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 temporary=POLICY.with_suffix('.next')
 try:
  with temporary.open('w') as f:
   os.chmod(temporary,0o600);json.dump(sorted(names),f);f.flush();os.fsync(f.fileno())
  os.replace(temporary,POLICY)
 finally:temporary.unlink(missing_ok=True)

def mounts_ready(name):
 if name not in NAS:return
 import subprocess
 result=subprocess.run(['/usr/bin/findmnt','-J','-T','/srv/tamestorage','-o','UUID'],capture_output=True,text=True,timeout=4,check=True)
 if json.loads(result.stdout)['filesystems'][0]['uuid']!='4dcef454-5a20-484b-b00e-fe9a136f82b9':raise ControlError('Primary NAS is offline. Reconnect it before starting this service.')
 if not Path('/srv/tamestorage/uploads').is_dir():raise ControlError('Primary NAS is not ready.')

def inspect(name):
 if name in REMOTE:
  row=remote_detail(name);return {'Id':name,'State':{'Status':row['state'],'Running':row['state']=='running'},'remote':row}
 return docker('GET','/containers/'+valid(name)+'/json')

def listing():
 paused=stopped();result=[]
 for row in docker('GET','/containers/json?all=1'):
  name=next((n.lstrip('/') for n in row.get('Names',[]) if n.lstrip('/') in ALLOWED),None)
  if not name:continue
  status=row.get('State','unknown');description=str(row.get('Status',''))[:160]
  health='unhealthy' if '(unhealthy)' in description else 'healthy' if '(healthy)' in description else None
  with LOCK:active=next((k for k,v in JOBS.items() if (v['name']==name or name in v.get('targets',[])) and v['state'] in {'queued','running'}),None)
  result.append(dict(name=name,label=ALLOWED[name],image=row.get('Image',''),state=status,
                     health=health,summary=description,stopped_by_user=name in paused,
                     operation=active,one_shot=name=='livesync-init',self_control=name=='tamestorage'))
 for row in result:row['host']='Server'
 if REMOTE_IDENTITY.is_file():
  remote=remote_snapshot()
  rows=remote.get('containers') or []
  result.extend(rows or [dict(name=name,label=label,host='Cyborg',state='offline',summary='Waiting for Cyborg',stopped_by_user=False,controls_blocked=True,one_shot=False,self_control=False) for name,label in REMOTE.items()])
 return sorted(result,key=lambda row:(row.get('host')=='Cyborg',row['one_shot'],row['label'].lower()))

def detail(name):
 row=inspect(name)
 if name in REMOTE:return row['remote']
 state=row.get('State',{});host=row.get('HostConfig',{});ports=[]
 for private,bindings in (row.get('NetworkSettings',{}).get('Ports') or {}).items():
  for binding in bindings or []:ports.append(binding.get('HostIp','')+':'+binding.get('HostPort','')+' → '+private)
 return dict(name=name,label=ALLOWED[name],image=row.get('Config',{}).get('Image',''),
             started=state.get('StartedAt'),finished=state.get('FinishedAt'),state=state.get('Status'),
             health=state.get('Health',{}).get('Status'),exit_code=state.get('ExitCode'),
             restarts=row.get('RestartCount',0),restart_policy=host.get('RestartPolicy',{}).get('Name','no'),
             memory_limit=host.get('Memory',0),cpu_limit=host.get('NanoCpus',0)/1e9,
             network=host.get('NetworkMode',''),ports=ports,
             volumes=[dict(path=m.get('Destination',''),writable=bool(m.get('RW'))) for m in row.get('Mounts',[])])

def operation(ident):
 with LOCK:
  result=JOBS.get(ident)
  if not result:raise ControlError('Operation expired. Refresh service status.')
  return {k:v for k,v in result.items() if k!='at'}

def _run(ident):
 time.sleep(.7) # Return the HTTP acknowledgement before a self restart/stop.
 with LOCK:job=JOBS[ident];job['state']='running'
 try:
  name,action=job['name'],job['action']
  if action in {'start','restart'}:mounts_ready(name)
  if name in REMOTE:
   remote_control(action+' '+name.removeprefix('cyborg-'),timeout=25);refresh_remote();job['state']='done';return
  suffix='?t=10' if action in {'stop','restart'} else ''
  docker('POST','/containers/'+valid(name)+'/'+action+suffix,timeout=25)
  with LOCK:
   names=stopped()
   if action=='stop':names.add(name)
   else:names.discard(name)
   write_stopped(names)
   job['state']='done'
 except Exception as error:
  with LOCK:job['state']='error';job['error']=str(error) if isinstance(error,ControlError) else 'Docker is unavailable. Refresh and try again.'

def action(name,verb):
 valid(name)
 if verb not in {'start','stop','restart'} or name=='livesync-init':raise ControlError('Unsupported container action.')
 if verb in {'start','restart'}:mounts_ready(name)
 row=inspect(name);state=row.get('State',{}).get('Status')
 if name in REMOTE and verb!='stop' and row.get('remote',{}).get('controls_blocked'):raise ControlError('Turn on Server tasks in NTU VPN first.')
 if (verb=='start' and state=='running') or (verb=='stop' and state in {'exited','created'}):raise ControlError('Container is already in that state. Refresh the list.')
 with LOCK:
  now=time.monotonic()
  for ident in list(JOBS):
   if now-JOBS[ident]['at']>600 and JOBS[ident]['state'] not in {'queued','running'}:del JOBS[ident]
  if any((v['name']==name or name in v.get('targets',[])) and v['state'] in {'queued','running'} for v in JOBS.values()):raise ControlError('A change is already in progress.')
  if sum(v['state'] in {'queued','running'} for v in JOBS.values())>=4:raise ControlError('Other services are updating. Try again shortly.')
  ident=secrets.token_hex(12);JOBS[ident]=dict(name=name,action=verb,state='queued',at=now)
  EXECUTOR.submit(_run,ident)
 return dict(operation=ident)

def dispatch(payload):
 op=payload.get('action');name=payload.get('name')
 if op=='containers-list':return dict(containers=listing())
 if op=='container-detail':return dict(container=detail(name))
 if op=='container-action':return action(name,payload.get('verb'))
 if op=='container-operation':return dict(operation=operation(payload.get('id')))
 raise ControlError('Unsupported request.')

SYSTEM_UNITS={
 'goodnotes-sync':'goodnotes-obsidian-links.service',
 'web-proxy':'caddy.service','nas-share':'smbd.service',
 'vault-publisher':'quartz-site.service','disk-analysis':'tamestorage-disk-analysis.service',
 'nas-recovery':'tamestorage-primary-recover.timer','scheduled-backup':'tamestorage-backup.timer',
 'transcription':'tamestorage-transcription.path','recording-inbox':'tamestorage-transcription-inbox.path',
 'semantic-search':'lecture-search.socket','lecture-index':'lecture-search-index.path',
 'ocr-index':'lecture-search-ocr.path','grammar-gateway':'cyborg-grammar-gateway.socket',
}
REMOTE_KEYS={'grammar','models','speaker','sunshine','gpu-guard','storage','docker','helpers','digital','service-storage'}
REMOTE_LOCK=threading.Lock()
REMOTE_CACHE={'at':0,'data':{}}

CONTROL_CACHE={'at':0,'data':{},'refreshing':False}
CONTROL_LOCK=threading.Lock()
def remote_control(command,timeout=8):
 import subprocess
 allowed={'status','ensure'} | {verb+' '+key for verb in ('detail','start','stop','restart') for key in ('helper','digital')}
 if command not in allowed:raise ControlError('Unknown remote operation.')
 argv=['/usr/sbin/runuser','-u','tameszaza','--','/usr/bin/ssh','-T','-i',str(REMOTE_IDENTITY),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(REMOTE_IDENTITY.with_name('known_hosts')),'-o','ConnectTimeout=3','tameszaza@100.66.32.22',command]
 try:
  p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout,check=True);data=json.loads(p.stdout)
  if not isinstance(data,dict) or data.get('error'):raise ValueError()
  return data
 except (OSError,ValueError,subprocess.SubprocessError):raise ControlError('Cyborg is unavailable or Server tasks is off.') from None

def refresh_remote():
 try:data=remote_control('status',timeout=7)
 except ControlError:data={}
 with CONTROL_LOCK:CONTROL_CACHE.update(at=time.monotonic(),data=data,refreshing=False)

def remote_snapshot():
 # A sleeping/offline Cyborg cannot block local service listing or its controls.
 with CONTROL_LOCK:
  if (not CONTROL_CACHE['at'] or time.monotonic()-CONTROL_CACHE['at']>15) and not CONTROL_CACHE['refreshing']:
   CONTROL_CACHE['refreshing']=True;EXECUTOR.submit(refresh_remote)
  return CONTROL_CACHE['data']

def remote_detail(name):
 if name not in REMOTE:raise ControlError('Unknown remote service.')
 data=remote_control('detail '+name.removeprefix('cyborg-'),timeout=5)
 if data.get('state')=='unavailable':raise ControlError('Cyborg Docker or its external drive is unavailable.')
 return data

def system_health(key):
 import subprocess
 if key not in SYSTEM_UNITS:raise ControlError('Unknown service.')
 p=subprocess.run(['/usr/bin/systemctl','is-active',SYSTEM_UNITS[key]],capture_output=True,text=True,timeout=4)
 return {'active':p.returncode==0,'state':p.stdout.strip()[:30]}

def cyborg_health(key):
 import subprocess
 if key not in REMOTE_KEYS:raise ControlError('Unknown remote service.')
 with REMOTE_LOCK:
  now=time.monotonic()
  if now-REMOTE_CACHE['at']>30:
   command=['/usr/sbin/runuser','-u','tameszaza','--','/usr/bin/ssh','-T',
     '-i','/home/tameszaza/.config/uptime-kuma/cyborg_monitor_ed25519','-o','IdentitiesOnly=yes',
     '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
     '-o','UserKnownHostsFile=/home/tameszaza/.config/uptime-kuma/known_hosts',
     '-o','ConnectTimeout=4','tameszaza@100.66.32.22']
   try:
    p=subprocess.run(command,capture_output=True,text=True,timeout=8,check=True)
    data=json.loads(p.stdout)
    if not isinstance(data,dict):raise ValueError()
   except (OSError,ValueError,subprocess.SubprocessError):data={}
   REMOTE_CACHE.update(at=now,data=data)
  result=REMOTE_CACHE['data'].get(key,{})
 return {'active':bool(result.get('active')),'state':'active' if result.get('active') else 'unavailable'}

class ReadOnlyDocker(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,status,data):
  raw=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers()
  if self.command!='HEAD':self.wfile.write(raw)
 def do_GET(self):
  path=re.sub(r'^/v[0-9.]+(?=/)','',urlsplit(self.path).path)
  try:
   if path=='/_ping':return self.reply(200,{'ok':True})
   if path=='/version':return self.reply(200,{'Version':'restricted-monitor','ApiVersion':'1.45','MinAPIVersion':'1.24','Os':'linux','Arch':'amd64'})
   if path=='/containers/json':return self.reply(200,[{'Names':['/'+r['name']],'Id':r['name'],'ImageID':'managed','State':r['state']} for r in listing()])
   health=re.fullmatch(r'/(system|cyborg)/([a-z-]+)',path)
   if health:
    data=system_health(health[2]) if health[1]=='system' else cyborg_health(health[2])
    return self.reply(200 if data['active'] else 503,data)
   match=re.fullmatch(r'/containers/([A-Za-z0-9_.-]+)/json',path)
   if not match:return self.reply(404,{'error':'Not available'})
   name=match[1];row=inspect(name);state=row.get('State',{})
   safe={k:state.get(k) for k in ('Running','Status','StartedAt','FinishedAt','ExitCode','OOMKilled','Paused','Restarting')}
   if 'Health' in state:safe['Health']={'Status':state['Health'].get('Status')}
   return self.reply(200,{'Id':row.get('Id'),'Name':'/'+name,'State':safe})
  except (ControlError,OSError,ValueError):return self.reply(503,{'error':'Container unavailable'})
 def do_HEAD(self):self.do_GET()
 def deny(self):self.reply(405,{'error':'Read only'})
 do_POST=do_PUT=do_DELETE=do_PATCH=deny

def start_monitor_adapter():
 server=ThreadingHTTPServer(('127.0.0.1',9780),ReadOnlyDocker)
 threading.Thread(target=server.serve_forever,daemon=True,name='docker-monitor').start()
 return server

