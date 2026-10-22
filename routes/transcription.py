"""Admin upload/download form and a narrowly scoped Cyborg worker API."""
import codecs
from contextlib import contextmanager
import fcntl
from functools import wraps
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3

from flask import abort, current_app, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import ClientDisconnected
from lib.transcription_store import ARTIFACTS, QueueError, Store, valid_id

def store():
    upload=Path(current_app.config['UPLOAD_FOLDER'])
    return Store(current_app.config.get('TRANSCRIPTION_ROOT',str(upload.parent/'.tamestorage_state/transcription')),
                 current_app.config.get('TRANSCRIPTION_INPUTS',str(upload/'temp/Transcription')),
                 current_app.config.get('TRANSCRIPTION_OUTPUTS',str(upload/'Admin/Transcripts')))

def body():
    raw=request.stream.read(8193)
    if len(raw)>8192: raise QueueError('Request is too large.',413)
    try: data=json.loads(raw or b'{}')
    except ValueError: raise QueueError('Invalid request.',400)
    if not isinstance(data,dict): raise QueueError('Invalid request.',400)
    return data

def admin(view):
    @wraps(view)
    def wrapped(*args,**kwargs):
        if not session.get('logged_in') or session.get('username')!='Admin': return redirect(url_for('login'))
        if request.method not in {'GET','HEAD'}:
            supplied=request.headers.get('X-CSRF-Token','')
            if not supplied or not hmac.compare_digest(supplied.encode(),session.get('transcription_csrf','').encode()): abort(403)
        try: return view(*args,**kwargs)
        except (OSError,sqlite3.Error): raise QueueError('Transcription storage is unavailable. Please retry shortly.',503)
    return wrapped

def worker(view):
    @wraps(view)
    def wrapped(*args,**kwargs):
        try: key=(store().root/'worker.key').read_text().strip()
        except OSError: raise QueueError('Transcription storage is unavailable.',503)
        supplied=request.headers.get('Authorization','').removeprefix('Bearer ')
        if not key or not hmac.compare_digest(supplied.encode(),key.encode()): abort(401)
        try: return view(*args,**kwargs)
        except (OSError,sqlite3.Error): raise QueueError('Transcription storage is unavailable. Please retry shortly.',503)
    return wrapped

@contextmanager
def job_lock(queue,job_id):
    with (queue.work_dir(job_id)/'transfer.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise QueueError('A file transfer is still finishing. Try again shortly.')
        yield

def receive(target,limit,expected_size=None,expected_sha=None,text=False):
    if request.content_length is None: raise QueueError('A file length is required.',411)
    if not 0<request.content_length<=limit or (expected_size is not None and request.content_length!=expected_size): raise QueueError('Unexpected file size.',413)
    temporary=target.with_name(target.name+'.part'); sha=hashlib.sha256(); count=0
    decoder=codecs.getincrementaldecoder('utf-8')('strict') if text else None
    try:
        with temporary.open('wb') as output:
            while True:
                chunk=request.stream.read(min(1024**2,limit+1-count))
                if not chunk: break
                count+=len(chunk)
                if count>limit: raise QueueError('File is too large.',413)
                if decoder:
                    decoded=decoder.decode(chunk)
                    if '\0' in decoded: raise QueueError('Result must be plain UTF-8 text.',400)
                output.write(chunk); sha.update(chunk)
            if decoder: decoder.decode(b'',final=True)
            if count!=request.content_length or (expected_sha and not hmac.compare_digest(sha.hexdigest(),expected_sha)):
                raise QueueError('The transfer checksum did not match. Please retry.',400)
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary,target); return sha.hexdigest(),count
    except UnicodeError: raise QueueError('Result must be plain UTF-8 text.',400)
    except ClientDisconnected: raise QueueError('The upload was interrupted.',400)
    finally: temporary.unlink(missing_ok=True)

@admin
def page():
    token=session.setdefault('transcription_csrf',secrets.token_urlsafe(32))
    return render_template('transcription.html',csrf=token)

@admin
def jobs():
    queue=store()
    if request.method=='GET': return jsonify(jobs=queue.listing())
    data=body(); options=data.get('options',{})
    if not isinstance(options,dict): raise QueueError('Invalid options.',400)
    row=queue.create(data.get('filename',''),data.get('size'),data.get('title',''),options)
    return jsonify(job=queue.public(row),upload_url=url_for('transcription_upload',job_id=row['id'])),201

@admin
def upload(job_id):
    queue=store()
    with job_lock(queue,job_id):
        row=queue.get(job_id)
        if row['state']!='uploading': raise QueueError('This upload has already finished.')
        sha,_=receive(queue.input_path(row),5*1024**3,expected_size=row['size'])
        queue.uploaded(job_id,sha)
    return jsonify(job=queue.public(queue.get(job_id)))

@admin
def action(job_id,action):
    queue=store()
    with job_lock(queue,job_id): queue.action(job_id,action)
    return jsonify(ok=True)

