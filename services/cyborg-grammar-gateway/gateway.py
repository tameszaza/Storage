#!/usr/bin/env python3
"""One accepted HTTP connection, reusing the server's socket-activation pattern."""
import http.client, json, os, socket, sys, time, hmac, subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode
from validation import validate, protect_markup

BASELINE_TIMEOUT = int(os.environ.get("TAMES_GRAMMAR_BASELINE_TIMEOUT", "45"))
REQUEST_BUDGET = int(os.environ.get("TAMES_GRAMMAR_REQUEST_BUDGET", "85"))  # Automatic grammar stays bounded; manual previews have a separate limit.
VERIFY_TIMEOUT = int(os.environ.get("TAMES_GRAMMAR_VERIFY_TIMEOUT", "6"))
ASSIST_TIMEOUT = int(os.environ.get("TAMES_GRAMMAR_ASSIST_TIMEOUT", "250"))
AI_ENABLED = os.environ.get("TAMES_GRAMMAR_AI_ENABLED", "true").lower() == "true"

def forward(host, port, method, path, body, content_type, timeout=6, connect_timeout=.6):
    connection = http.client.HTTPConnection(host, port, timeout=connect_timeout)
    try:
        connection.connect(); connection.sock.settimeout(timeout)
        connection.request(method, path, body, {'Content-Type': content_type, 'Connection': 'close'})
        response = connection.getresponse(); data = response.read(2_000_001)
        if len(data) > 2_000_000: raise ValueError('Oversized response')
        return response.status, data
    finally:
        connection.close()

def ai_available():
    # A Tailscale TCP connection alone can outlive the remote machine. Probe the
    # existing non-inference health route with a short read deadline as well.
    try:
        status, body = forward('100.66.32.22', 9019, 'GET', '/health', None,
                               'application/json', .6, connect_timeout=.35)
        return status == 200 and isinstance(json.loads(body), dict)
    except (OSError, ValueError, http.client.HTTPException):
        return False


