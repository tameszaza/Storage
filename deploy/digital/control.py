#!/usr/bin/python3
"""Root-owned, allowlisted Docker lifecycle. No exec/create/delete/pull API."""
import json,os,subprocess,sys,time,secrets,signal
from pathlib import Path
VOLUME=Path('/run/media/tameszaza/New Volume');ROOT=Path('/srv/cyborg-services');UUID='A87A-15B6'
STATE=Path('/home/tameszaza/.config/cyborg-server-tasks/state.json')
POLICY=ROOT/'config/stopped.json';RESUME=ROOT/'config/resume.json'
MANAGED={'helper':'cyborg-helper','digital':'cyborg-digital'}
LABELS={'helper':'Cyborg helpers','digital':'Digital circuit simulator'}
PORTS={'helper':['100.66.32.22:9020'],'digital':['100.66.32.22:9030']}
def run(args,timeout=15,check=True):
 p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
 if check and p.returncode:raise RuntimeError('Docker/storage operation failed')
 return p

def mounted():
 p=run(['/usr/bin/findmnt','-rn','-M',str(VOLUME),'-S','UUID='+UUID],check=False)
 if p.returncode or not ROOT.is_mount():raise RuntimeError('External service storage is unavailable')
 source=run(['/usr/bin/findmnt','-rn','-M',str(ROOT),'-o','FSTYPE']).stdout.strip()
 if source!='ext4':raise RuntimeError('Service storage is not the expected Linux filesystem')

def accepting():
 try:return json.loads(STATE.read_text()).get('accepting') is True
 except (OSError,ValueError,AttributeError):return False

def policy(path):
 try:
  data=json.loads(path.read_text())
  if not isinstance(data,list) or any(v not in MANAGED for v in data):raise ValueError()
  return set(data)
 except FileNotFoundError:return set()

def save(path,value):
 temporary=path.with_suffix('.next');temporary.write_text(json.dumps(sorted(value))+'\n');temporary.chmod(0o600);temporary.replace(path)

def inspect(key):
 if key not in MANAGED:raise ValueError('Unknown service')
 p=run(['/usr/bin/docker','inspect',MANAGED[key]],check=False)
 return json.loads(p.stdout)[0] if p.returncode==0 else None

def detail(key):
 row=inspect(key)
 if row is None:return {'name':'cyborg-'+key,'label':LABELS[key],'host':'Cyborg','state':'unavailable','summary':'Docker or external storage unavailable','stopped_by_user':False,'one_shot':False,'self_control':False}
 state=row.get('State',{});host=row.get('HostConfig',{});status=state.get('Status','unknown')
 return {'name':'cyborg-'+key,'label':LABELS[key],'host':'Cyborg','state':status,'summary':'Paused by Server tasks' if not accepting() else ('Stopped by you' if key in policy(POLICY) else status.title()),'stopped_by_user':key in policy(POLICY),'one_shot':False,'self_control':False,'image':row.get('Config',{}).get('Image',''),'health':state.get('Health',{}).get('Status'),'started':state.get('StartedAt'),'finished':state.get('FinishedAt'),'restarts':row.get('RestartCount',0),'restart_policy':host.get('RestartPolicy',{}).get('Name','no'),'memory_limit':host.get('Memory',0),'cpu_limit':host.get('NanoCpus',0)/1e9,'network':host.get('NetworkMode',''),'ports':PORTS[key],'volumes':[{'path':m.get('Destination',''),'writable':bool(m.get('RW'))} for m in row.get('Mounts',[])],'accepting':accepting(),'controls_blocked':not accepting()}

def ensure_docker():
 if not accepting():raise RuntimeError('Server tasks is off')
 run(['/usr/bin/systemctl','start','docker.service'],timeout=25);mounted()
 if not accepting():raise RuntimeError('Server tasks is off')

def act(action,key):
 if key not in MANAGED or action not in ('start','stop','restart'):raise ValueError('Unsupported service action')
 if action!='stop':ensure_docker()
 else:mounted()
 args=['/usr/bin/docker',action]
 if action in ('stop','restart'):args+=['--time','3']
 run(args+[MANAGED[key]],timeout=12)
 stopped=policy(POLICY)
 if action=='stop':stopped.add(key)
 else:stopped.discard(key)
 save(POLICY,stopped)
 return {'ok':True}

