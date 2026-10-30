"""Durable Workspace jobs and write receipts, on the existing NAS volume."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import secrets

ROOT = Path('/srv/tamestorage/.tamestorage_state/assistant')
WAKE = Path('/srv/tamestorage/.tamestorage_state/transcription/wake')
TERMINAL = {'ready', 'failed', 'canceled', 'review'}


def configure_worker():
    """Bound allocations while allowing V8's large, uncommitted address reservation."""
    import resource
    import subprocess
    os.nice(19)
    if shutil.which('ionice'):
        subprocess.run(['ionice','-c','3','-p',str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    resource.setrlimit(resource.RLIMIT_DATA,(512*1024**2,512*1024**2))
    resource.setrlimit(resource.RLIMIT_CPU,(45,50))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))


class Store:
    def __init__(self, root=None):
        self.root = Path(root or ROOT)
        if not self.root.parent.is_dir():
            raise OSError('Workspace storage is unavailable')
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.root.chmod(0o700)
        with (self.root/'initialize.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.db = sqlite3.connect(self.root/'jobs.sqlite3', timeout=5)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
              provider TEXT NOT NULL, question TEXT NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', result TEXT,
              created REAL NOT NULL, updated REAL NOT NULL, lease REAL NOT NULL DEFAULT 0,
              retry_at REAL NOT NULL DEFAULT 0, cancel INTEGER NOT NULL DEFAULT 0,
              failures INTEGER NOT NULL DEFAULT 0, code TEXT NOT NULL DEFAULT '',
              UNIQUE(owner,request_id));
            CREATE TABLE IF NOT EXISTS operations (
              job TEXT NOT NULL, key TEXT NOT NULL, tool TEXT NOT NULL, arguments TEXT NOT NULL,
              status TEXT NOT NULL, result TEXT, actions TEXT NOT NULL DEFAULT '[]',
              PRIMARY KEY(job,key));
        CREATE TABLE IF NOT EXISTS provider_pause(provider TEXT PRIMARY KEY,until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS file_intents(job TEXT,key TEXT,path TEXT,expected TEXT,target TEXT,PRIMARY KEY(job,key));
            ''')
            (self.root/'jobs.sqlite3').chmod(0o600)
            key=self.root/'worker-key'
            if not key.is_file():
                temporary=self.root/'.worker-key.tmp'
                descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(descriptor,'w') as stream:
                    stream.write(secrets.token_hex(32));stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,key)
    def __enter__(self): return self
    def __exit__(self, *args): self.db.close()

    def wake(self):
        # This lock also fences dispatcher marker removal against new submissions.
        with (self.root/'queue.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            (self.root/'pending').touch()
            try:
                if WAKE.parent.is_dir(): WAKE.touch()
            except OSError:pass  # The job and pending marker are already durable.

    def enqueue(self, owner, request_id, provider, question, payload, images=()):
        with (self.root/'submission.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._enqueue(owner,request_id,provider,question,payload,images)

    def _enqueue(self, owner, request_id, provider, question, payload, images):
        key = hashlib.sha256((owner+'\0'+request_id).encode()).hexdigest()
        existing = self.db.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone()
        if existing:
            if existing['owner'] != owner or existing['question'] != question:
                raise ValueError('This request ID already belongs to another message')
            if existing['status']=='failed' and images and owner=='Admin' and payload.get('include_context'):
                resumed=self.resume_photo_job(existing,payload,images,provider)
                if resumed is not None:return resumed
            # A retry, provider switch or lost HTTP response must never replay writes.
            if payload.get('retry') and existing['status'] in ('failed','canceled') and not self.db.execute('SELECT 1 FROM operations WHERE job=?',(key,)).fetchone():
                with self.db:self.db.execute('DELETE FROM jobs WHERE id=?',(key,))
            else:return self.public(existing)
        self.prune()
        active = self.db.execute("SELECT count(*) FROM jobs WHERE owner=? AND status NOT IN ('ready','failed','canceled','review')", (owner,)).fetchone()[0]
        if active >= 12: raise ValueError('Finish or stop an active Workspace task first')
        count=self.db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        queued_bytes=self.db.execute("SELECT coalesce(sum(json_extract(payload,'$.upload_bytes')),0) FROM jobs").fetchone()[0]
        upload_bytes=sum(len(image) for image in images)
        if count>=64 or queued_bytes+upload_bytes>512*1024**2:raise ValueError('There are too many active Workspace tasks. Finish or stop one first')
        if upload_bytes and shutil.disk_usage(self.root).free<upload_bytes+64*1024**2:raise ValueError('Not enough free storage to process these images')
        folder = self.root/'images'/key
        files = []
        if folder.exists():self.remove_images(key)  # Recover an upload interrupted before its job commit.
        if images:
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            for i, image in enumerate(images):
                target = folder/str(i)
                with target.open('wb') as stream:
                    stream.write(image);stream.flush();os.fsync(stream.fileno())
                target.chmod(0o600)
                files.append(str(target))
            for directory in (folder,folder.parent,self.root):
                descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
        payload = {'tool_protocol':2,**payload, 'images': files,'upload_bytes':upload_bytes,'context_jobs':[v for v in payload.get('context_jobs',[]) if v!=key]}
        now = time.time()
        state = dict(phase='vision' if files else 'plan', image_index=0, observations=[],
                     facts=[], plan_count=0, repairs=0, tool='', actions=[])
        try:
            with self.db:
                self.db.execute('INSERT INTO jobs(id,owner,request_id,provider,question,payload,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                                (key, owner, request_id, provider, question, json.dumps(payload, ensure_ascii=False), json.dumps(state), now, now))
        except sqlite3.IntegrityError:
            existing = self.db.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone()
            if not existing: raise
            return self.public(existing)
        self.wake()
        self.prune()
        return self.get(key, owner)

    def resume_photo_job(self,row,payload,images,provider):
        from lib.workspace_agent import plain_separate_log,ensure_image_outcomes,observation
        if not plain_separate_log(row['question']) or row['code'] not in ('invalid-model-output','needs-input','no-progress','worker-error'):return None
        if not 1<len(images)<=8:return None
        import re
        expected_count=re.search(r'\b(?:these|all)\s+([1-8])\b',row['question'],re.I)
        if expected_count and len(images)!=int(expected_count[1]):raise ValueError('Reattach all original images to resume this request')
        records=self.db.execute('SELECT * FROM operations WHERE job=?',(row['id'],)).fetchall()
        completed=[r for r in records if r['status']=='done']
        if any(r['status']=='started' or r['status']=='done' and r['tool']!='append_diary_entry' for r in records):return None
        previous=json.loads(row['payload']);old_state=json.loads(row['state'])
        if not previous.get('images') and expected_count is None:return None
        # New failures retain their originals. Legacy failures can still resume
        # when the browser reattaches the exact image(s) already saved.
        if previous.get('images') and (len(images)!=len(previous['images']) or any(not Path(p).is_file() or hashlib.sha256(Path(p).read_bytes()).digest()!=hashlib.sha256(raw).digest() for p,raw in zip(previous['images'],images))):
            raise ValueError('Retry with the original attached images; completed entries were preserved')
        from routes.obsidian_images import store_image
        for item in completed:
            args=json.loads(item['arguments']);result=json.loads(item['result'] or '{}');numbers=args.get('image_numbers')
            if not result.get('verified') or not isinstance(numbers,list) or len(numbers)!=1 or not 1<=numbers[0]<=len(images):return None
            expected=result.get('image_urls') or ([result['image_url']] if result.get('image_url') else [])
            if len(expected)!=1 or store_image(images[numbers[0]-1])['url']!=expected[0]:raise ValueError('The previously saved image differs. Reattach the original images to resume safely')
        upload_bytes=sum(len(raw) for raw in images)
        active=self.db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
        account=self.db.execute("SELECT count(*) FROM jobs WHERE owner=? AND status IN ('queued','running')",(row['owner'],)).fetchone()[0]
        retained=self.db.execute("SELECT coalesce(sum(json_extract(payload,'$.upload_bytes')),0) FROM jobs WHERE id!=?",(row['id'],)).fetchone()[0]
        if account>=12 or active>=64 or retained+upload_bytes>512*1024**2:raise ValueError('Finish an active task before resuming this one')
        if shutil.disk_usage(self.root).free<upload_bytes+64*1024**2:raise ValueError('Not enough space to resume these images')
        folder=self.root/'images'/row['id'];folder.mkdir(mode=0o700,parents=True,exist_ok=True);files=[]
        for index,raw in enumerate(images):
            target=folder/str(index)
            with target.open('wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            target.chmod(0o600);files.append(str(target))
        directory=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
        # Preserve the original request time across regeneration and midnight.
        if previous.get('clock'):clock=previous['clock']
        else:
            from datetime import datetime,timedelta
            from zoneinfo import ZoneInfo
            from flask import current_app
            moment=datetime.fromtimestamp(row['created'],ZoneInfo(current_app.config.get('APP_TIMEZONE','Asia/Singapore')))
            clock=dict(today=moment.date().isoformat(),tomorrow=(moment.date()+timedelta(days=1)).isoformat(),local_datetime=moment.isoformat(timespec='seconds'),timezone=str(moment.tzinfo))
        data={**payload,'clock':clock,'tool_protocol':2,'images':files,'upload_bytes':upload_bytes,'context_jobs':[]}
        state=dict(phase='vision',image_index=0,image_count=len(images),question=row['question'],facts=[],observations=[],actions=[],plan_count=0,repairs=0,tool='',separate_images=True,direct_image_outputs=True,outline_checked=True,context_resolved=True)
        for item in completed:
            args=json.loads(item['arguments']);result=json.loads(item['result']);observation(state,item['tool'],args,result)
            for action in json.loads(item['actions']):
                if action.get('action')=='diary.saved':action={**action,'image_numbers':args.get('image_numbers')}
                if action not in state['actions']:state['actions'].append(action)
        # Retained source readings may be reused without another vision call.
        for item in old_state.get('observations',[]):
            if item['tool']=='image_analysis':observation(state,item['tool'],item['arguments'],item['result'])
        ensure_image_outcomes(state)
        with self.db:self.db.execute("UPDATE jobs SET payload=?,state=?,status='queued',result=NULL,cancel=0,failures=0,code='',lease=0,retry_at=0,provider=?,updated=? WHERE id=?",(json.dumps(data,ensure_ascii=False),json.dumps(state,ensure_ascii=False),provider,time.time(),row['id']))
        self.wake();return self.get(row['id'],row['owner'])

    def public(self, row):
        result = json.loads(row['result']) if row['result'] else None
        state=json.loads(row['state'])
        progress=''
        if state.get('actions'):
            from lib.workspace_agent import render_receipts
            progress=render_receipts({**state,'observations':state.get('observations',[])})
        from lib.workspace_agent import task_activity
        activity=task_activity(dict(row),state)
        return dict(activity=activity,id=row['id'], status=row['status'], provider='Cyborg' if row['provider']=='cyborg' else 'Google',
                    code=row['code'], result=result,cancel_requested=bool(row['cancel']),progress_response=progress)

    def get(self, key, owner):
        row = self.db.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (key, owner)).fetchone()
        if not row: raise FileNotFoundError()
        return self.public(row)

    def claim(self):
        now = time.time()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute("SELECT * FROM jobs WHERE status IN ('queued','running') AND lease<? AND retry_at<=? ORDER BY created LIMIT 1", (now, now)).fetchone()
            if not row: return None
            self.db.execute("UPDATE jobs SET status='running',lease=?,updated=?,code='' WHERE id=?", (now+300, now, row['id']))
            return dict(row)

    def activity(self,key,state,label):
        state['activity']=label
        with self.db:self.db.execute('UPDATE jobs SET state=?,updated=? WHERE id=?',(json.dumps(state,ensure_ascii=False),time.time(),key))

    def checkpoint(self, key, state, code='', delay=0, failures=None):
        state.pop('activity',None)
        fields = 'state=?,status=?,lease=0,retry_at=?,updated=?,code=?'
        values = [json.dumps(state, ensure_ascii=False), 'queued' if delay else 'running',time.time()+delay, time.time(), code]
        if failures is not None:
            fields += ',failures=?'; values.append(failures)
        with self.db: self.db.execute('UPDATE jobs SET '+fields+' WHERE id=?', [*values, key])

    def finish(self, key, state, text, status='ready', code='', extra=None):
        row = self.db.execute('SELECT provider,payload FROM jobs WHERE id=?', (key,)).fetchone()
        result = dict(response=text, workspace_result=True, workspace_context=context_receipt(state), provider='Cyborg' if row['provider']=='cyborg' else 'Google',
                      actions=state.get('actions', []), **(extra or {}))
        # Only recoverable model failures retain sources, for an explicit Regenerate.
        payload=json.loads(row['payload'])
        recoverable=status=='failed' and code=='invalid-model-output' and bool(payload.get('images')) and state.get('separate_images',False)
        if recoverable:result['recoverable']=True
        with self.db:
            self.db.execute('UPDATE jobs SET state=?,payload=?,status=?,result=?,lease=0,updated=?,code=? WHERE id=?',
                            (json.dumps(state if recoverable else {'actions':state.get('actions', []),'goals':state.get('goals',[])}), json.dumps(payload) if recoverable else '{}', status, json.dumps(result, ensure_ascii=False), time.time(), code, key))
        if not recoverable:self.remove_images(key)

    def remove_images(self, key):
        if len(key)!=64 or any(c not in '0123456789abcdef' for c in key): raise ValueError('Invalid job ID')
        folder = self.root/'images'/key
        if folder.exists() and not folder.is_symlink(): shutil.rmtree(folder)

    def canceled(self, key):
        row = self.db.execute('SELECT cancel FROM jobs WHERE id=?', (key,)).fetchone()
        return not row or bool(row[0])

    def cancel(self, key, owner):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT * FROM jobs WHERE id=? AND owner=?',(key,owner)).fetchone()
            if not row:raise FileNotFoundError()
            changed = self.db.execute("UPDATE jobs SET cancel=1,retry_at=0 WHERE id=? AND owner=? AND status IN ('queued','running')", (key, owner)).rowcount
        if changed:
            if row['lease']<time.time() and not self.db.execute("SELECT 1 FROM operations WHERE job=? AND status='started'",(key,)).fetchone():
                state=json.loads(row['state'])
                from lib.workspace_agent import render_receipts
                receipts=render_receipts(state)
                self.finish(key,state,'Stopped.'+ ('\n\n'+receipts if receipts else ''),status='canceled')
            else:self.wake()

    def pending(self):
        return self.db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone() is not None

    def sync_marker(self):
        with (self.root/'queue.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.pending(): (self.root/'pending').touch()
            else: (self.root/'pending').unlink(missing_ok=True)

    def prune(self):
        # Bounded runtime receipt retention on submission, not a scheduled cleaner.
        expired=self.db.execute("SELECT id FROM jobs WHERE status='failed' AND updated<? AND json_extract(payload,'$.upload_bytes')>0 LIMIT 16",(time.time()-86400,)).fetchall()
        for row in expired:
            self.remove_images(row[0])
            with self.db:self.db.execute("UPDATE jobs SET payload='{}',state=json_object('actions',json_extract(state,'$.actions'),'goals',json_extract(state,'$.goals')) WHERE id=?",(row[0],))
        rows = self.db.execute("SELECT id FROM jobs WHERE status IN ('ready','failed','canceled','review') AND (updated<? OR id IN (SELECT id FROM jobs WHERE status IN ('ready','failed','canceled','review') ORDER BY updated DESC LIMIT -1 OFFSET 256)) LIMIT 16", (time.time()-7*86400,)).fetchall()
        for row in rows:
            self.remove_images(row[0])
            with self.db:
                self.db.execute('DELETE FROM operations WHERE job=?', (row[0],))
                self.db.execute('DELETE FROM file_intents WHERE job=?',(row[0],))
                self.db.execute('DELETE FROM jobs WHERE id=?', (row[0],))


def context_receipt(state):
    # Small, account-checked references survive a completed turn. Full source bodies
    # and uploads do not. Readers refresh these targets before a later edit.
    references=state.get('facts',[])[-50:]
    return {'references':references,'focus':state.get('focus',{}),
            'goals':[{'text':g['text'],'done':g.get('done',False)} for g in state.get('goals',[])]}
