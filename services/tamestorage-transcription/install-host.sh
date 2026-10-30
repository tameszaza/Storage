#!/bin/bash
# Run once with sudo after reviewing the maintained source in this directory.
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
source_dir=/home/tameszaza/.local/lib/tamestorage-transcription
[[ $(findmnt -n -o UUID -T /srv/tamestorage) == 4dcef454-5a20-484b-b00e-fe9a136f82b9 ]]
python3 - <<'PY'
from pathlib import Path
import hashlib,os,subprocess,tempfile
p=Path('/usr/local/sbin/tamestorage-temp-cleanup')
old=p.read_text()
new=old
if 'transcription_inbox=' not in old:
    assert hashlib.sha256(old.encode()).hexdigest()=='896c6c0bc9e11bfb2d55f69e71d6ee57ffb41b50bf883c110b0ddcea0c328764','Cleaner changed; review it before installation.'
    new=old.replace('goodnotes_inbox="$temp_dir/Goodnotes-cleanup"','goodnotes_inbox="$temp_dir/Goodnotes-cleanup"\ntranscription_inbox="$temp_dir/Transcription"')
    anchor='! -path "$goodnotes_inbox" ! -path "$goodnotes_inbox/*"'
    assert new.count(anchor)==2
    new=new.replace(anchor,anchor+' ! -path "$transcription_inbox" ! -path "$transcription_inbox/*"')
helper=Path('/srv/docker/tamestorage/deploy/tamestorage-transcription-cleanup.py')
compile(helper.read_text(),str(helper),'exec')
subprocess.run(['install','-o','root','-g','root','-m','0755',str(helper),'/usr/local/libexec/tamestorage-transcription-cleanup.py'],check=True)
call='/usr/bin/python3 /usr/local/libexec/tamestorage-transcription-cleanup.py'
if call not in new:new+='\n# Expire only verified completed recording inputs using the existing schedule.\n'+call+'\n'
if new!=old:
    fd,tmp=tempfile.mkstemp(prefix='.transcription-cleaner-',dir=p.parent)
    try:
        with os.fdopen(fd,'w') as out:out.write(new)
        subprocess.run(['bash','-n',tmp],check=True)
        os.chmod(tmp,0o755);os.chown(tmp,0,0);os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
print('Pending recording inputs protected; completed recordings use existing24h cleanup.')
PY
install -o root -g root -m 0644 "$source_dir/tamestorage-transcription.service" /etc/systemd/system/tamestorage-transcription.service
install -o root -g root -m 0644 "$source_dir/tamestorage-transcription.path" /etc/systemd/system/tamestorage-transcription.path
systemd-analyze verify /etc/systemd/system/tamestorage-transcription.service /etc/systemd/system/tamestorage-transcription.path
systemctl daemon-reload
systemctl enable --now tamestorage-transcription.path
echo 'Transcription queue event trigger installed. No worker runs while the queue is empty.'
