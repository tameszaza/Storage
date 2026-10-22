"""Read-only forced SSH command for server uptime monitoring; no resident agent."""
import json
from pathlib import Path
import subprocess

def unit(name,user=False):
 args=['systemctl']+(['--user'] if user else [])+['show',name,'--property=ActiveState','--property=LoadState','--value']
 p=subprocess.run(args,text=True,capture_output=True,timeout=4)
 values=p.stdout.splitlines()
 return dict(active='active' in values,loaded='loaded' in values,state=next((v for v in values if v in {'active','inactive','failed','activating','deactivating'}),'unknown'))

result={
 'grammar':unit('cyborg-grammar.service',True),
 'models':unit('cyborg-grammar-model.service',True),
 'speaker':unit('cyborg-speaker.service',True),
 'sunshine':unit('app-dev.lizardbyte.app.Sunshine.service',True),
 'gpu-guard':unit('cyborg-gpu-guard.service'),
}
# An intentionally stopped, loaded speaker control remains available on demand.
speaker=result['speaker'];speaker['active']=speaker['loaded'] and speaker['state'] in {'active','inactive'}
p=subprocess.run(['findmnt','-J','-T','/run/media/tameszaza/New Volume','-o','UUID'],text=True,capture_output=True,timeout=3)
try:mounted=json.loads(p.stdout)['filesystems'][0]['uuid'].upper()=='A87A-15B6'
except (ValueError,KeyError,IndexError):mounted=False
result['storage']=dict(active=mounted,loaded=True)
try:
 p=subprocess.run(['/usr/bin/sudo','-n','/usr/local/libexec/cyborg-services','status'],text=True,capture_output=True,timeout=4,check=True)
 service=json.loads(p.stdout);accepting=service.get('accepting') is True
 rows=service.get('containers',[])
 ready=bool(rows and rows[0].get('state')=='running')
 standby=not accepting or bool(rows and rows[0].get('stopped_by_user'))
 result['docker']=dict(active=True,loaded=True,state='ready' if ready else 'standby' if standby else 'unavailable')
 result['helpers']=dict(active=ready or standby,loaded=True,state='running' if ready else 'standby' if standby else 'unavailable')
 digital=next((row for row in rows if row.get('name')=='cyborg-digital'),{})
 digital_ready=digital.get('state')=='running' and digital.get('health')=='healthy'
 digital_standby=(not accepting or digital.get('stopped_by_user')) and digital.get('state') in {'exited','created'}
 result['digital']=dict(active=digital_ready or bool(digital_standby),loaded=bool(digital),state='running' if digital_ready else 'standby' if digital_standby else 'unhealthy')
 result['service-storage']=dict(active=service.get('external') and service.get('storage'),loaded=True)
except (OSError,ValueError,subprocess.SubprocessError):
 for key in ('docker','helpers','digital','service-storage'):result[key]=dict(active=False,loaded=False)
print(json.dumps(result))

