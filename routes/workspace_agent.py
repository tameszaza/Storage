"""Account-scoped Workspace queue API, served by the existing web container."""
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

from flask import current_app, jsonify, request, session, abort

from lib.workspace_store import Store, ROOT


def store(): return Store(current_app.config.get('WORKSPACE_AGENT_ROOT',ROOT))


def assistant_origin_allowed():
    origin=request.headers.get('Origin')
    if not origin:return True
    origin=origin.rstrip('/')
    if origin==request.host_url.rstrip('/'):return True
    # Tailnet HTTPS is terminated by Serve before the local HTTP backend.
    # Accept that upgrade only for the same authority; CSRF remains mandatory.
    return request.scheme=='http' and origin=='https://'+request.host


def protect(write=False):
    if not session.get('logged_in') or not session.get('username'):abort(401)
    if write:
        if not assistant_origin_allowed():abort(403)
        token=session.get('assistant_csrf','');supplied=request.form.get('csrf','')
        if not token or not supplied.isascii() or not secrets.compare_digest(token,supplied):abort(403)


def enqueue(question,provider,images,include_context):
    protect(True)
    if request.headers.get('X-Workspace-Client')!='1':
        return jsonify(response='Refresh the Assistant page to load the Workspace upgrade before sending this request.'),409
    if provider not in ('cyborg','google'):abort(400)
    if len(question)>6000 or len(question.encode())>14000 or (not question and not images):return jsonify(response='Use a shorter Workspace request (up to 6,000 characters) or attach images.'),400
    request_id=request.form.get('request_id','')
    if not re.fullmatch(r'[A-Za-z0-9.-]{1,128}',request_id):return jsonify(response='Reload the assistant before submitting this message.'),400
    from lib.ai_client import build_ai_context,calendar_write_requested
    if question and not images and calendar_write_requested(question) and re.search(r'\b(?:images?|photos?|pictures?|screenshots?)\b',question,re.I):
        return jsonify(response='Attach the schedule image first. No calendar dates were added.')
    from lib.assistant_agenda import calendar_clock
    if images:
        from lib.assistant_images import open_images,ImageInputError
        try:
            with open_images(images):pass
        except ImageInputError as exc:return jsonify(response=str(exc)),400
    history=[]
    try:
        raw=request.form.get('history','[]')
        if len(raw)>20000:raise ValueError()
        value=json.loads(raw)
        if not isinstance(value,list):raise ValueError()
        for message in value[-10:]:
            if not isinstance(message,dict) or message.get('role') not in ('user','assistant'):raise ValueError()
            history.append({'role':message['role'],'text':str(message.get('text',''))[:1200]})
    except (ValueError,TypeError):return jsonify(response='Conversation context is invalid. Reload and try again.'),400
    preferences={'detail':request.form.get('detail_level','balanced'),'format':request.form.get('response_style','practical')}
    if preferences['detail'] not in ('brief','balanced','deep') or preferences['format'] not in ('practical','step-by-step','checklist','table'):abort(400)
    payload=dict(include_context=include_context,clock=calendar_clock(),history=history,preferences=preferences,retry=request.form.get('archive_refresh')=='1',
        context=build_ai_context(username=session['username'],include_tree=include_context,
            file_path=request.form.get('file_path','').strip(),detail_level=preferences['detail'],response_style=preferences['format']))
    try:
        refs=json.loads(request.form.get('context_jobs','[]'))
        if not isinstance(refs,list) or len(refs)>4 or any(not isinstance(k,str) or not re.fullmatch('[0-9a-f]{64}',k) for k in refs):raise ValueError()
        payload['context_jobs']=refs
    except (ValueError,TypeError):return jsonify(response='Invalid conversation references.'),400
    # Read/navigation commands retain their deterministic, model-free behavior.
    if include_context and not images and re.match(r'^\s*(?:/(?:help|commands|open|go|navigate|inspect|info)\b|commands$|open\s|go to\s|navigate to\s)',question,re.I):
        from routes.ai import handle_file_command
        direct=handle_file_command(question)
        if direct and direct.get('handled'):return jsonify(direct)
    if include_context and not images:
        match=re.match(r'^/(move|copy|rename)\s+(.+)$',question,re.I)
        if match:
            from routes.ai import _split_command_arguments
            left,right=_split_command_arguments(match[2])
            if not left or not right:return jsonify(response='Command is incomplete. Use /help for examples.'),400
            name={'move':'move_file','copy':'copy_file','rename':'rename_path'}[match[1].lower()]
            payload['direct']={'tool':name,'arguments':{'source':left,('new_name' if name=='rename_path' else 'destination_folder'):right}}
    try:
        with store() as queue:
            retained=[]
            for key in refs:
                row=queue.db.execute('SELECT owner FROM jobs WHERE id=?',(key,)).fetchone()
                if row and row['owner']!=session['username']:raise FileNotFoundError()
                if row:retained.append(key)
            payload['context_jobs']=retained
            job=queue.enqueue(session['username'],request_id,provider,question,payload,images)
    except FileNotFoundError:return jsonify(response='An earlier conversation reference expired. Start a new chat.'),400
    except ValueError as exc:return jsonify(response=str(exc)),400
    except OSError:return jsonify(response='Workspace storage is unavailable. No job was submitted.'),503
    if job['result']:return jsonify(job['result'])
    return jsonify(activity=job['activity'],response=job['activity']['step'],workspace_job=job['id'],workspace_state=job['status'],provider=job['provider'])


