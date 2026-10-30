"""Account-scoped text files and Admin LiveSync notes, with revision-aware writes."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import secrets
from typing import Literal
from urllib.parse import urlencode

from flask import current_app,g,url_for
from lib.workspace_protocol import RejectedOperation

MAX_FILE=1024*1024


def operation_id():
    key=getattr(g,'workspace_operation_key','')
    job=getattr(g,'workspace_job_id','')
    if not key or not job:raise RejectedOperation('This operation needs the durable Workspace runner')
    return hashlib.sha256((job+'\0'+key).encode()).hexdigest()[:32]


def selected_images(numbers,default_all=False):
    context=getattr(g,'diary_context',{})
    attached=context.get('images_bytes',[])
    if numbers is None:numbers=list(range(1,len(attached)+1)) if default_all else []
    if not isinstance(numbers,list) or len(numbers)!=len(set(numbers)) or any(type(i)!=int or not 1<=i<=len(attached) for i in numbers):
        raise RejectedOperation('Select existing attached images by their 1-based numbers')
    from routes.obsidian_images import store_image
    return [store_image(attached[i-1])['url'] for i in numbers]


def note_rpc(data):
    from lib.assistant_diary import NODE,SCRIPT
    result=subprocess.run([NODE,'--max-old-space-size=96',SCRIPT],input=json.dumps(data,ensure_ascii=False),text=True,capture_output=True,timeout=75)
    for line in reversed(result.stdout.splitlines()):
        if line.startswith('TAMES_NOTE_RESULT='):
            value=json.loads(line.split('=',1)[1])
            if value.get('no_change'):raise RejectedOperation(str(value.get('error','The note changed; read it again'))[:500])
            if result.returncode==0 and value.get('success'):return value
    raise RuntimeError('The note service did not return a verified result')


def note_tools(username):
    from lib.assistant_diary import available
    if not available(username):return {}
    def search_obsidian_notes(query: str = '', limit: int = 20, cursor: str = '') -> dict:
        """Find Obsidian Markdown note paths by name/path without reading note contents. Empty query lists notes. Returns a continuation cursor when limited; pass it to inspect more metadata. Admin vault only, no whole-vault content download."""
        try:return note_rpc(dict(operation='search',query=query,limit=limit,cursor=cursor))
        except Exception:return {'error':'Vault note discovery is unavailable. Retry when storage is available or supply a known note path.'}
    def read_obsidian_note(path: str, start_line: int = 1, max_lines: int = 160) -> dict:
        """Read a known Obsidian Markdown note through normal encrypted LiveSync. Returns text, existence and a revision token. Read successive line ranges for long notes. Admin vault only; this is distinct from NAS files."""
        try:return note_rpc(dict(operation='read',path=path,start_line=start_line,max_lines=max_lines))
        except RejectedOperation as exc:return {'error':str(exc)}
        except Exception:return {'error':'The note could not be read. Check its path or try again when vault storage is available.'}
    def write_obsidian_note(path: str, content: str, mode: Literal['create','append','replace','edit'] = 'create', revision: str = '', find: str = '', replace_all: bool = False, image_numbers: list[int] | None = None) -> dict:
        """Create, append, replace or exactly edit a known Obsidian .md note. For existing notes first read_obsidian_note and use its revision. edit replaces exact find text with content; repeated matches require replace_all=true. image_numbers explicitly attaches selected images (null/[] means none). Each call is an independent, retry-safe operation; existing concurrent edits are preserved."""
        if len(content)>16000 or mode not in ('create','append','replace','edit'):raise RejectedOperation('Use supported note mode and text up to 16,000 characters')
        images=selected_images(image_numbers)
        if images:content+='\n\n'+'\n\n'.join('![Image]('+image+')' for image in images)
        result=note_rpc(dict(operation='write',id=operation_id(),path=path,content=content,mode=mode,revision=revision,find=find,replace_all=replace_all))
        uri='obsidian://open?'+urlencode({'vault':'Notes','file':path})
        action=dict(action='note.saved',title=path,url=uri,operation_id=result['operation_id'],label={'create':'Created note','append':'Appended to note','replace':'Replaced note contents','edit':'Edited note'}[mode])
        g.assistant_actions=getattr(g,'assistant_actions',[])+[action]
        from lib.activity import log_activity
        log_activity('ai.note.'+mode,path)
        return {**result,'open_url':uri,'image_urls':images,'verified':True}
    return {tool.__name__:tool for tool in (search_obsidian_notes,read_obsidian_note,write_obsidian_note)}


def file_tools(username):
    from lib.ai_client import _resolve_workspace_path,_visible_path
    from lib.ai_client import is_ai_readable_file
    def resolve(path):
        relative,absolute=_resolve_workspace_path(username,path)
        if not relative or relative=='Admin/ChatGPT Archive' or relative.startswith('Admin/ChatGPT Archive/') or not is_ai_readable_file(relative):
            raise RejectedOperation('Use an ordinary text/code/Markdown file inside this account; managed data is unavailable here')
        return relative,Path(absolute)
    def parent_fd(relative):
        from lib.storage import upload_root
        descriptor=os.open(upload_root(),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in Path(relative).parts[:-1]:
                nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor);os.close(descriptor);descriptor=nxt
            return descriptor,Path(relative).name
        except BaseException:os.close(descriptor);raise
    def load(descriptor,name):
        try:fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=descriptor)
        except FileNotFoundError:return None,None
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_FILE:raise RejectedOperation('Edit one regular text file under 1 MiB')
            data=stream.read(MAX_FILE+1)
        if len(data)>MAX_FILE:raise RejectedOperation('The file exceeds the edit size limit')
        try:data.decode('utf-8')
        except UnicodeError:raise RejectedOperation('This is not a UTF-8 text file')
        return data,info
    def write(path,content,mode,revision,find='',replace_all=False):
        relative,absolute=resolve(path)
        try:descriptor,leaf=parent_fd(relative)
        except FileNotFoundError:raise RejectedOperation('The parent folder does not exist. Create it, then resume this file operation')
        temporary=None
        try:
            before,info=load(descriptor,leaf)
            old_hash=hashlib.sha256(before).hexdigest() if before is not None else ''
            store=getattr(g,'workspace_store',None)
            if store is None:raise RejectedOperation('This operation needs the durable Workspace runner')
            identity=operation_id();job=g.workspace_job_id
            intent=store.db.execute('SELECT * FROM file_intents WHERE job=? AND key=?',(job,identity)).fetchone()
            if intent and old_hash==intent['target']:return file_receipt(relative,old_hash,'already_saved',identity,mode)
            if mode=='create' and before is not None:raise RejectedOperation('The file exists. Read it, then explicitly append/edit/replace using its revision')
            if mode!='create' and before is None:raise RejectedOperation('The file does not exist. Choose create or the correct path')
            if before is not None and (not re.fullmatch('[0-9a-f]{64}',revision) or revision!=old_hash):raise RejectedOperation('The file changed or has not been read. Read its current text/revision before editing')
            text=before.decode('utf-8') if before is not None else ''
            if mode=='append':output=text+content
            elif mode=='edit':
                count=text.count(find) if find else 0
                if not count or count>1 and not replace_all:raise RejectedOperation('The edit target is missing or not unique. Read the file and provide exact find text')
                output=text.replace(find,content,-1 if replace_all else 1)
            else:output=content
            data=output.encode('utf-8')
            if len(data)>MAX_FILE:raise RejectedOperation('Resulting text file must be under 1 MiB')
            digest=hashlib.sha256(data).hexdigest()
            if intent and (intent['path']!=relative or intent['target']!=digest):raise RejectedOperation('The pending file intent differs; inspect the current file before continuing')
            with store.db:store.db.execute('INSERT OR IGNORE INTO file_intents(job,key,path,expected,target) VALUES(?,?,?,?,?)',(job,identity,relative,old_hash,digest))
            temporary=Path('.workspace-write-'+secrets.token_hex(16))
            fd=os.open(temporary.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=descriptor)
            with os.fdopen(fd,'wb') as stream:
                if info:os.fchmod(stream.fileno(),stat.S_IMODE(info.st_mode))
                else:os.fchmod(stream.fileno(),0o644)
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            # Optimistic revision check immediately before the atomic replacement.
            current,current_info=load(descriptor,leaf)
            if current!=before or info and (info.st_ino,info.st_mtime_ns)!=(current_info.st_ino,current_info.st_mtime_ns):raise RejectedOperation('The file changed during preparation; read its current revision')
            if before is not None:
                from lib.versions import create_version
                create_version(relative,'before assistant '+mode)
                current,_=load(descriptor,leaf)
                if current!=before:raise RejectedOperation('The file changed while preserving its previous version')
                os.replace(temporary.name,leaf,src_dir_fd=descriptor,dst_dir_fd=descriptor)
            else:os.link(temporary.name,leaf,src_dir_fd=descriptor,dst_dir_fd=descriptor,follow_symlinks=False)
            os.fsync(descriptor)
            check,_=load(descriptor,leaf)
            if hashlib.sha256(check).hexdigest()!=digest:raise RuntimeError('Saved file could not be read back')
            from lib.search_index import invalidate_search_cache
            from lib.activity import log_activity
            invalidate_search_cache();log_activity('ai.file.'+mode,relative)
            return file_receipt(relative,digest,'saved',identity,mode)
        finally:
            if temporary:
                try:os.unlink(temporary.name,dir_fd=descriptor)
                except FileNotFoundError:pass
            os.close(descriptor)
    def file_receipt(path,revision,status,identity,mode):
        url=url_for('index',path=str(Path(path).parent))
        action=dict(action='file.saved',title=path,url=url,operation_id=identity,label={'create':'Created file','append':'Appended to file','replace':'Replaced file contents','edit':'Edited file'}[mode])
        g.assistant_actions=getattr(g,'assistant_actions',[])+[action]
        return dict(success=True,path=path,revision=revision,status=status,open_url=url,verified=True)
    def write_text_file(path: str, content: str, mode: Literal['create','append','replace'] = 'create', revision: str = '') -> dict:
        """Create a UTF-8 file, append text or replace its contents. Useful for reports, Markdown, code, CSV and JSON. Read existing files first and supply their revision; creates never overwrite. One operation is retry-safe and preserves the existing version on edits. Parent folders must exist; create them as a preceding operation."""
        return write(path,content,mode,revision)
    def edit_text_file(path: str, find: str, replacement: str, revision: str = '', replace_all: bool = False) -> dict:
        """Edit exact text in an existing UTF-8 file using a revision from read_text_file. Replaces one unique match, or every exact match when replace_all=true. Does not execute the file."""
        return write(path,replacement,'edit',revision,find,replace_all)
    return {tool.__name__:tool for tool in (write_text_file,edit_text_file)}


def with_file_revision(tool,username):
    """Keep the existing bounded reader, add an optimistic edit token."""
    from functools import wraps
    @wraps(tool)
    def wrapped(*args,**kwargs):
        result=tool(*args,**kwargs)
        if result.get('error'):return result
        from lib.ai_client import _resolve_workspace_path
        path=kwargs.get('path') or (args[0] if args else '')
        try:
            _,absolute=_resolve_workspace_path(username,path)
            fd=os.open(absolute,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as stream:
                data=stream.read(MAX_FILE+1)
            if len(data)<=MAX_FILE:result={**result,'revision':hashlib.sha256(data).hexdigest()}
        except OSError:pass
        return result
    return wrapped
