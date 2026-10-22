"""On-demand review of existing ASR data; publication uses the existing music worker."""
from contextlib import closing
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time

from lib.lyric_transcription import source_path, signature
from lib.transcription_store import QueueError, digest

STAMP=re.compile(r'^\[(\d+):(\d{2})\.(\d{2})\](.*)$')
MAX_TEXT=262144


def _hash(data): return hashlib.sha256(data).hexdigest()


def _generated(row,opt):
    return opt.get('kind')=='lyrics' and (opt.get('lyrics_disposition')=='transcribed' or (opt.get('lyrics_disposition') is None and row['stage']=='Synced lyrics ready'))


def _row(queue, ident):
    row=queue.get(ident); options=json.loads(row['options'])
    if row['state']!='completed' or not _generated(row,options):
        raise QueueError('This song has no generated lyrics to review.',404)
    return row,options


def _legacy_attribution(queue,row,options,raw,expected):
    if options.get('lyrics_disposition') is not None or options.get('lyrics_review_sha'): return False
    path=queue.work_dir(row['id'])/'transcript.ready'
    if path.is_symlink() or path.stat().st_size>MAX_TEXT or digest(path)!=expected: return False
    original=path.read_bytes()
    byline=b'[00:00.00]Transcribed by Cybrog AI'
    def body(value):
        return [line for line in value.splitlines() if not line.startswith(b'[by:') and line!=byline]
    # The prior migration added attribution only. Every other line must match exactly.
    return byline in raw.splitlines() and body(raw)==body(original)


def _editable(lines,opt):
    if 'lyrics_review_lines' in opt: return set(opt['lyrics_review_lines'])
    return {i for i,line in enumerate(lines) if (m:=STAMP.match(line)) and m[4].strip() and not (m[4].startswith('Transcribed by Cybrog AI') and int(m[1])==int(m[2])==int(m[3])==0)}


def _current(queue,row,options,music_root):
    source=source_path(options,music_root)
    if signature(source)!=options['lyrics_signature']: raise QueueError('The song changed. Reload its latest lyrics.')
    target=source.with_suffix('.lrc')
    if target.is_symlink() or not target.is_file(): raise QueueError('Generated lyrics are no longer available.',404)
    if target.stat().st_size>MAX_TEXT: raise QueueError('This lyric file is too large to review.',413)
    raw=target.read_bytes(); sha=_hash(raw)
    expected=options.get('lyrics_review_sha') or json.loads(row['artifacts'])['transcript']['sha256']
    pending=options.get('lyrics_review_pending',{})
    if sha!=expected and sha!=_hash(str(pending.get('text','')).encode()) and not _legacy_attribution(queue,row,options,raw,expected): raise QueueError('Lyrics were changed outside this editor. They have been preserved; reload after checking the file.')
    return source,target,raw,sha


def _version(sha,confirmed): return _hash((sha+json.dumps(confirmed,sort_keys=True)).encode())


def listing(queue,folder):
    result=[]
    with closing(queue.connect()) as db:
        for row in db.execute("SELECT id,stage,options FROM jobs WHERE state='completed' ORDER BY title"):
            opt=json.loads(row['options'])
            if not _generated(row,opt): continue
            if Path(opt.get('lyrics_source','')).parent.as_posix()!=folder: continue
            result.append(dict(id=row['id'],title=opt.get('lyrics_title','Song'),artist=opt.get('lyrics_artist',''),
                               reviewed=bool(opt.get('lyrics_reviewed')),pending=bool(opt.get('lyrics_review_pending'))))
    return result


def read(queue,ident,music_root):
    row,opt=_row(queue,ident); source,target,raw,sha=_current(queue,row,opt,music_root)
    path=queue.work_dir(ident)/'details.ready'
    if path.is_symlink() or digest(path)!=json.loads(row['artifacts'])['details']['sha256']:
        raise QueueError('Recognition details failed verification.')
    with gzip.open(path,'rb') as f: data=f.read(16*1024**2+1)
    if len(data)>16*1024**2: raise QueueError('Recognition details are too large.',413)
    units=json.loads(data).get('selected_word_units',[])
    timed=[]
    for index,line in enumerate(raw.decode('utf-8').splitlines()):
        match=STAMP.match(line)
        if match:
            start=int(match[1])*60+int(match[2])+int(match[3])/100
            timed.append(dict(index=index,start=start,time=line[:match.start(4)],text=match[4]))
    confirmed=opt.get('lyrics_review_confirmed',[])
    editable=_editable(raw.decode('utf-8').splitlines(),opt)
    for i,line in enumerate(timed):
        end=timed[i+1]['start'] if i+1<len(timed) else float('inf')
        words=[u.get('text','').strip() for u in units if u.get('uncertain') and line['start']-.025<=float(u.get('start',0))<end-.025]
        line['uncertain']=list(dict.fromkeys(w for w in words if w))
        line['confirmed']=line['index'] in confirmed
        line['editable']=line['index'] in editable
    return dict(id=ident,title=opt.get('lyrics_title',source.stem),artist=opt.get('lyrics_artist',''),
                revision=_version(sha,confirmed),lines=timed,pending=bool(opt.get('lyrics_review_pending')),pending_token=opt.get('lyrics_review_pending',{}).get('token'),
                save_error=opt.get('lyrics_review_error',''))


