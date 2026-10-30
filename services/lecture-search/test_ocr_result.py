"""Opt-in real user-systemd regression; only disposable units/state."""
from pathlib import Path
import subprocess,uuid,time,tempfile
root=Path(__file__).resolve().parent
content=(root/'lecture-search-ocr-result.conf').read_text()
units=Path.home()/'.config/systemd/user';units.mkdir(parents=True,exist_ok=True)
name='tames-ocr-hook-'+uuid.uuid4().hex[:8];path=units/(name+'.service')
counter=Path('/run/user/1000')/(name+'.runs')
watch=units/(name+'.path')
watch.write_text('[Path]\nPathExists=/run/user/1000/'+name+'-never\nUnit='+name+'.service\n')
def settle():
 for _ in range(100):
  state=subprocess.check_output(['systemctl','--user','show',name+'.service','-p','ActiveState','--value'],text=True).strip()
  if state in ('failed','inactive'):return
  time.sleep(.02)
 raise AssertionError('Disposable service did not settle')
try:
 hook=content.replace('ExecStopPost=+','ExecStopPost=').replace('/usr/bin/systemctl reset-failed lecture-search-ocr.service','/usr/bin/systemctl --user reset-failed '+name+'.service')
 def setup(command):
  path.write_text('[Unit]\nStartLimitIntervalSec=600\nStartLimitBurst=3\n[Service]\nType=oneshot\nSuccessExitStatus=75\nExecStart='+command+'\n'+hook)
  subprocess.run(['systemctl','--user','daemon-reload'],check=True,capture_output=True)
  subprocess.run(['systemctl','--user','reset-failed',name+'.service'],capture_output=True)
 def run():
  p=subprocess.run(['systemctl','--user','start',name+'.service'],capture_output=True,text=True);settle();return p
 subprocess.run(['systemctl','--user','daemon-reload'],check=True,capture_output=True)
 setup('/usr/bin/true')
 subprocess.run(['systemctl','--user','start',name+'.path'],check=True,capture_output=True)
 for command,label in [('/usr/bin/true','successful drains'),('/bin/sh -c "exit 75"','deferred Cyborg standby')]:
  setup(command)
  for i in range(8):
   r=run();assert r.returncode==0,(label,r.stderr)
  print('PASS eight native systemd starts:',label,flush=True)
 setup('/bin/sh -c \"echo run >> '+str(counter)+'; exit 1\"')
 for i in range(3):assert run().returncode!=0
 assert run().returncode!=0
 assert len(counter.read_text().splitlines())==3,'Failure limit did not block fourth execution'
 print('PASS actual failures retain three-start circuit breaker',flush=True)
finally:
 subprocess.run(['systemctl','--user','stop',name+'.path',name+'.service'],capture_output=True)
 subprocess.run(['systemctl','--user','reset-failed',name+'.service'],capture_output=True)
 path.unlink(missing_ok=True);watch.unlink(missing_ok=True);counter.unlink(missing_ok=True)
 subprocess.run(['systemctl','--user','daemon-reload'],capture_output=True)
