"""One question, native Codex, same iPad vault tools; process exits afterward."""
import fcntl
import http.server
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path('/srv/tamestorage/.tamestorage_state/obsidian-mobile')

def atomic(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as stream: json.dump(value, stream, ensure_ascii=False)
    os.replace(temp, path)

def run(directory, lockfd):
    started = time.monotonic()
    token = uuid.uuid4().hex + uuid.uuid4().hex
    data = json.loads((directory / 'input.json').read_text())
    stop = threading.Event()
    serial = threading.Lock()
    calls = 0
    def expired():
        return (stop.is_set() or (directory / 'cancel').exists() or time.monotonic() - started > 300 or
                not (directory / 'heartbeat').exists() or time.time() - (directory / 'heartbeat').stat().st_mtime > 35)
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            nonlocal calls
            if self.path != '/mcp' or self.headers.get('Authorization') != 'Bearer ' + token:
                self.send_error(401); return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536: self.send_error(413); return
            try: req = json.loads(self.rfile.read(size))
            except ValueError: self.send_error(400); return
            if 'id' not in req:
                self.send_response(202); self.end_headers(); return
            method = req.get('method')
            if method == 'initialize':
                result = {'protocolVersion': req.get('params', {}).get('protocolVersion', '2025-03-26'),
                          'capabilities': {'tools': {}}, 'serverInfo': {'name': 'Obsidian iPad vault reader', 'version': '1.0.0'}}
            elif method == 'ping': result = {}
            elif method == 'tools/list': result = {'tools': data['tools']}
            elif method == 'tools/call':
                with serial:
                    calls += 1
                    try:
                        params = req.get('params', {})
                        if calls > 30 or params.get('name') not in {t['name'] for t in data['tools']}:
                            raise ValueError('Read-only tool budget reached.')
                        ident = uuid.uuid4().hex
                        atomic(directory / 'tool.json', {'id': ident, 'name': params['name'], 'arguments': params.get('arguments', {})})
                        while not expired():
                            try:
                                returned = json.loads((directory / 'result.json').read_text())
                                if returned.get('id') == ident:
                                    result = returned['result']; break
                            except (OSError, ValueError): pass
                            stop.wait(.15)
                        else: raise TimeoutError('The iPad reader stopped. Keep Obsidian in the foreground during a question.')
                    except Exception as error:
                        result = {'isError': True, 'content': [{'type': 'text', 'text': str(error)}]}
                    finally:
                        for name in ('tool.json', 'result.json'): (directory / name).unlink(missing_ok=True)
            else:
                self.send_error(405); return
            body = json.dumps({'jsonrpc': '2.0', 'id': req['id'], 'result': result}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError): pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    args = [str(ROOT / 'codex'), 'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules',
            '--skip-git-repo-check', '--sandbox', 'read-only', '-c', 'approval_policy="never"',
            '-c', 'project_doc_max_bytes=0', '-c', 'features.shell_tool=false', '--json', '--color', 'never',
            '-C', str(ROOT / 'work'), '-c', 'developer_instructions=' + json.dumps((ROOT / 'role.txt').read_text()),
            '-c', 'mcp_servers.lecture.url=' + json.dumps('http://127.0.0.1:' + str(server.server_port) + '/mcp'),
            '-c', 'mcp_servers.lecture.bearer_token_env_var="LECTURE_READER_TOKEN"',
            '-c', 'mcp_servers.lecture.required=true', '-c', 'mcp_servers.lecture.default_tools_approval_mode="auto"']
    if data['model']: args += ['-m', data['model']]
    if data['effort']: args += ['-c', 'model_reasoning_effort=' + json.dumps(data['effort'])]
    args += ['-']
    env = {**os.environ, 'CODEX_HOME': str(ROOT / 'home'), 'LECTURE_READER_TOKEN': token}
    child = None
    state = {'state': 'done'}
    try:
        child = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 env=env, start_new_session=True)
        child.stdin.write(data['prompt'].encode()); child.stdin.close()
        del data['prompt']
        (directory / 'input.json').unlink(missing_ok=True)
        selector = selectors.DefaultSelector(); selector.register(child.stdout, selectors.EVENT_READ)
        buffer = b''; output_size = 0; answer = ''; failure = ''
        while True:
            if expired(): raise TimeoutError('Question stopped or the iPad disconnected. Keep Obsidian in the foreground and retry.')
            ready = selector.select(.2)
            if ready:
                chunk = os.read(child.stdout.fileno(), 65536)
                if not chunk: break
                output_size += len(chunk)
                if output_size > 2000000: raise ValueError('Codex response exceeded size limit.')
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    try:
                        item = json.loads(line)
                        if item.get('type') == 'item.completed' and item.get('item', {}).get('type') == 'agent_message':
                            answer = item['item'].get('text', answer)
                        elif item.get('type') in ('turn.failed', 'error'):
                            failure = item.get('error', {}).get('message') or item.get('message') or 'Codex request failed.'
                    except (ValueError, AttributeError): pass
            if child.poll() is not None and not ready: break
        child.wait(timeout=5)
        if child.returncode or not answer: raise ValueError(failure or 'Codex could not finish. Check the server login/model and retry.')
        state['answer'] = answer
    except Exception as error:
        # Only user-facing errors; never emit CLI stderr/auth/config.
        state['error'] = str(error)[:700]
    finally:
        stop.set()
        if child is not None and child.poll() is None:
            try: os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try: os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                child.wait()
        server.shutdown(); server.server_close()
        for name in ('input.json', 'tool.json', 'result.json'): (directory / name).unlink(missing_ok=True)
        if (directory / 'cancel').exists() or not directory.exists(): shutil.rmtree(directory, ignore_errors=True)
        else: atomic(directory / 'state.json', state)
        os.close(lockfd)

if __name__ == '__main__':
    os.umask(0o077)
    run(Path(sys.argv[1]), int(sys.argv[2]))
