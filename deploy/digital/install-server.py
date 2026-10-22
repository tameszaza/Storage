#!/usr/bin/python3
"""Install reviewed Digital proxy and remote-control inventory."""
import hashlib, os, pathlib, subprocess
BASE=pathlib.Path(__file__).resolve().parent
assert os.geteuid()==0, 'Run with sudo'
target=pathlib.Path('/usr/local/libexec/tamestorage_containers.py')
expected=(BASE/'broker.sha256').read_text().strip()
new=(BASE/'broker.py').read_text()
assert hashlib.sha256(target.read_text().rstrip().encode()).hexdigest()==expected or target.read_text()==new, 'Broker changed; review before applying'
compile(new,str(target),'exec')
caddy=pathlib.Path('/etc/caddy/Caddyfile')
before=caddy.read_text()
block='''
# Digital runs on Cyborg; this listener is only for Tailscale Serve.
:8097 {
    bind 127.0.0.1
    uri strip_prefix /digital
    reverse_proxy 100.66.32.22:9030 {
        transport http {
            dial_timeout 5s
        }
    }
}
'''
assert ':8097' not in before or 'reverse_proxy 100.66.32.22:9030' in before, 'Proxy port already configured for another service'
candidate=before if ':8097' in before else before.rstrip()+'\n'+block
pending=caddy.with_name('Caddyfile.digital-next')
try:
    pending.write_text(candidate);pending.chmod(0o644)
    subprocess.run(['/usr/bin/caddy','validate','--adapter','caddyfile','--config',str(pending)],check=True)
    temp=target.with_suffix('.next');temp.write_text(new);temp.chmod(0o644);temp.replace(target)
    pending.replace(caddy)
finally:
    pending.unlink(missing_ok=True)
subprocess.run(['systemctl','reload','caddy.service'],check=True)
subprocess.run(['systemctl','restart','tamestorage-host-control.service'],check=True)
subprocess.run(['tailscale','serve','--bg','--yes','--set-path','/digital','http://127.0.0.1:8097'],check=True)
print('Digital proxy and allowlisted service controls installed; existing routes preserved.')
