"""Read-only, demand-driven activity snapshot for the existing host broker.

No task admission/start, model calls, schema initialization, tree scans or log tailer.
Only fixed status sources are read; arbitrary paths/commands are never accepted.
"""
from contextlib import closing
from datetime import datetime
import base64
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time

STATE = Path('/srv/tamestorage/.tamestorage_state')
HOME = Path('/home/tameszaza')
UNITS = {
 'ocr':'lecture-search-ocr.service', 'index':'lecture-search-index.service',
 'publish':'quartz-site.service', 'goodnotes':'goodnotes-obsidian-links.service',
 'backup':'tamestorage-backup.service', 'dispatch':'tamestorage-transcription.service',
 'recovery':'tamestorage-primary-recover.service',
}
CACHE = {'at':0, 'data':None}
LOCK = threading.Lock()


def text(value, limit=240):
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or ''))
    value = re.sub(r'(?i)(?:https?://|bearer\s+)\S+', '[redacted]', value)
    value = re.sub(r'(?i)\b(password|passwd|token|secret|api[_-]?key|authorization)\s*[=:]\s*\S+', r'\1=[redacted]', value)
    return value[:limit]


def timestamp(value):
    try:
        if isinstance(value,(int,float)):return float(value) or None
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except (TypeError, ValueError):return None


def small_json(path, limit=1024*1024):
    with Path(path).open('rb') as handle: raw=handle.read(limit+1)
    if len(raw)>limit:raise ValueError('Status source exceeds limit')
    return json.loads(raw)


def database(path):
    db=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True,timeout=.15)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('PRAGMA busy_timeout=150')
    deadline=time.monotonic()+.35
    db.set_progress_handler(lambda: 1 if time.monotonic()>deadline else 0,1000)
    return db


def http_json(host, port, path, headers=None, timeout=1.2):
    connection=http.client.HTTPConnection(host,port,timeout=timeout)
    try:
        connection.request('GET',path,headers=headers or {})
        response=connection.getresponse();raw=response.read(256*1024+1)
        if response.status!=200 or len(raw)>256*1024:raise OSError('Status unavailable')
        return json.loads(raw)
    finally:connection.close()


def unit_status():
    args=['/usr/bin/systemctl','show',*UNITS.values(),
          '--property=Id,ActiveState,SubState,Result,MainPID,ExecMainStartTimestamp,ExecMainExitTimestamp,ActiveExitTimestamp']
    output=subprocess.run(args,capture_output=True,text=True,timeout=2,check=True).stdout
    result={}
    for block in output.split('\n\n'):
        item=dict(line.split('=',1) for line in block.splitlines() if '=' in line)
        if item.get('Id'):result[item['Id']]=item
    return {key:result.get(name,{}) for key,name in UNITS.items()}


def processes():
    output=subprocess.run(['/bin/ps','-eo','pid=,ppid=,etimes=,args='],capture_output=True,text=True,timeout=1.5,check=True).stdout
    rows=[]
    for line in output.splitlines()[:4096]:
        parts=line.split(None,3)
        if len(parts)==4 and all(p.isdigit() for p in parts[:3]):rows.append((int(parts[0]),int(parts[1]),int(parts[2]),parts[3][:2048]))
    return rows


def children(rows, pid):
    found=[];parents={int(pid or 0)}-{0}
    for _ in range(8):
        next_ids={row[0] for row in rows if row[1] in parents}
        if not next_ids:break
        found.extend(row[3] for row in rows if row[0] in next_ids)
        parents=next_ids
    return found


def task(key,label,host='Server',state='idle',stage='Waiting for work',**fields):
    return dict(id=key,label=label,host=host,state=state,stage=text(stage),**fields)


