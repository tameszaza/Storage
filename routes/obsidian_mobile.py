"""On-demand mobile Codex relay. No model, index, daemon or idle polling."""
import fcntl
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from flask import jsonify, request, send_file

ROOT = Path('/srv/tamestorage/.tamestorage_state/obsidian-mobile')
ALLOWED = {'find_files', 'read_file', 'read_page_image', 'search_notes', 'semantic_search'}
JOB = re.compile(r'^[0-9a-f]{32}$')
ASSETS = {'pdf-runtime.js', 'pdf-worker.js'}

def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
    os.replace(temporary, path)

def reply(value, code=200):
    response = jsonify(value)
    response.status_code = code
    response.headers['Cache-Control'] = 'no-store'
    return response

def authorized():
    try:
        key = (ROOT / 'bridge.key').read_text().strip()
        return bool(key) and hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + key)
    except OSError:
        return False

def command(action):
    if not authorized(): return reply({'error': 'Unauthorized'}, 401)
    if request.content_length is None or not 0 < request.content_length <= 2200000:
        return reply({'error': 'Request size limit'}, 413)
    data = request.get_json(silent=True)
    if not isinstance(data, dict): return reply({'error': 'Expected JSON'}, 400)
    if action == 'start':
        prompt, tools = data.get('prompt'), data.get('tools')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 200000:
            return reply({'error': 'Question/context size limit'}, 400)
        if not isinstance(tools, list) or not 1 <= len(tools) <= 5 or any(not isinstance(t, dict) or t.get('name') not in ALLOWED for t in tools):
            return reply({'error': 'Read-only vault tools required'}, 400)
        model, effort = data.get('model', ''), data.get('effort', '')
        if not isinstance(model, str) or not re.fullmatch(r'[\w.:-]{0,100}', model) or effort not in ('', 'low', 'medium', 'high'):
            return reply({'error': 'Invalid model/reasoning'}, 400)
        if not (ROOT / 'home/auth.json').is_file(): return reply({'error': 'Server Codex login is unavailable'}, 503)
        # NAS must exist; never create the runtime on an unmounted root directory.
        if not (ROOT / 'codex').is_file() or not (ROOT / 'jobs').is_dir():
            return reply({'error': 'Private server storage is unavailable'}, 503)
        lock = (ROOT / 'active.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return reply({'error': 'Another iPad question is running. Try again shortly.'}, 409)
        directory = ROOT / 'jobs' / uuid.uuid4().hex
        try:
            # Prune abandoned result metadata on a new request, never by a timer.
            for old in (ROOT / 'jobs').iterdir():
                if old.is_dir() and JOB.fullmatch(old.name) and time.time() - old.stat().st_mtime > 600:
                    shutil.rmtree(old, ignore_errors=True)
            directory.mkdir(mode=0o700)
            atomic(directory / 'input.json', {'prompt': prompt, 'tools': tools, 'model': model, 'effort': effort})
            atomic(directory / 'state.json', {'state': 'running'})
            (directory / 'heartbeat').touch()
            subprocess.Popen([sys.executable, '/app/lib/obsidian_mobile_runner.py', str(directory), str(lock.fileno())],
                             pass_fds=(lock.fileno(),), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            return reply({'error': 'Could not start private Codex reader'}, 503)
        finally:
            lock.close()
        return reply({'job': directory.name})
    job = data.get('job', '')
    if not isinstance(job, str) or not JOB.fullmatch(job): return reply({'error': 'Invalid question ID'}, 400)
    directory = ROOT / 'jobs' / job
    if not directory.is_dir(): return reply({'error': 'Question expired or stopped'}, 404)
    if action == 'cancel':
        (directory / 'cancel').touch()
        return reply({'stopped': True})
    if action == 'poll':
        (directory / 'heartbeat').touch()
        try: state = json.loads((directory / 'state.json').read_text())
        except (OSError, ValueError): return reply({'state': 'running'})
        if state.get('state') == 'done':
            # The response is the only server copy of the answer after this point.
            if not data.get('heartbeat'): shutil.rmtree(directory, ignore_errors=True)
            return reply(state)
        try: state['tool'] = json.loads((directory / 'tool.json').read_text())
        except (OSError, ValueError): pass
        return reply(state)
    if action == 'result':
        ident, result = data.get('id'), data.get('result')
        try: current = json.loads((directory / 'tool.json').read_text())
        except (OSError, ValueError): return reply({'error': 'No tool call pending'}, 409)
        if ident != current.get('id') or not isinstance(result, dict): return reply({'error': 'Stale tool result'}, 409)
        atomic(directory / 'result.json', {'id': ident, 'result': result})
        return reply({'ok': True})
    return reply({'error': 'Not found'}, 404)

def asset(name):
    if name not in ASSETS: return reply({'error': 'Not found'}, 404)
    target = ROOT / 'assets' / name
    if not target.is_file() or target.is_symlink(): return reply({'error': 'Engine unavailable'}, 503)
    return send_file(target, mimetype='application/javascript', conditional=True, max_age=86400)

def register_routes(app):
    for name in ('start', 'poll', 'result', 'cancel'):
        app.add_url_rule('/obsidian-mobile/' + name, 'obsidian_mobile_' + name,
                         lambda action=name: command(action), methods=['POST'])
    app.add_url_rule('/obsidian-mobile/assets/<name>', 'obsidian_mobile_asset', asset)
