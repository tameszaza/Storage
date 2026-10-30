"""One-shot, recoverable intake for direct files in temp/Transcription."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

sys.dont_write_bytecode=True
sys.path.insert(0,'/srv/docker/tamestorage')
from lib.transcription_store import Store,MEDIA_EXTENSIONS,QueueError,valid_id

def fingerprint(path):
    s=os.stat(path,follow_symlinks=False)
    if not stat.S_ISREG(s.st_mode):raise ValueError('Not an ordinary recording.')
    return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def write_json(path,data):
    p=path.with_name(path.name+'.part')
    with p.open('w') as out:json.dump(data,out);out.flush();os.fsync(out.fileno())
    os.replace(p,path)

class Inbox:
    def __init__(self,queue,settle_seconds=5,now=time.time):
        self.queue=queue;self.settle=settle_seconds;self.now=now
        self.marker=queue.root/'audio-wake';self.state_path=queue.root/'inbox-pending.json'

    def finish(self,job_id):
        q=self.queue;folder=q.work_dir(job_id);meta_path=folder/'inbox.json'
        with (folder/'transfer.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return True
            row=q.get(job_id);meta=json.loads(meta_path.read_text())
            if row['state']!='uploading':meta_path.unlink();return False
            path=q.input_path(row)
            if not path.exists():
                source=q.inputs/meta['name']
                if source.parent!=q.inputs or source.is_symlink() or not source.is_file() or fingerprint(source)[:2]!=meta['identity']:
                    # Nothing was moved; preserve any replacement in the inbox.
                    q.action(job_id,'remove');return False
                os.rename(source,path)
                meta.update(stamp=fingerprint(path),since=self.now());write_json(meta_path,meta)
            before=fingerprint(path)
            if before!=meta['stamp']:
                meta.update(stamp=before,since=self.now());write_json(meta_path,meta);return True
            if self.now()-meta['since']<self.settle:return True
            if not 0<before[2]<=5*1024**3:
                # Source stays in this job; never discard a rejected recording.
                with q.transaction() as db:
                    db.execute("UPDATE jobs SET state='failed',stage='Needs attention',message='Recording must be between 1 byte and 5 GB.' WHERE id=?",(job_id,));q._wake(db)
                meta_path.unlink();return False
            h=hashlib.sha256()
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as source:
                for block in iter(lambda:source.read(1024**2),b''):h.update(block)
            after=fingerprint(path)
            if before!=after:
                meta.update(stamp=after,since=self.now());write_json(meta_path,meta);return True
            with q.transaction() as db:
                if q.get(job_id,db)['state']!='uploading':return False
                db.execute('UPDATE jobs SET size=? WHERE id=?',(after[2],job_id))
            q.uploaded(job_id,h.hexdigest());meta_path.unlink();return False

    def once(self):
        q=self.queue;self.marker.unlink(missing_ok=True)
        pending=False
        for folder in (q.root/'jobs').iterdir():
            if folder.is_dir() and not folder.is_symlink() and (folder/'inbox.json').is_file():
                try:valid_id(folder.name);pending=self.finish(folder.name) or pending
                except FileNotFoundError:pass  # Entry removed through Admin while waiting.
        try:observed=json.loads(self.state_path.read_text())
        except FileNotFoundError:observed={}
        current={}
        for source in q.inputs.iterdir():
            if source.name.startswith('.') or source.is_symlink() or not source.is_file() or source.suffix.lower() not in MEDIA_EXTENSIONS:continue
            stamp=fingerprint(source)
            if not 0<stamp[2]<=5*1024**3:continue
            previous=observed.get(source.name)
            if not previous or previous['stamp']!=stamp:
                current[source.name]=dict(stamp=stamp,since=self.now());pending=True;continue
            if self.now()-previous['since']<self.settle:
                current[source.name]=previous;pending=True;continue
            row=q.create(source.name,stamp[2],source.stem,dict(language='en',llm=True,speakers=True),already_stored=True)
            metadata=dict(name=source.name,identity=stamp[:2],stamp=stamp,since=self.now())
            write_json(q.work_dir(row['id'])/'inbox.json',metadata)
            with q.transaction() as db:db.execute("UPDATE jobs SET stage='Checking inbox recording' WHERE id=?",(row['id'],))
            pending=self.finish(row['id']) or pending
        write_json(self.state_path,current)
        if pending:self.marker.touch()
        return pending or self.marker.exists()

def main():
    root=Path('/srv/tamestorage/.tamestorage_state/transcription')
    volume=subprocess.check_output(['findmnt','-rn','-M','/srv/tamestorage','-o','UUID'],text=True).strip()
    if volume!='4dcef454-5a20-484b-b00e-fe9a136f82b9':return 1
    q=Store(root,'/srv/tamestorage/uploads/temp/Transcription','/srv/tamestorage/uploads/Admin/Transcripts')
    with (root/'inbox.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        inbox=Inbox(q)
        while inbox.once():time.sleep(5)
    print('Transcription inbox intake complete.',flush=True)

if __name__=='__main__':sys.exit(main())