def job(key):
    protect()
    if not re.fullmatch('[0-9a-f]{64}',key):abort(404)
    try:
        with store() as queue:result=queue.get(key,session['username'])
    except FileNotFoundError:abort(404)
    return jsonify(result)


def cancel(key):
    protect(True)
    if not re.fullmatch('[0-9a-f]{64}',key):abort(404)
    try:
        with store() as queue:queue.cancel(key,session['username']);result=queue.get(key,session['username'])
    except FileNotFoundError:abort(404)
    return jsonify(result)


def process():
    with store() as queue:
        expected=(queue.root/'worker-key').read_text().strip()
        supplied=request.headers.get('X-Workspace-Key','')
        if len(expected)<32 or not supplied.isascii() or not secrets.compare_digest(expected,supplied):abort(403)
        with (queue.root/'worker.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return jsonify(progress=False,pending=True)
            code='''import json,sys,os
from lib.workspace_store import configure_worker
configure_worker()
os.environ['TAMES_WORKSPACE_CHILD']='1'
from app import app
from lib.workspace_agent import step
with app.app_context():progress=step(sys.argv[1])
print(json.dumps({'progress':progress}))
'''
            try:
                child=subprocess.run([sys.executable,'-B','-c',code,str(queue.root)],capture_output=True,text=True,timeout=285)
                progress=json.loads(child.stdout)['progress'] if child.returncode==0 else False
                if child.returncode:current_app.logger.warning('Workspace worker exited code=%s',child.returncode)
            except (subprocess.TimeoutExpired,ValueError,KeyError):progress=False
            queue.sync_marker()
            return jsonify(progress=progress,pending=queue.pending())


def register_routes(app):
    app.add_url_rule('/chat/jobs/<key>','workspace_job',job)
    app.add_url_rule('/chat/jobs/<key>/cancel','workspace_cancel',cancel,methods=['POST'])
    app.add_url_rule('/chat/process','workspace_process',process,methods=['POST'])
    @app.after_request
    def private_jobs(response):
        if (request.endpoint or '').startswith('workspace_'):response.headers['Cache-Control']='private, no-store'
        return response
    # One startup recovery check, using the existing path-triggered dispatcher.
    # Worker children must not recursively wake themselves during app bootstrap.
    root=Path(app.config.get('WORKSPACE_AGENT_ROOT',ROOT))
    if os.environ.get('TAMES_WORKSPACE_CHILD')!='1' and (root/'jobs.sqlite3').is_file():
        try:
            with Store(root) as queue:
                if queue.pending():queue.wake()
        except Exception:app.logger.warning('Workspace queue could not be resumed during startup')