def combine(baseline, extra):
    matches = baseline['matches']
    for match in extra.get('matches', []):
        start, length = match.get('offset'), match.get('length')
        if not isinstance(start, int) or not isinstance(length, int) or start < 0 or length <= 0: continue
        if not match.get('replacements'): continue
        # Sentence-wide style warnings and local grammar edits are independent.
        # Suppress duplicate actions, never a distinct warning merely overlapping.
        values = {r.get('value') for r in match['replacements'] if isinstance(r, dict)}
        if any(start == old['offset'] and length == old['length'] and
               values.intersection(r.get('value') for r in old.get('replacements', []) if isinstance(r, dict))
               for old in matches): continue
        matches.append(match)
    matches.sort(key=lambda match: match['offset'])
    return baseline

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args): pass
    def reply(self, status, data, mode='languagetool'):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.send_header('X-Cyborg-Grammar', mode)
        self.send_header('X-Cyborg-AI', getattr(self, 'ai_state', 'not-requested'))
        self.end_headers(); self.close_connection = True
        try: self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError): pass
    def do_GET(self):
        if self.path not in ('/v2/languages', '/v2/info'):
            return self.reply(404, b'{"error":"Not found"}')
        try:
            code, data = forward('127.0.0.1', 8010, 'GET', self.path, None, 'application/json')
            self.reply(code, data)
        except Exception: self.reply(503, b'{"error":"LanguageTool unavailable"}')
    def do_assist(self):
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 200000: return self.reply(413, b'{"error":"Passage too large"}')
            body = self.rfile.read(size)
            code, data = forward('100.66.32.22', 9019, 'POST', '/v1/assist', body, 'application/json', ASSIST_TIMEOUT)
            return self.reply(code, data, 'assistant')
        except Exception:
            return self.reply(503, b'{"error":"Cyborg AI is unavailable. Turn on AI mode and check Tailscale."}')
    def do_control(self):
        try:
            key=Path('/srv/tamestorage/.tamestorage_state/obsidian-mobile/bridge.key').read_text().strip()
            if not key or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+key):
                return self.reply(401,b'{"error":"Unauthorized"}')
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<=1024:return self.reply(413,b'{"error":"Request size limit"}')
            action=json.loads(self.rfile.read(size)).get('action')
            if action not in ('on','off','status'):return self.reply(400,b'{"error":"Unknown action"}')
            base='/home/tameszaza/.local/lib/cyborg-grammar-gateway/'
            result=subprocess.run(['/usr/bin/ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',
                '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+base+'mobile-known-hosts',
                '-o','IdentitiesOnly=yes','-i',base+'mobile-control-key','tameszaza@100.66.32.22',action],
                capture_output=True,text=True,timeout=22,check=True)
            return self.reply(200,json.dumps({'text':result.stdout[:4096].strip()}).encode(),'control')
        except Exception:return self.reply(503,b'{"error":"Cyborg or its HDD is unavailable"}')
    def do_POST(self):
        if self.path == '/v1/control': return self.do_control()
        if self.path == '/v1/assist': return self.do_assist()
        if self.path != '/v2/check': return self.reply(404, b'{"error":"Not found"}')
        started = time.monotonic()
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 262144: return self.reply(413, b'{"error":"Request too large"}')
            body = self.rfile.read(size); content_type = self.headers.get('Content-Type', 'application/x-www-form-urlencoded')
            # Auto-detection can load an uncached language; observed checks took 30s.
            code, data = forward('127.0.0.1', 8010, 'POST', self.path, body, content_type, BASELINE_TIMEOUT)
            if code != 200: return self.reply(code, data)
            baseline = json.loads(data)
            params = json.loads(body) if 'application/json' in content_type else {
                k: v[0] for k,v in parse_qs(body.decode(), keep_blank_values=True).items()}
            baseline['matches'] = protect_markup(params, baseline['matches'])
            data = json.dumps(baseline, ensure_ascii=False).encode()
            if not AI_ENABLED:
                self.ai_state = 'disabled'
                return self.reply(code, data)
            if 'LLM_RULE' in str(params.get('disabledRules', '')).split(',') or \
               'LLM' in str(params.get('disabledCategories', '')).split(','):
                return self.reply(code, data)
            if str(params.get('enabledOnly', 'false')).lower() == 'true' and \
               'LLM_RULE' not in str(params.get('enabledRules', '')).split(',') and \
               'LLM' not in str(params.get('enabledCategories', '')).split(','):
                return self.reply(code, data)
            language = baseline.get('language', {}).get('code', '')
            if not language.startswith('en'): return self.reply(code, data)
            if params.get('language', 'auto') == 'auto': params['language'] = language
            # Honor the same user-selected default/Picky level in both checkers.
            ai_params = dict(params)
            ai_body = json.dumps(ai_params).encode() if 'application/json' in content_type else urlencode(ai_params).encode()
            mode = 'languagetool'
            if not ai_available():
                self.ai_state = 'unavailable'
                return self.reply(code, data)
            self.ai_state = 'unavailable'
            try:
                remaining = REQUEST_BUDGET - (time.monotonic() - started) - VERIFY_TIMEOUT - 1
                if remaining <= 0: return self.reply(code, data)
                ai_code, ai_data = forward('100.66.32.22', 9019, 'POST', self.path, ai_body, content_type, min(80, remaining))
                if ai_code == 200:
                    self.ai_state = 'available'
                    extra = json.loads(ai_data)
                    extra['matches'] = protect_markup(params, extra.get('matches', []))
                    candidates = combine({'matches': list(baseline['matches'])}, extra)
                    candidates['matches'] = [m for m in candidates['matches'] if m not in baseline['matches']]
                    def verify(proposed):
                        encoded = json.dumps(proposed).encode() if 'application/json' in content_type else urlencode(proposed).encode()
                        remaining = REQUEST_BUDGET - (time.monotonic() - started)
                        if remaining <= 0: raise TimeoutError('Verification budget exhausted')
                        status, result = forward('127.0.0.1', 8010, 'POST', self.path, encoded, content_type, min(VERIFY_TIMEOUT, remaining))
                        if status != 200: raise ValueError('Verifier unavailable')
                        checked = json.loads(result)
                        checked['matches'] = protect_markup(proposed, checked['matches'])
                        return checked
                    accepted = validate(params, baseline, candidates, verify)
                    data = json.dumps(combine(baseline, accepted), ensure_ascii=False).encode(); mode = 'hybrid'
            except Exception:
                self.ai_state = 'unavailable'  # Any AI failure retains the already-fetched LT response.
            self.reply(code, data, mode)
        except Exception as error:
            # No request text, credentials or exception message in diagnostics.
            print('grammar request failed: type='+type(error).__name__+
                  ' elapsed='+str(round(time.monotonic()-started, 2))+'s', file=sys.stderr)
            if isinstance(error, TimeoutError):
                self.reply(504, b'{"error":"LanguageTool check timed out"}')
            else:
                self.reply(503, b'{"error":"LanguageTool unavailable"}')

if __name__ == '__main__':
    sock = socket.socket(fileno=0); sock.settimeout(max(REQUEST_BUDGET + 10, ASSIST_TIMEOUT + 5))
    Handler(sock, ('127.0.0.1', 0), SimpleNamespace(server_name='grammar', server_port=8096))
    sock.close()
