"""Admin archive pages and machine-authenticated collector bridge."""
import contextlib
import fcntl
import hashlib
import hmac
import json
import re
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys

from flask import abort,current_app,jsonify,render_template,request,session
from lib.chat_archive import Archive,ROOT,STATE,exact_answer,ident,wake
from lib.security import admin_required

def archive(): return Archive(current_app.config.get('CHAT_ARCHIVE_ROOT',ROOT))

def protect():
    if not session.get('logged_in') or session.get('username')!='Admin':abort(403)

def machine():
    try:key=(STATE/'collector-key').read_text().strip()
    except OSError:abort(503)
    if not hmac.compare_digest(request.headers.get('X-Export-Key',''),key):abort(403)

@admin_required
def page():
    protect()
    try:
        with archive() as store:
            status=store.status();rows=store.recent()
    except (OSError,sqlite3.Error):status=None;rows=[]
    if not session.get('assistant_csrf'):session['assistant_csrf']=secrets.token_urlsafe(32)
    return render_template('chat_archive.html',archive_status=status,archive_rows=rows,assistant_csrf=session['assistant_csrf'])

@admin_required
def search():
    protect()
    try:
        from lib.chat_archive_jobs import helper
        with archive() as store:result=store.search(request.args.get('q',''),limit=20,role=request.args.get('role',''),after=request.args.get('after',''),before=request.args.get('before',''),semantic=request.args.get('semantic')=='1',helper=helper)
        return jsonify(result)
    except ValueError:return jsonify(error='Check the search filters.'),400
    except (OSError,sqlite3.Error):return jsonify(error='Archive is not ready yet.'),503

@admin_required
def conversation(scope,cid):
    protect()
    try:
        with archive() as store:doc=store.conversation(scope,cid,request.args.get('page',0))
    except (FileNotFoundError,ValueError):abort(404)
    return render_template('chat_archive_conversation.html',conversation=doc)

@admin_required
def job(key):
    protect()
    try:
        with archive() as store:result=store.answer_job(key)
    except (FileNotFoundError,ValueError):abort(404)
    return jsonify(result)

def respond(question,mode):
    protect()
    token=session.get('assistant_csrf','');supplied=request.form.get('csrf','')
    if not token or not supplied.isascii() or not secrets.compare_digest(token,supplied):abort(403)
    if mode not in ('cyborg','google','excerpts'):abort(400)
    if not question.strip() or len(question)>1000:return jsonify(response='Use a question under 1,000 characters.'),400
    try:
        from lib.chat_archive_jobs import helper
        with archive() as store:
            result=store.search(question,semantic=mode!='excerpts',helper=helper)
            sources=result['sources']
            if re.search(r'\b(it|that|those|they|afterward|instead)\b',question,re.I):
                previous=[]
                for key in session.get('archive_last_sources',[])[:8]:
                    row=store.db.execute('SELECT c.* FROM archive_chunks c WHERE c.id=? AND c.branch=1 AND NOT EXISTS(SELECT 1 FROM archive_dirty d WHERE d.scope=c.scope AND d.cid=c.cid)',(key,)).fetchone()
                    if row:previous.append(store.source(row))
                existing={s['id'] for s in sources}
                sources=(sources+[s for s in previous if s['id'] not in existing])[:8]
                if previous:question='Previous question: '+session.get('archive_last_question','')+'\nFollow-up: '+question
            if sources:
                session['archive_last_sources']=[s['id'] for s in sources]
                session['archive_last_question']=question[-200:]
            reply=exact_answer(sources)
            if not sources:
                if mode=='cyborg' and result['semantic']=='waiting for Cyborg':
                    key=store.enqueue_answer(question,[])
                    queued=store.answer_job(key)
                    if queued['status']=='ready':return jsonify(queued['result'])
                    reply.update(response='Waiting for Cyborg to answer from your chats.',archive_job=key,provider='Cyborg',archive_state='queued')
                return jsonify(reply)
            if mode=='cyborg':
                key=store.enqueue_answer(question,sources);queued=store.answer_job(key)
                if queued['status']=='ready':return jsonify(queued['result'])
                reply.update(response='Waiting for Cyborg to answer from your chats.',archive_job=key,provider='Cyborg',archive_state='queued')
            elif mode=='google':
                from lib.chat_archive_jobs import google_answer
                cache_key=hashlib.sha256(json.dumps(['v1',question,sources],sort_keys=True).encode()).hexdigest()
                row=store.db.execute('SELECT result FROM archive_answer_cache WHERE key=?',(cache_key,)).fetchone()
                if row and request.form.get('archive_refresh')!='1':reply=json.loads(row[0])
                else:
                    try:
                        reply=google_answer(question,sources,current_app.config.get('CHAT_ARCHIVE_MODEL','gemini-3.6-flash'))
                        with store.db:
                            store.db.execute('INSERT OR REPLACE INTO archive_answer_cache VALUES(?,?,strftime(\'%s\',\'now\'))',(cache_key,json.dumps(reply,ensure_ascii=False)))
                            store.db.execute('DELETE FROM archive_answer_cache WHERE key IN (SELECT key FROM archive_answer_cache ORDER BY created DESC LIMIT -1 OFFSET 256)')
                    except Exception:reply.update(response='Google is unavailable. Matching excerpts are below.')
            return jsonify(reply)
    except (OSError,sqlite3.Error):return jsonify(response='Archive is not ready yet.'),503

