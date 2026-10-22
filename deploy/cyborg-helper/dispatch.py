"""Server-side event dispatcher: no process remains when the queue is empty."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode=True
sys.path.insert(0,'/srv/docker/tamestorage')
sys.path.insert(0,str(Path.home()/'.local/lib/lecture-search'))
import helper_client
from lib.transcription_store import Store
from lib.lyric_transcription import publish_ready

ROOT=Path('/srv/tamestorage/.tamestorage_state/transcription')
CONFIG=Path.home()/'.config/tamestorage-transcription'

def main():
    while True:
        try:
            volume=json.loads(subprocess.check_output(['findmnt','-J','-T',str(ROOT),'-o','UUID'],text=True))['filesystems'][0]
            if volume['uuid']!='4dcef454-5a20-484b-b00e-fe9a136f82b9':return 1
            queue=Store(ROOT,'/srv/tamestorage/uploads/temp/Transcription','/srv/tamestorage/uploads/Admin/Transcripts')
            publish_ready(queue)
            audio_pending=queue.pending()
            helper_pending=helper_client.pending()
            if not audio_pending and not helper_pending:return 0
            if helper_pending:
                try:helper_client.resume_pending()
                except helper_client.Paused:pass
            if not audio_pending:
                if not helper_client.pending():return 0
                time.sleep(60);continue
            command=['ssh','-T','-i',str(CONFIG/'id_ed25519'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes',
                                   '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(CONFIG/'known_hosts'),
                                   '-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',
                                   'tameszaza@100.66.32.22']
            with subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) as child:
                checked=0
                while True:
                    try:code=child.wait(timeout=5);break
                    except subprocess.TimeoutExpired:
                        if time.monotonic()-checked>=60 and helper_client.pending():
                            checked=time.monotonic()
                            try:helper_client.resume_pending()
                            except helper_client.Paused:pass
            if code:time.sleep(int(os.environ.get('TAMES_TRANSCRIPTION_RETRY_SECONDS','60')))
            elif queue.pending():time.sleep(int(os.environ.get('TAMES_TRANSCRIPTION_LEASE_RETRY_SECONDS','15')))  # An earlier worker lease may still be expiring.
        except (OSError,ValueError):return 1

if __name__=='__main__':sys.exit(main())