def deploy():
 ensure_docker()
 base=VOLUME/'Cyborg Services/deploy'
 pidfile=ROOT/'config/deploy.pid'
 with subprocess.Popen(['/usr/bin/docker','build','--pull=false','-t','cyborg-helper:1',str(base)],stdout=sys.stderr,stderr=sys.stderr,start_new_session=True) as build:
  pidfile.write_text(str(build.pid));pidfile.chmod(0o600)
  try:
   while build.poll() is None:
    if not accepting():
     os.killpg(build.pid,signal.SIGTERM);raise RuntimeError('Server tasks off')
    time.sleep(.5)
   if build.returncode:raise RuntimeError('Helper image build failed')
  finally:pidfile.unlink(missing_ok=True)
 tokenfile=ROOT/'config/helper.json'
 if not tokenfile.exists():
  tokenfile.write_text(json.dumps({'token':secrets.token_urlsafe(32)}));os.chown(tokenfile,1000,1000);tokenfile.chmod(0o400)
 credentials=Path('/home/tameszaza/.config/cyborg-services/helper.json');credentials.parent.mkdir(mode=0o700,parents=True,exist_ok=True);os.chown(credentials.parent,1000,1000)
 credentials.write_text(json.dumps({'url':'http://100.66.32.22:9020','token':json.loads(tokenfile.read_text())['token']}));credentials.chmod(0o600);os.chown(credentials,1000,1000)
 if (base/'models').is_dir():run(['/usr/bin/rsync','-a','--checksum',str(base/'models')+'/',str(ROOT/'models')+'/'],timeout=120)
 row=inspect('helper')
 if row:run(['/usr/bin/docker','rm','-f','cyborg-helper'],timeout=10)
 run(['/usr/bin/docker','create','--name','cyborg-helper','--restart','no','--user','1000:1000','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true','--pids-limit','512','--memory','8g','--memory-swap','8g','--cpus','8','--publish','100.66.32.22:9020:9020','--mount','type=bind,source='+str(ROOT/'work')+',target=/work','--mount','type=bind,source='+str(ROOT/'models')+',target=/models,readonly','--mount','type=bind,source='+str(tokenfile)+',target=/run/helper.json,readonly','--mount','type=bind,source=/home/tameszaza/.config/cyborg-server-tasks,target=/control,readonly','cyborg-helper:1'],timeout=20)
 if accepting() and 'helper' not in policy(POLICY):run(['/usr/bin/docker','start','cyborg-helper'])
 return {'ok':True,'deployed':'cyborg-helper'}

def main(args):
 if args==['deploy']:return deploy()
 if args==['status']:
  disk=run(['/usr/bin/findmnt','-rn','-M',str(VOLUME),'-S','UUID='+UUID],check=False).returncode==0
  return {'accepting':accepting(),'external':disk,'storage':ROOT.is_mount(),'containers':[detail(k) for k in MANAGED]}
 if len(args)==2 and args[0]=='detail':return detail(args[1])
 if len(args)==2 and args[0] in ('start','stop','restart'):return act(*args)
 if args==['pause']:
  if not ROOT.is_mount():return {'ok':True}
  pidfile=ROOT/'config/deploy.pid'
  if pidfile.exists():
   try:
    pid=int(pidfile.read_text());argv=Path('/proc')/str(pid)/'cmdline'
    if b'cyborg-helper:1' in argv.read_bytes().split(b'\0'):os.killpg(pid,signal.SIGTERM)
   except (OSError,ValueError):pass
  active=set()
  for key,name in MANAGED.items():
   row=inspect(key)
   if row and row.get('State',{}).get('Running'):
    active.add(key);run(['/usr/bin/docker','stop','--time','3',name],timeout=7)
  save(RESUME,active)
  return {'ok':True}
 if args in (['resume'],['ensure']):
  if not accepting():raise RuntimeError('Server tasks is off')
  if args==['resume'] and run(['/usr/bin/systemctl','is-active','--quiet','docker.service'],check=False).returncode:
   run(['/usr/bin/systemctl','start','--no-block','docker.service'],timeout=5)
   return {'ok':True,'warming':True}
  ensure_docker();stopped=policy(POLICY)
  for key,name in MANAGED.items():
   if not accepting():raise RuntimeError('Server tasks is off')
   if key not in stopped and inspect(key):run(['/usr/bin/docker','start',name],timeout=10)
  return {'ok':True}
 raise ValueError('Unsupported control operation')
if __name__=='__main__':
 try:print(json.dumps(main(sys.argv[1:])))
 except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):print(json.dumps({'error':'Service unavailable, paused or invalid operation'}));sys.exit(1)