def collector():
    machine()
    if not request.content_length or request.content_length>64*1024*1024:return jsonify(error='size'),413
    req=request.get_json(silent=True)
    if not isinstance(req,dict):return jsonify(error='request'),400
    op=req.get('op');payload=req.get('payload',{})
    if req.get('version')!=2 and op!='overview':return jsonify(error='version'),409
    try:
        from lib.chat_archive_export import Store,valid_id
        root=Path(current_app.config.get('CHAT_ARCHIVE_ROOT',ROOT))
        if not (root/'data/progress.sqlite3').is_file():return jsonify(error='storage'),503
        with (STATE/'collector.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            store=Store(root/'data')
            try:
                if op=='overview':result=store.overview()
                elif op=='reserve':result=store.reserve()
                elif op=='cooldown':result=store.cooldown(payload)
                elif op=='throttle':result=store.throttle()
                else:
                    scope=valid_id(req.get('scope'))
                    if op=='state':result=store.state(scope)
                    elif op=='reset_discovery':
                        with store.db:store.db.execute('DELETE FROM discovery WHERE scope=?',(scope,))
                        result={'reset':True}
                    elif op=='checkpoint':result=store.checkpoint(scope,payload)
                    elif op=='save':result=store.save(scope,payload)
                    elif op=='report':result=store.report(scope,payload)
                    elif op=='finalize':result=store.finalize(scope)
                    else:raise ValueError('Unknown operation')
            finally:store.db.close()
        if op in ('save','checkpoint'):wake()
        return jsonify(ok=True,result=result)
    except (ValueError,TypeError,KeyError):return jsonify(error='request'),400
    except Exception:
        current_app.logger.error('Archive collector operation failed: %s',op if op in ('overview','state','save','checkpoint','report','finalize','reserve','throttle','cooldown','reset_discovery') else 'unknown')
        return jsonify(error='write'),503

def process():
    machine()
    if (STATE/'maintenance').exists():return jsonify(ok=True,pending=True,progress=False)
    from lib.chat_archive_jobs import step
    def counts(store):
        return {name:store.db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('archive_dirty','archive_embeddings','archive_mirror')} | {'answers_pending':store.db.execute("SELECT count(*) FROM archive_answers WHERE status IN ('queued','running')").fetchone()[0]}
    with archive() as store:before=counts(store)
    # Short-lived background child: never lower a reusable web thread's priority.
    code='''import os,resource,shutil,subprocess,json,sys
os.nice(19)
if shutil.which('ionice'):subprocess.run(['ionice','-c','3','-p',str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
resource.setrlimit(resource.RLIMIT_AS,(384*1024**2,384*1024**2))
resource.setrlimit(resource.RLIMIT_CPU,(45,50))
from lib.chat_archive_jobs import step
print(json.dumps({'pending':step(sys.argv[1])}))
'''
    child=subprocess.run([sys.executable,'-B','-c',code,str(current_app.config.get('CHAT_ARCHIVE_ROOT',ROOT))],capture_output=True,text=True,timeout=285)
    with archive() as store:after=counts(store);pending=store.pending()
    progress=any(before[k]!=after[k] for k in ('archive_dirty','archive_embeddings','archive_mirror','answers_pending'))
    with (STATE/'queue.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with archive() as store:
            if store.pending():(STATE/'pending').touch()
            else:(STATE/'pending').unlink(missing_ok=True)
    return jsonify(ok=True,pending=pending,progress=progress)

def register_routes(app):
    app.add_url_rule('/admin/chat-archive','chat_archive_page',page)
    app.add_url_rule('/admin/chat-archive/search','chat_archive_search',search)
    app.add_url_rule('/admin/chat-archive/<scope>/<cid>','chat_archive_conversation',conversation)
    app.add_url_rule('/admin/chat-archive/jobs/<key>','chat_archive_job',job)
    app.add_url_rule('/admin/chat-archive/collector','chat_archive_collector',collector,methods=['POST'])
    app.add_url_rule('/admin/chat-archive/process','chat_archive_process',process,methods=['POST'])
    @app.after_request
    def private_archive(response):
        if (request.endpoint or '').startswith('chat_archive'):
            response.headers['Cache-Control']='private, no-store'
            response.headers['X-Content-Type-Options']='nosniff'
        return response