@admin
def download(job_id,name):
    queue=store(); row=queue.get(job_id)
    if name not in ARTIFACTS or row['state']!='completed': abort(404)
    if name=='details' or json.loads(row['options']).get('kind')=='lyrics': path=queue.work_dir(job_id)/(name+'.ready')
    else:
        relative=row['result_dir'] or ''
        if Path(relative).name!=relative: abort(404)
        folder=queue.outputs/relative
        if folder.is_symlink(): abort(404)
        path=folder/ARTIFACTS[name][0]
    if path.is_symlink() or not path.is_file(): abort(404)
    return send_file(path,as_attachment=True,download_name=ARTIFACTS[name][0],mimetype='application/gzip' if name=='details' else 'text/plain; charset=utf-8',conditional=False,max_age=0)

@worker
def claim():
    row=store().claim()
    if row is None: return '',204
    return jsonify(job={k:row[k] for k in ('id','title','filename','size','sha256','options','lease')})

@worker
def worker_job(job_id):
    return jsonify(job=store().public(store().get(job_id)))

@worker
def media(job_id):
    queue=store(); row=queue.leased(job_id,request.headers.get('X-Job-Lease','')); path=queue.input_path(row)
    if path.is_symlink() or not path.is_file(): raise QueueError('The original recording is unavailable.',410)
    response=send_file(path,mimetype='application/octet-stream',conditional=False,max_age=0)
    response.headers['X-Content-SHA256']=row['sha256']; return response

@worker
def heartbeat(job_id):
    data=body(); store().progress(job_id,request.headers.get('X-Job-Lease',''),data.get('stage','Processing'),data.get('progress',0),data.get('message',''))
    return jsonify(ok=True)

@worker
def artifact(job_id,name):
    queue=store(); lease=request.headers.get('X-Job-Lease','')
    if name not in ARTIFACTS: abort(404)
    expected=request.headers.get('X-Content-SHA256','')
    if len(expected)!=64: raise QueueError('A checksum is required.',400)
    with job_lock(queue,job_id):
        queue.leased(job_id,lease)
        sha,count=receive(queue.work_dir(job_id)/(name+'.ready'),ARTIFACTS[name][1],expected_sha=expected,text=name!='details')
        queue.artifact_saved(job_id,lease,name,sha,count)
    return jsonify(sha256=sha,bytes=count)

@worker
def complete(job_id):
    queue=store()
    with job_lock(queue,job_id): result=queue.complete(job_id,request.headers.get('X-Job-Lease',''))
    return jsonify(job=result)

@worker
def pause(job_id):
    queue=store();data=body()
    with job_lock(queue,job_id):queue.pause(job_id,request.headers.get('X-Job-Lease',''),data.get('reason','Server tasks off'))
    return jsonify(ok=True)

@worker
def failed(job_id):
    data=body(); store().fail(job_id,request.headers.get('X-Job-Lease',''),data.get('message','Cyborg could not finish this recording. Retry after checking the worker.'))
    return jsonify(ok=True)

def register_routes(app):
    app.add_url_rule('/admin/transcription','transcription_page',page)
    app.add_url_rule('/admin/transcription/jobs','transcription_jobs',jobs,methods=['GET','POST'])
    app.add_url_rule('/admin/transcription/jobs/<job_id>/upload','transcription_upload',upload,methods=['PUT'])
    app.add_url_rule('/admin/transcription/jobs/<job_id>/<action>','transcription_action',action,methods=['POST'])
    app.add_url_rule('/admin/transcription/jobs/<job_id>/download/<name>','transcription_download',download)
    app.add_url_rule('/api/transcription/claim','transcription_claim',claim,methods=['POST'])
    app.add_url_rule('/api/transcription/jobs/<job_id>','transcription_worker_job',worker_job)
    app.add_url_rule('/api/transcription/jobs/<job_id>/media','transcription_media',media)
    app.add_url_rule('/api/transcription/jobs/<job_id>/heartbeat','transcription_heartbeat',heartbeat,methods=['POST'])
    app.add_url_rule('/api/transcription/jobs/<job_id>/artifacts/<name>','transcription_artifact',artifact,methods=['PUT'])
    app.add_url_rule('/api/transcription/jobs/<job_id>/complete','transcription_complete',complete,methods=['POST'])
    app.add_url_rule('/api/transcription/jobs/<job_id>/pause','transcription_pause',pause,methods=['POST'])
    app.add_url_rule('/api/transcription/jobs/<job_id>/failed','transcription_failed',failed,methods=['POST'])

    @app.errorhandler(QueueError)
    def queue_error(error): return jsonify(error=error.message),error.status

    @app.before_request
    def transcription_storage_guard():
        if request.endpoint and request.endpoint.startswith('transcription_'):
            try: store()
            except (OSError,sqlite3.Error,QueueError): return jsonify(error='Transcription storage is unavailable.'),503

    @app.after_request
    def private_responses(response):
        if request.endpoint and request.endpoint.startswith('transcription_'):
            response.headers['Cache-Control']='private, no-store, max-age=0'
            response.headers['X-Content-Type-Options']='nosniff'
        return response