class Collector:
    def __init__(self):
        self.now=time.time();self.tasks=[];self.events=[];self.unavailable=[]
        self.units={};self.procs=[];self.helper={};self.remote={}

    def read(self,name,fn,default=None):
        try:return fn()
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,http.client.HTTPException,subprocess.SubprocessError):
            self.unavailable.append(name);return default

    def unit_task(self,key,label,watcher=False):
        info=self.units.get(key,{})
        if not info:return task(key,label,state='unknown',stage='Status unavailable')
        active=info.get('ActiveState')
        state='failed' if active=='failed' or info.get('Result') not in (None,'success','') else 'running' if active in ('active','activating','deactivating') and not watcher else 'idle'
        return task(key,label,state=state,stage='Running' if state=='running' else 'Needs attention' if state=='failed' else 'Watching for changes' if watcher and active=='active' else 'Waiting for work',
                    started=timestamp(info.get('ExecMainStartTimestamp')) if state=='running' else None,
                    updated=timestamp(info.get('ExecMainExitTimestamp')),details=[['Service',UNITS[key]]])

    def base(self):
        self.units=self.read('System jobs',unit_status,{})
        self.procs=self.read('Process stages',processes,[])
        self.helper=self.read('Cyborg helpers',lambda:http_json('100.66.32.22',9020,'/health',timeout=.8),{})
        def remote():
            import tamestorage_containers
            return tamestorage_containers.remote_snapshot()
        self.remote=self.read('Cyborg control',remote,{})

    def search(self):
        def collect():
            with closing(database(STATE/'lecture-search/index.sqlite')) as db:
                queue=[dict(r) for r in db.execute("SELECT j.path,d.kind,d.page_count FROM ocr_jobs j LEFT JOIN docs d ON d.path=j.path ORDER BY (j.path LIKE 'PDF Library/Books/%'),j.path LIMIT 20")]
                count=db.execute('SELECT count(*) FROM ocr_jobs').fetchone()[0]
                vectors=db.execute('SELECT count(*) FROM vector_pending').fetchone()[0]
                pages=None
                if queue and queue[0].get('kind')=='lecture PDF':
                    pages=dict(db.execute("SELECT min(CASE WHEN method='native-image' OR (method='native' AND trim(text)='') OR indexed=0 THEN page END) AS next_page, sum(CASE WHEN method='native-image' OR (method='native' AND trim(text)='') OR indexed=0 THEN 1 ELSE 0 END) AS remaining, sum(CASE WHEN method LIKE 'ocr%' OR method LIKE 'native+ocr%' THEN 1 ELSE 0 END) AS done FROM pages WHERE path=?",(queue[0]['path'],)).fetchone())
                return queue,count,vectors,pages
        data=self.read('OCR / index queue',collect)
        ocr=self.unit_task('ocr','PDF & image OCR')
        index=self.unit_task('index','Search indexing')
        if data:
            queue,count,vectors,pages=data;ocr['queued']=count;index['queued']=vectors
            if count:
                if ocr['state'] not in ('failed','running'):ocr.update(state='queued',stage='Waiting for OCR worker')
                ocr['title']=text(queue[0]['path']);ocr['items']=[text(r['path']) for r in queue[:8]]
                if pages:
                    done=int(pages['done'] or 0);remaining=int(pages['remaining'] or 0)
                    ocr.update(done=done,total=done+remaining)
                    ocr['details'].append(['Next page',str(pages['next_page'] or 'Assembling PDF')])
                    ocr['details'].append(['PDF pages',str(queue[0].get('page_count') or 'Unknown')])
                ocr['stage']='Coordinating page recognition / indexing' if ocr['state']=='running' else ocr['stage']
            elif ocr['state']=='running':ocr['stage']='Finishing searchable PDFs'
            if vectors and index['state']=='idle':index.update(state='queued',stage='Vector mirror updates pending')
        else:
            ocr.update(state='unknown',stage='Queue unavailable');index['stage']='Queue unavailable'
        self.tasks.extend([ocr,index])

    def publisher(self):
        row=self.unit_task('publish','Publish notes',watcher=True)
        cmds=children(self.procs,self.units.get('publish',{}).get('MainPID'))
        if any('export.mjs' in c for c in cmds):row.update(state='running',stage='Exporting vault notes')
        elif any('quartz/bootstrap-cli.mjs' in c for c in cmds):row.update(state='running',stage='Building website')
        if self.helper.get('busy') and self.helper.get('job')=='quartz':row.update(host='Cyborg',state='running',stage='Building website')
        elif (STATE/'quartz-site/build-required').exists() and row['state']=='idle':row.update(state='queued',stage='Website rebuild pending')
        checkpoint=STATE/'quartz-site/checkpoint.json'
        if checkpoint.exists():row['updated']=checkpoint.stat().st_mtime
        self.tasks.append(row)

    def goodnotes(self):
        row=self.unit_task('goodnotes','Goodnotes → Obsidian',watcher=True)
        cmds=children(self.procs,self.units.get('goodnotes',{}).get('MainPID'))
        if any('publish.mjs' in c for c in cmds):row.update(state='running',stage='Publishing PDF links')
        if any('cleanup-links.mjs' in c for c in cmds):row.update(state='running',stage='Reconciling library links')
        self.tasks.append(row)
        cleanup=task('goodnotes-cleanup','Goodnotes library cleanup')
        ledger=self.read('Goodnotes cleanup journal',lambda:small_json(STATE/'goodnotes-obsidian-links/cleanup-state.json'))
        if ledger is not None:
            jobs=ledger.get('jobs',[])
            if jobs:cleanup.update(state='running' if row['state']=='running' else 'queued',stage='Reconciling validated backup',queued=len(jobs),details=[['Planned PDF removals',str(sum(len(v.get('deleted',[])) for v in jobs))]])
        else:cleanup.update(state='unknown',stage='Cleanup journal unavailable')
        latest=self.read('Last Goodnotes cleanup',lambda:small_json(STATE/'goodnotes-obsidian-links/cleanup-last.json'))
        if latest:
            cleanup['updated']=(STATE/'goodnotes-obsidian-links/cleanup-last.json').stat().st_mtime
            cleanup.setdefault('details',[]).extend([['Last backup notebooks',str(latest.get('notebooks','—'))],['Last removed PDFs',str(latest.get('removed_pdfs',0))],['Protected / unconfirmed',str(latest.get('protected_or_unknown',0))]])
        self.tasks.append(cleanup)

    def couch(self):
        def collect():
            config=small_json(HOME/'.config/goodnotes-links/livesync.json')
            authorization=base64.b64encode((config['username']+':'+config['password']).encode()).decode()
            return http_json('127.0.0.1',5984,'/_active_tasks',{'Authorization':'Basic '+authorization})
        rows=self.read('LiveSync database maintenance',collect)
        if rows is None:self.tasks.append(task('database','LiveSync database',state='unknown',stage='Maintenance status unavailable'));return
        if not rows:self.tasks.append(task('database','LiveSync database',stage='No maintenance running'));return
        labels={'database_compaction':'Compacting database','view_compaction':'Compacting views','indexer':'Updating database index','replication':'Replicating database'}
        for i,row in enumerate(rows[:12]):
            item=task('database:'+str(i),'LiveSync database',state='running',stage=labels.get(row.get('type'),'Database maintenance'),started=timestamp(row.get('started_on')),updated=timestamp(row.get('updated_on')))
            if isinstance(row.get('progress'),(int,float)):item['progress']=max(0,min(100,row['progress']))
            self.tasks.append(item)

    def media(self):
        playlists=self.read('Music sync',lambda:small_json(STATE/'playlists.json',2*1024*1024))
        mapping={'running':'running','deleting':'running','queued':'queued','delete_queued':'queued','failed':'failed'}
        if isinstance(playlists,list):
            active=[p for p in playlists if p.get('status') in mapping]
            for p in active[:16]:
                self.tasks.append(task('music:'+str(p.get('id')),'Music sync',state=mapping[p['status']],stage='Removing playlist' if p['status']=='deleting' else 'Syncing playlist' if p['status']=='running' else 'Queued' if p['status'] in ('queued','delete_queued') else 'Needs attention',title=text(p.get('name')),started=timestamp(p.get('last_started_at')),updated=timestamp(p.get('last_finished_at')),details=[['Imported this sync',str(p.get('sync_success_count',0))],['Unavailable',str(p.get('sync_unavailable_count',0))]]))
            if not active:self.tasks.append(task('music','Music sync'))
        else:self.tasks.append(task('music','Music sync',state='unknown',stage='Queue unavailable'))
        def videos():
            with closing(database(STATE/'video_downloads.sqlite')) as db:
                return [dict(r) for r in db.execute("SELECT id,title,status,received,total,created FROM downloads WHERE status IN ('queued','downloading','deleting','failed') ORDER BY CASE status WHEN 'downloading' THEN 0 WHEN 'deleting' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,created DESC LIMIT 16")]
        video=self.read('Video downloads',videos)
        if video:
            for p in video:
                row=task('video:'+p['id'],'Video download',state={'downloading':'running','deleting':'running','queued':'queued','failed':'failed'}[p['status']],stage={'downloading':'Downloading','deleting':'Removing','queued':'Queued','failed':'Needs attention'}[p['status']],title=text(p['title']),started=p['created'])
                if p.get('total'):row['progress']=min(100,100*p['received']/p['total'])
                self.tasks.append(row)
        else:self.tasks.append(task('video','Video downloads',state='idle' if video is not None else 'unknown',stage='Waiting for work' if video is not None else 'Queue unavailable'))

    def transcription(self):
        def collect():
            with closing(database(STATE/'transcription/queue.sqlite')) as db:
                active=[dict(r) for r in db.execute("SELECT id,title,state,stage,progress,created,updated,lease_until FROM jobs WHERE state IN ('queued','running','uploading','failed') ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'uploading' THEN 2 ELSE 3 END,updated DESC LIMIT 20")]
                recent=[dict(r) for r in db.execute("SELECT id,title,state,stage,updated FROM jobs WHERE state IN ('completed','cancelled') ORDER BY updated DESC LIMIT 6")]
                return active,recent
        data=self.read('Transcription / lyrics',collect)
        if data is None:self.tasks.append(task('transcription','Transcription & lyrics','Cyborg',state='unknown',stage='Queue unavailable'));return
        active,recent=data
        for p in active:
            state={'running':'running','queued':'queued','uploading':'waiting','failed':'failed'}[p['state']]
            if state=='running' and (p['lease_until'] or 0)<self.now:state='waiting'
            self.tasks.append(task('transcription:'+p['id'],'Transcription / lyrics','Cyborg',state=state,stage=p['stage'] if state!='waiting' else 'Waiting for worker / upload',title=text(p['title']),progress=max(0,min(100,p['progress'])),started=p['created'],updated=p['updated']))
        if not active:self.tasks.append(task('transcription','Transcription & lyrics','Cyborg'))
        for p in recent:self.events.append(dict(id='transcription:'+p['id'],host='Cyborg',at=p['updated'],state=p['state'],label='Transcription / lyrics',message=text(p['title'])))

    def other(self):
        self.tasks.append(self.unit_task('backup','NAS backup & temp cleanup'))
        self.tasks.append(self.unit_task('recovery','Storage recovery'))
        cal=self.read('Calendar sync',lambda:small_json(STATE/'caldav-status.json'))
        self.tasks.append(task('calendar','Calendar sync',state='idle' if cal and cal.get('success') else 'failed' if cal else 'unknown',stage='Up to date' if cal and cal.get('success') else 'Needs attention' if cal else 'Status unavailable',updated=timestamp((cal or {}).get('checked_at'))))
        for pid,parent,elapsed,args in self.procs:
            if re.search(r'(?:^|/|\s)docker\s+(?:build\b|buildx\s+build\b)',args):
                self.tasks.append(task('build:'+str(pid),'Docker image build',state='running',stage='Building image',started=self.now-elapsed))
        health=self.read('Cyborg AI',lambda:http_json('100.66.32.22',9019,'/health',timeout=.6))
        self.tasks.append(task('ai','Cyborg AI','Cyborg',state='available' if health else 'unknown',stage='API available · individual requests not exposed' if health else 'AI API unavailable',title=text((health or {}).get('model'))))

    def helper_task(self):
        labels={'pdf':'PDF OCR / image inventory','image':'Pasted image OCR','embed':'Computing embeddings','vectors':'Updating vector mirror','search':'Semantic search','quartz':'Building published notes','source':'Receiving PDF source'}
        if self.helper.get('busy'):
            self.tasks.append(task('helper','Cyborg CPU helper','Cyborg',state='running',stage=labels.get(self.helper.get('job'),'Receiving / processing work')))
        else:self.tasks.append(task('helper','Cyborg CPU helper','Cyborg',stage='Waiting for work' if self.helper.get('ready') else 'Not running / unreachable',state='idle' if self.helper.get('ready') else 'unknown'))
        accepting=self.remote.get('accepting',self.helper.get('accepting'))
        paused=accepting is False
        offline=not self.helper and not self.remote
        for row in self.tasks:
            if row['host']=='Cyborg' and row['state']!='failed':
                row['workPending']=row['state'] in ('queued','running','waiting')
                if paused:row.update(state='paused',stage='Server tasks is Off')
                elif offline:row.update(state='waiting',stage='Waiting for Cyborg')
        if paused:self.unavailable=[name for name in self.unavailable if name not in ('Cyborg helpers','Cyborg AI')]
        return dict(id='Cyborg',state='paused' if paused else 'offline' if offline else 'online',accepting=accepting)

    def journal(self):
        output=subprocess.run(['/usr/bin/journalctl','--no-pager','-o','json','-n','36','-u',UNITS['ocr'],'-u',UNITS['publish'],'-u',UNITS['goodnotes']],capture_output=True,text=True,timeout=1.5,check=True).stdout
        for line in output.splitlines():
            try:
                row=json.loads(line);message=row.get('MESSAGE','');at=int(row['__REALTIME_TIMESTAMP'])/1e6
                label=message=''
                raw=row.get('MESSAGE','')
                if raw.startswith('{'):
                    data=json.loads(raw)
                    if 'ocr_pages_processed' in data:label='PDF OCR';message=text(data.get('path'))+' · page '+str(data.get('page'))
                    elif 'ocr_completed' in data:label='PDF OCR';message=str(data['ocr_completed'])+' pages completed'
                    elif 'links' in data:label='Goodnotes links';message=str(data.get('changed',0))+' links updated'
                elif raw.startswith('Quartz updated:'):label='Note publishing';message=text(raw)
                elif raw.startswith('Goodnotes cleanup:'):
                    data=json.loads(raw.split(':',1)[1]);label='Goodnotes cleanup';message=str(data.get('notebooks',0))+' notebooks · '+str(data.get('removed_pdfs',0))+' obsolete PDFs removed'
                if label:self.events.append(dict(id=row.get('__CURSOR',str(at)),host='Server',at=at,label=label,message=message,state='completed'))
            except (ValueError,KeyError,TypeError):continue

    def collect(self):
        self.base()
        for name,fn in [('Search queues',self.search),('Note publishing',self.publisher),('Goodnotes',self.goodnotes),('Database maintenance',self.couch),('Media jobs',self.media),('Transcription',self.transcription),('Other jobs',self.other)]:self.read(name,fn)
        self.read('Recent worker events',self.journal)
        cyborg=self.helper_task()
        return dict(version=1,generated=self.now,refreshAfter=5,hosts=[dict(id='Server',state='online'),cyborg],tasks=self.tasks,
                    events=sorted(self.events,key=lambda row:row['at'],reverse=True)[:24],unavailable=list(dict.fromkeys(self.unavailable)),stale=False)


def snapshot():
    now=time.monotonic()
    if CACHE['data'] and now-CACHE['at']<5:return CACHE['data']
    if not LOCK.acquire(blocking=False):
        if CACHE['data']:return {**CACHE['data'],'stale':True}
        return dict(version=1,generated=time.time(),refreshAfter=5,hosts=[],tasks=[],events=[],unavailable=['Snapshot loading'],stale=True)
    try:
        if CACHE['data'] and time.monotonic()-CACHE['at']<5:return CACHE['data']
        data=Collector().collect();CACHE.update(at=time.monotonic(),data=data);return data
    finally:LOCK.release()