def submit(queue,ident,data,music_root):
    with queue.transaction() as db:
        row,opt=_row(queue,ident)
        if opt.get('lyrics_review_pending'): raise QueueError('A save is already in progress. Please wait.')
        source,target,raw,sha=_current(queue,row,opt,music_root)
        confirmed=opt.get('lyrics_review_confirmed',[])
        if data.get('revision')!=_version(sha,confirmed): raise QueueError('This review changed in another window. Reload before saving.')
        edits=data.get('lines')
        if not isinstance(edits,list) or len(edits)>10000: raise QueueError('Invalid lyric edits.',400)
        lines=raw.decode('utf-8').splitlines(); editable=_editable(lines,opt);seen=set();checked=[]
        for edit in edits:
            if not isinstance(edit,dict): raise QueueError('Invalid lyric line.',400)
            index=edit.get('index'); text=edit.get('text')
            if type(index) is not int or index in seen or not 0<=index<len(lines): raise QueueError('Invalid lyric line.',400)
            match=STAMP.match(lines[index])
            if not match or index not in editable: raise QueueError('Invalid lyric line.',400)
            if match[4].startswith('Transcribed by Cybrog AI') and int(match[1])==int(match[2])==int(match[3])==0:
                raise QueueError('Keep the Cybrog AI attribution.',400)
            if not isinstance(text,str) or len(text)>2000 or any(c in text for c in '\r\n\0'):
                raise QueueError('Use plain text for each line (up to 2,000 characters).',400)
            if type(edit.get('confirmed')) is not bool: raise QueueError('Invalid confirmation.',400)
            seen.add(index);lines[index]=lines[index][:match.start(4)]+text.strip()
            if edit['confirmed']: checked.append(index)
        if seen!=editable: raise QueueError('Send every editable lyric line. Reload and try again.',400)
        text='\n'.join(lines)+'\n'
        if len(text.encode())>MAX_TEXT: raise QueueError('Lyrics are too large.',413)
        token=secrets.token_hex(16)
        opt['lyrics_review_pending']=dict(token=token,base=sha,text=text,confirmed=sorted(checked),editable=sorted(editable))
        opt.pop('lyrics_review_error',None)
        db.execute('UPDATE jobs SET options=?,updated=? WHERE id=?',(json.dumps(opt),time.time(),ident))
        # One marker check is folded into the existing playlist loop; no new timer/process.
        (queue.root/'lyrics-review-wake').touch()
    return token


def status(queue,ident,token):
    row,opt=_row(queue,ident)
    pending=opt.get('lyrics_review_pending',{})
    if pending.get('token')==token: return dict(state='saving')
    if opt.get('lyrics_review_token')==token:
        return dict(state='error' if opt.get('lyrics_review_error') else 'saved',error=opt.get('lyrics_review_error',''))
    raise QueueError('This save is no longer current. Reload the review.')


def publish_pending(queue,music_root='/srv/music'):
    marker=queue.root/'lyrics-review-wake'
    if not marker.exists(): return 0
    count=0
    with queue.transaction() as db:
        rows=list(db.execute("SELECT * FROM jobs WHERE state='completed' AND options LIKE '%\"lyrics_review_pending\"%'") )
        for entry in rows:
            row=dict(entry);opt=json.loads(row['options']);pending=opt.get('lyrics_review_pending')
            if not pending: continue
            temporary=None
            try:
                source,target,raw,sha=_current(queue,row,opt,music_root)
                text=pending['text'].encode(); newsha=_hash(text)
                if sha!=pending['base'] and sha!=newsha: raise QueueError('Lyrics changed before saving. Reload to keep the latest version.')
                if digest(source)!=row['sha256']: raise QueueError('The original song changed. Lyrics were preserved.')
                temporary=target.with_name('.lyrics-review-'+row['id']+'.part')
                # Recovery after replace-before-DB-commit accepts only this exact requested content.
                if sha!=newsha:
                    temporary.unlink(missing_ok=True)
                    with temporary.open('xb') as f: f.write(text);f.flush();os.fsync(f.fileno())
                    if target.is_symlink() or digest(target)!=pending['base']: raise QueueError('Lyrics changed during saving. Reload before trying again.')
                    os.replace(temporary,target);os.utime(source.parent,None)
                    directory=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
                    try: os.fsync(directory)
                    finally: os.close(directory)
                opt['lyrics_review_lines']=pending['editable']
                opt['lyrics_review_sha']=newsha;opt['lyrics_review_confirmed']=pending['confirmed']
                opt['lyrics_reviewed']=True;opt.pop('lyrics_review_error',None);count+=1
            except (QueueError,FileNotFoundError) as error:
                opt['lyrics_review_error']=error.message if isinstance(error,QueueError) else 'The song or its lyrics were removed. Nothing was recreated.'
            finally:
                if temporary: temporary.unlink(missing_ok=True)
            opt['lyrics_review_token']=pending['token'];opt.pop('lyrics_review_pending',None)
            db.execute('UPDATE jobs SET options=?,updated=? WHERE id=?',(json.dumps(opt),time.time(),row['id']))
        if not rows: marker.unlink(missing_ok=True)
    return count
