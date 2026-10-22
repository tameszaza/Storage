"""Persistent Server tasks switch; AI services start on explicit On only."""
import json
import fcntl
import signal
import time
import os
from pathlib import Path
import subprocess
import sys
import tempfile

STATE = Path.home()/'.config/cyborg-server-tasks/state.json'

def accepting(path=None):
    try:
        value=json.loads((path or STATE).read_text())
        return value.get('accepting') is True
    except FileNotFoundError:
        return True  # Preserve pre-install behavior until explicitly changed.
    except (OSError,ValueError,AttributeError):
        return False

def set_accepting(enabled,path=None):
    if type(enabled) is not bool:raise ValueError('Expected On or Off')
    target=path or STATE
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.state-',dir=target.parent)
    try:
        with os.fdopen(fd,'w') as out:
            json.dump({'accepting':enabled},out);out.write('\n');out.flush();os.fsync(out.fileno())
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)

GRAMMAR=['cyborg-grammar.service','cyborg-grammar-model.service']
TRANSCRIPTION='/run/media/tameszaza/New Volume/Cyborg Transcription/'

def processes():
    found=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            if path.stat().st_uid!=os.getuid():continue
            argv=(path/'cmdline').read_bytes().split(b'\0')
            pipeline=any((TRANSCRIPTION+name).encode() in argv for name in ['worker.py','infer.py'])
            runtime=b'/run/media/tameszaza/New Volume/Cyborg Grammar/runtime/'
            job_model=bool(argv and argv[0].startswith(runtime) and b'OLLAMA_HOST=127.0.0.1:11436' in (path/'environ').read_bytes().split(b'\0'))
            if pipeline or job_model:found.append((int(path.name),argv))
        except (OSError,ValueError):pass
    return found

def helper_control(action):
    controller=Path('/usr/local/libexec/cyborg-services')
    if not controller.is_file():return
    result=subprocess.run(['/usr/bin/sudo','-n',str(controller),action],capture_output=True,timeout=8 if action=='pause' else 15)
    if result.returncode:raise RuntimeError('Cyborg helpers could not '+action)

def quiesce():
    helper_error=None
    try:helper_control('pause')
    except (RuntimeError,subprocess.SubprocessError) as error:helper_error=error
    # Block admission first. Stop only known user services and this pipeline's groups.
    result=subprocess.run(['/usr/bin/systemctl','--user','stop',*GRAMMAR],capture_output=True,timeout=13)
    if result.returncode:raise RuntimeError('Could not stop Cyborg AI services')
    owned=processes()
    groups=set()
    for pid,argv in owned:
        try:
            if (TRANSCRIPTION+'infer.py').encode() in argv and os.getpgid(pid)==pid:groups.add(pid)
        except ProcessLookupError:pass
    for group in groups:
        try:os.killpg(group,signal.SIGTERM);os.killpg(group,signal.SIGCONT)
        except ProcessLookupError:pass
    for pid,argv in owned:
        try:os.kill(pid,signal.SIGTERM);os.kill(pid,signal.SIGCONT)
        except ProcessLookupError:pass
    deadline=time.monotonic()+4
    while processes() and time.monotonic()<deadline:time.sleep(.1)
    # A blocked old worker cannot hold GPU work after Off. Exact owned commands only.
    for pid,argv in processes():
        try:
            if (TRANSCRIPTION+'infer.py').encode() in argv and os.getpgid(pid)==pid:os.killpg(pid,signal.SIGKILL)
            else:os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:pass
    if processes():
        time.sleep(.2)
        if processes():raise RuntimeError('Tasks are blocked; a process is still exiting. Refresh shortly.')
    if helper_error:raise RuntimeError('Tasks blocked; helper stop needs a refresh') from helper_error

def change(action):
    STATE.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (STATE.parent/'control.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        set_accepting(action=='on')
        if action=='off':quiesce()
        else:
            helper_error=None
            try:helper_control('resume')
            except (RuntimeError,subprocess.SubprocessError) as error:helper_error=error
            # Explicit On starts AI even after reboot or a previous manual stop.
            # systemd owns cold startup; no resident watcher or boot enablement.
            result=subprocess.run(['/usr/bin/systemctl','--user','start','--no-block',GRAMMAR[0]],capture_output=True,timeout=5)
            if result.returncode:raise RuntimeError('Tasks enabled; AI services could not start')
            (STATE.parent/'resume-grammar').unlink(missing_ok=True)
            if helper_error:raise RuntimeError('AI starting; helpers need a refresh') from helper_error

def main():
    if len(sys.argv)!=2:raise SystemExit('Use on, off, status or desktop-status')
    action=sys.argv[1]
    if action in {'on','off'}:change(action)
    elif action not in {'status','desktop-status'}:raise SystemExit('Unknown operation')
    value={'accepting':accepting()}
    if action=='desktop-status':
        result=subprocess.run(['/usr/local/libexec/cyborg-gpu-guard/control','status'],text=True,capture_output=True,timeout=12)
        if result.returncode:raise SystemExit('GPU Guard status unavailable')
        guard=json.loads(result.stdout)
        value.update(guard=guard['active'],temperature_c=guard.get('temperature_c'))
    print(json.dumps(value))

if __name__=='__main__':main()
