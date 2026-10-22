"""Disk-backed transcription queue. No model imports or background threads."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import time
import uuid

MAGIC = 'tamestorage-transcription-v1'
MEDIA_EXTENSIONS = {'.wav','.mp3','.m4a','.aac','.flac','.ogg','.opus','.mp4','.mov','.mkv','.webm','.aiff','.wma'}
ARTIFACTS = {'transcript':('Transcript.txt',20*1024**2), 'review':('Review.txt',20*1024**2), 'details':('Processing.json.gz',64*1024**2)}
TERMINAL = {'completed','failed','cancelled'}

class QueueError(Exception):
    def __init__(self, message, status=409): self.message, self.status = message, status

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024**2),b''): h.update(block)
    return h.hexdigest()

def valid_id(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value): raise QueueError('Unknown job.',404)
    return value

class Store:
    def __init__(self, root, inputs, outputs):
        self.root,self.inputs,self.outputs=map(Path,(root,inputs,outputs))
        if not self.root.is_dir() or (self.root/'READY').read_text().strip()!=MAGIC:
            raise QueueError('Transcription storage is unavailable.',503)
        if not self.inputs.is_dir() or not self.outputs.is_dir(): raise QueueError('Recording or result storage is unavailable.',503)

    @classmethod
    def initialize(cls,root,inputs,outputs):
        # Provisioning only. Runtime must never create a substitute NAS mount.
        root,inputs,outputs=map(Path,(root,inputs,outputs))
        for p in (root,inputs,outputs,root/'jobs'): p.mkdir(parents=True,exist_ok=True)
        (root/'READY').write_text(MAGIC+'\n')
        with sqlite3.connect(root/'queue.sqlite') as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
                title TEXT NOT NULL, filename TEXT NOT NULL, size INTEGER NOT NULL,
                sha256 TEXT, options TEXT NOT NULL, state TEXT NOT NULL,
                stage TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                lease TEXT, lease_until REAL, artifacts TEXT NOT NULL DEFAULT '{}', result_dir TEXT);
                CREATE TABLE IF NOT EXISTS worker (id INTEGER PRIMARY KEY CHECK(id=1), seen REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS queue_state ON jobs(state,created);''')
        return cls(root,inputs,outputs)

    def connect(self):
        if not (self.root/'queue.sqlite').is_file(): raise QueueError('Transcription storage is unavailable.',503)
        db=sqlite3.connect(self.root/'queue.sqlite',timeout=10,isolation_level=None)
        db.row_factory=sqlite3.Row; db.execute('PRAGMA busy_timeout=10000'); return db

    @contextmanager
    def transaction(self):
        db=self.connect()
        try:
            db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
        except BaseException: db.rollback(); raise
        finally: db.close()

    def _wake(self,db):
        pending=db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') OR (state='completed' AND stage='Saving synced lyrics') LIMIT 1").fetchone()
        if pending: (self.root/'wake').touch()
        else: (self.root/'wake').unlink(missing_ok=True)
        return bool(pending)

    def pending(self):
        with self.transaction() as db: return self._wake(db)

    def get(self,job_id,db=None):
        valid_id(job_id)
        if db is None:
            with self.connect() as con: return self.get(job_id,con)
        row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if row is None: raise QueueError('Unknown job.',404)
        return dict(row)

    def public(self,row):
        result={k:row[k] for k in ('id','created','updated','title','filename','size','state','stage','progress','message','result_dir')}
        result['artifacts']={k:{'bytes':v['bytes'],'sha256':v['sha256']} for k,v in json.loads(row['artifacts']).items()}
        result['connected']=row['state']=='running' and (row['lease_until'] or 0)>time.time()
        return result

    def listing(self):
        with self.connect() as db:
            return [self.public(dict(r)) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 100')]

    def create(self,filename,size,title,options,*,already_stored=False,internal_options=None):
        filename=str(filename).replace('\\','/').split('/')[-1].replace('\0','')[:180]
        if Path(filename).suffix.lower() not in MEDIA_EXTENSIONS: raise QueueError('Choose an audio or video file.',415)
        if type(size) is not int or not 1<=size<=5*1024**3: raise QueueError('The recording must be between 1 byte and 5 GB.',413)
        if shutil.disk_usage(self.inputs).free<(0 if already_stored else size)+256*1024**2: raise QueueError('There is not enough recording storage.',507)
        title=str(title or Path(filename).stem).strip()[:160] or 'Recording'
        language=options.get('language','en')
        if language not in {'en','auto'}: raise QueueError('Choose English or automatic language detection.',400)
        options={'language':language,'vocabulary':str(options.get('vocabulary',''))[:1500],
                 'llm':bool(options.get('llm',True)),'speakers':bool(options.get('speakers',True))}
        # Set only by the trusted playlist importer, never from HTTP options.
        if internal_options: options.update(internal_options)
        job_id=uuid.uuid4().hex; now=time.time()
        (self.root/'jobs'/job_id).mkdir(mode=0o700); (self.inputs/job_id).mkdir(mode=0o700)
        with self.transaction() as db:
            db.execute('INSERT INTO jobs(id,created,updated,title,filename,size,options,state,stage) VALUES(?,?,?,?,?,?,?,?,?)',
                       (job_id,now,now,title,filename,size,json.dumps(options),'uploading','Waiting for upload'))
        return self.get(job_id)

    def work_dir(self,job_id):
        path=self.root/'jobs'/valid_id(job_id)
        if path.is_symlink() or not path.is_dir(): raise QueueError('Job storage is unavailable.',503)
        return path

    def input_path(self,row):
        folder=self.inputs/valid_id(row['id'])
        if folder.is_symlink() or not folder.is_dir(): raise QueueError('Recording storage is unavailable.',503)
        return folder/('Recording'+Path(row['filename']).suffix.lower())

    def uploaded(self,job_id,sha256):
        with self.transaction() as db:
            row=self.get(job_id,db)
            if row['state']!='uploading': raise QueueError('This upload is no longer active.')
            path=self.input_path(row)
            if path.stat().st_size!=row['size']: raise QueueError('The upload is incomplete.',400)
            db.execute("UPDATE jobs SET state='queued',stage='Queued for Cyborg',sha256=?,updated=? WHERE id=?",(sha256,time.time(),job_id)); self._wake(db)

    def claim(self):
        now=time.time()
        with self.transaction() as db:
            db.execute('INSERT INTO worker(id,seen) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET seen=excluded.seen',(now,))
            if db.execute("SELECT 1 FROM jobs WHERE state='running' AND lease_until>? LIMIT 1",(now,)).fetchone(): return None
            row=db.execute("SELECT id FROM jobs WHERE state='queued' OR (state='running' AND lease_until<=?) ORDER BY created LIMIT 1",(now,)).fetchone()
            if not row: self._wake(db); return None
            token=secrets.token_urlsafe(32)
            db.execute("UPDATE jobs SET state='running',lease=?,lease_until=?,updated=?,stage='Preparing recording',message='' WHERE id=?",(token,now+120,now,row['id']))
            result=self.get(row['id'],db); result['options']=json.loads(result['options']); return result

    def leased(self,job_id,lease,db=None):
        row=self.get(job_id,db)
        if row['state']!='running' or not lease or not secrets.compare_digest(row['lease'] or '',lease): raise QueueError('This job is no longer assigned to this worker.')
        return row

    def progress(self,job_id,lease,stage,progress,message=''):
        try: progress=max(0,min(99,float(progress)))
        except (TypeError,ValueError): raise QueueError('Invalid progress.',400)
        with self.transaction() as db:
            self.leased(job_id,lease,db)
            db.execute('UPDATE jobs SET stage=?,progress=?,message=?,updated=?,lease_until=? WHERE id=?',
                       (str(stage)[:100],progress,str(message)[:240],time.time(),time.time()+120,job_id))
            db.execute('INSERT INTO worker(id,seen) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET seen=excluded.seen',(time.time(),))

    def artifact_saved(self,job_id,lease,name,sha256,size):
        if name not in ARTIFACTS: raise QueueError('Unknown result type.',404)
        with self.transaction() as db:
            row=self.leased(job_id,lease,db); artifacts=json.loads(row['artifacts'])
            artifacts[name]={'sha256':sha256,'bytes':size}
            db.execute('UPDATE jobs SET artifacts=?,updated=?,lease_until=? WHERE id=?',(json.dumps(artifacts),time.time(),time.time()+120,job_id))

    def complete(self,job_id,lease):
        row=self.get(job_id)
        if row['state']=='completed' and secrets.compare_digest(row['lease'] or '',lease or ''): return self.public(row)
        with self.transaction() as db:
            row=self.leased(job_id,lease,db); artifacts=json.loads(row['artifacts'])
            if set(artifacts)!=set(ARTIFACTS): raise QueueError('The result is incomplete.')
            if json.loads(row['options']).get('kind')=='lyrics':
                from lib.lyric_transcription import complete_lyrics
                return complete_lyrics(self,row,artifacts,db)
            if digest(self.work_dir(job_id)/'details.ready')!=artifacts['details']['sha256']: raise QueueError('Result verification failed.')
            title=re.sub(r'[^\w .()-]','_',row['title'])[:70].strip(' .') or 'Recording'
            relative=title+' - '+job_id
            target=self.outputs/relative
            if target.is_symlink(): raise QueueError('Result storage is unavailable.',503)
            target.mkdir(exist_ok=True)
            for name in ('transcript','review'):
                source=self.work_dir(job_id)/(name+'.ready'); expected=artifacts[name]['sha256']
                if digest(source)!=expected: raise QueueError('Result verification failed.')
                dest=target/ARTIFACTS[name][0]
                if dest.exists():
                    if dest.is_symlink() or digest(dest)!=expected: raise QueueError('A different file already occupies the result path.')
                else:
                    temporary=target/('.'+name+'.part')
                    with source.open('rb') as inp,temporary.open('wb') as out:
                        shutil.copyfileobj(inp,out,1024**2); out.flush(); os.fsync(out.fileno())
                    if digest(temporary)!=expected: raise QueueError('Result verification failed.')
                    os.replace(temporary,dest)
            db.execute("UPDATE jobs SET state='completed',stage='Ready to download',progress=100,result_dir=?,updated=? WHERE id=?",(relative,time.time(),job_id)); self._wake(db)
        # Completed inputs follow the existing temp worker's 24-hour retention.
        return self.public(self.get(job_id))

    def pause(self,job_id,lease,reason):
        if reason not in {'Server tasks off','Waiting for GPU recovery'}:raise QueueError('Invalid pause reason.',400)
        with self.transaction() as db:
            self.leased(job_id,lease,db)
            db.execute("UPDATE jobs SET state='queued',stage=?,message='',updated=?,lease=NULL,lease_until=NULL WHERE id=?",(reason,time.time(),job_id))
            self._wake(db)

    def fail(self,job_id,lease,message):
        with self.transaction() as db:
            self.leased(job_id,lease,db)
            db.execute("UPDATE jobs SET state='failed',stage='Needs attention',message=?,updated=?,lease_until=NULL WHERE id=?",(str(message)[:240],time.time(),job_id)); self._wake(db)

    def action(self,job_id,action):
        with self.transaction() as db:
            row=self.get(job_id,db)
            if action=='retry':
                if row['state'] not in {'failed','cancelled'}: raise QueueError('Only stopped jobs can be retried.')
                if not self.input_path(row).is_file() or not row['sha256']: raise QueueError('Upload the recording again; the original is no longer available.')
                db.execute("UPDATE jobs SET state='queued',stage='Queued for Cyborg',progress=0,message='',lease=NULL,lease_until=NULL,updated=? WHERE id=?",(time.time(),job_id))
            elif action=='cancel':
                if row['state'] not in {'queued','running','uploading'}: raise QueueError('This job has already stopped.')
                db.execute("UPDATE jobs SET state='cancelled',stage='Cancelled',lease=NULL,lease_until=NULL,updated=? WHERE id=?",(time.time(),job_id))
            elif action=='remove':
                if row['state'] not in TERMINAL|{'uploading'}: raise QueueError('Cancel the job before removing it.')
                db.execute('DELETE FROM jobs WHERE id=?',(job_id,))
                # Saved results are primary data; removing history never deletes them.
                input_dir=self.inputs/job_id
                if input_dir.is_dir() and not input_dir.is_symlink(): shutil.rmtree(input_dir)
                shutil.rmtree(self.work_dir(job_id))
            else: raise QueueError('Unknown action.',400)
            self._wake(db)
