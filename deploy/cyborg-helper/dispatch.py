"""Server-side event dispatcher: no process remains when the queue is empty."""
import json
import sqlite3
import urllib.request
import os
from pathlib import Path
import subprocess
import sys
import time
import socket
from urllib.parse import urlsplit

sys.dont_write_bytecode=True
sys.path.insert(0,'/srv/docker/tamestorage')
sys.path.insert(0,str(Path.home()/'.local/lib/lecture-search'))
import helper_client
from lib.transcription_store import Store
from lib.lyric_transcription import publish_ready

ROOT=Path('/srv/tamestorage/.tamestorage_state/transcription')
CONFIG=Path.home()/'.config/tamestorage-transcription'

ARCHIVE=Path('/srv/tamestorage/uploads/Admin/ChatGPT Archive/data/progress.sqlite3')
ARCHIVE_KEY=Path('/srv/tamestorage/.tamestorage_state/chat-archive/collector-key')
def archive_pending():
    state=ARCHIVE_KEY.parent
    return not (state/'maintenance').exists() and (state/'pending').exists()

WORKSPACE=Path('/srv/tamestorage/.tamestorage_state/assistant')
def workspace_pending():
    return (WORKSPACE/'pending').exists() and not (WORKSPACE/'maintenance').exists()

def workspace_step():
    request=urllib.request.Request('http://127.0.0.1:5000/chat/process',data=b'{}',headers={'Content-Type':'application/json','X-Workspace-Key':(WORKSPACE/'worker-key').read_text().strip()},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=290) as response:return bool(json.load(response).get('progress'))
    except (OSError,ValueError):return False

def archive_step():
    request=urllib.request.Request('http://127.0.0.1:5000/admin/chat-archive/process',data=b'{}',headers={'Content-Type':'application/json','X-Export-Key':ARCHIVE_KEY.read_text().strip()},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=290) as response:return bool(json.load(response).get('progress'))
    except (OSError,ValueError):return False

def helper_available():
    """Probe the configured background helper without enabling or submitting work."""
    try:
        config=helper_client.config()
        if not config:return False
        url=urlsplit(config['url'])
        with socket.create_connection((url.hostname,url.port or 80),timeout=1):return True
    except (OSError,ValueError,KeyError):return False


def wake_stamp():
    try:return (ROOT/'wake').stat().st_mtime_ns
    except OSError:return None


def workspace_retry_delay():
    path=WORKSPACE/'jobs.sqlite3'
    if not path.is_file():return None
    try:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=.5) as db:
            due=db.execute("SELECT min(max(retry_at,lease)) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        return max(0,due-time.time()) if due is not None else None
    except (OSError,sqlite3.Error):return None


def wait_for_work(seconds,seen,workspace_ready=False):
    # Keep existing idle backoff, but honor a new wake or a known job retry.
    # One stat/second while already pending adds no daemon or dependency.
    if workspace_ready:
        retry=workspace_retry_delay()
        if retry is not None:seconds=min(seconds,max(1,retry))
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if wake_stamp()!=seen:return
        time.sleep(min(1,until-time.monotonic()))


def main():
    while True:
        try:
            seen_wake=wake_stamp()
            volume=json.loads(subprocess.check_output(['findmnt','-J','-T',str(ROOT),'-o','UUID'],text=True))['filesystems'][0]
            if volume['uuid']!='4dcef454-5a20-484b-b00e-fe9a136f82b9':return 1
            queue=Store(ROOT,'/srv/tamestorage/uploads/temp/Transcription','/srv/tamestorage/uploads/Admin/Transcripts')
            publish_ready(queue)
            audio_pending=queue.pending()
            helper_pending=helper_client.pending()
            chats_pending=archive_pending()
            agent_pending=workspace_pending()
            progress=False
            if not audio_pending and not helper_pending and not chats_pending and not agent_pending:return 0
            core_busy=subprocess.run(['systemctl','is-active','--quiet','lecture-search-index.service','lecture-search-ocr.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0 or Path('/srv/tamestorage/.tamestorage_state/quartz-site/build-required').exists()
            helper_blocking=helper_pending
            if helper_pending and agent_pending and not audio_pending and not core_busy:
                helper_blocking=helper_available()
            if agent_pending and not audio_pending and not helper_blocking and not core_busy:
                progress=workspace_step()
            elif chats_pending and not audio_pending and not helper_pending and not core_busy:
                progress=archive_step()
                if archive_pending():
                    try:helper_client.ensure()
                    except helper_client.Paused:pass
            if helper_pending:
                try:helper_client.resume_pending()
                except helper_client.Paused:pass
            if not audio_pending:
                if not helper_client.pending() and not archive_pending() and not workspace_pending():return 0
                wait_for_work(1 if progress else 60,seen_wake,workspace_ready=agent_pending and not helper_blocking and not core_busy);continue
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
