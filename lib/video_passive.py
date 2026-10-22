"""Discover Vidfast media with a normal browser and a passive network log.

No DevTools connection is attached. Browser requests go through a local
CONNECT proxy which resolves and pins only public HTTPS destinations.
"""
import http.server
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit


def public_addresses(host, port):
    if not host or port != 443:
        raise ValueError('Only public HTTPS destinations are supported.')
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Private network addresses are not supported.')
    return addresses


def stop_process(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def browser_executable():
    choices = list(Path('/opt/playwright').glob('chromium-*/chrome-linux64/chrome'))
    if not choices:
        raise ValueError('The full Chromium browser is not installed.')
    return str(max(choices, key=lambda path: int(re.search(r'chromium-(\d+)', str(path))[1])))


class PublicProxy(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    slots = threading.BoundedSemaphore(48)


class Tunnel(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        if not self.server.slots.acquire(blocking=False):
            self.send_error(503)
            return
        upstream = None
        try:
            destination = urlsplit('https://' + self.path)
            if destination.username or destination.password or destination.path:
                raise ValueError('Invalid tunnel target.')
            addresses = public_addresses(destination.hostname, destination.port or 443)
            for address in addresses:
                try:
                    upstream = socket.create_connection(address[4][:2], timeout=10)
                    break
                except OSError:
                    continue
            if upstream is None:
                raise OSError('The destination did not accept a connection.')
            self.send_response(200, 'Connection established')
            self.end_headers()
            self.wfile.flush()
            endpoints = [self.connection, upstream]
            while not self.server.stopping.is_set():
                readable, _, _ = select.select(endpoints, [], [], 1)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = upstream if source is self.connection else self.connection
                    target.sendall(data)
        except (OSError, ValueError):
            if upstream is None:
                try:
                    self.send_error(502)
                except OSError:
                    pass
        finally:
            if upstream is not None:
                upstream.close()
            self.server.slots.release()


class LogReader:
    def __init__(self):
        self.offset = 0
        self.partial = b''
        self.urls = {}
        self.headers = []

    def read(self, path):
        if not path.exists():
            return
        with path.open('rb') as stream:
            stream.seek(self.offset)
            data = stream.read()
            self.offset = stream.tell()
        if self.offset > 64 * 1024**2:
            raise ValueError('The player produced too much network activity before loading a video.')
        lines = (self.partial + data).split(b'\n')
        self.partial = lines.pop()
        for line in lines:
            if not line.startswith(b'{"params"'):
                continue
            try:
                event = json.loads(line.rstrip(b','))
            except ValueError:
                continue
            params = event.get('params', {})
            url = params.get('url', '')
            if isinstance(url, str) and '.m3u8' in urlsplit(url).path.lower():
                if urlsplit(url).scheme == 'https':
                    self.urls.setdefault(url, event.get('source', {}).get('id'))
            headers = params.get('headers')
            if isinstance(headers, list):
                self.headers.append((event.get('source', {}).get('id'), headers))
                self.headers = self.headers[-2000:]

    def candidates(self, origin, user_agent):
        results = []
        for url, source_id in self.urls.items():
            parsed = urlsplit(url)
            headers = {'Referer': origin + '/', 'Origin': origin, 'User-Agent': user_agent}
            for header_source, lines in self.headers:
                http2_match = f':authority: {parsed.netloc}' in lines and any(
                    line.startswith(':path: ' + parsed.path) for line in lines)
                if header_source != source_id and not http2_match:
                    continue
                for line in lines:
                    name, separator, value = line.partition(':')
                    if separator and name.lower() in ('referer', 'origin', 'user-agent', 'accept'):
                        headers[name.lower()] = value.strip()
                        headers.pop(name.title(), None)
            results.append((url, headers))
        return results


def discover(url, cancelled, timeout=150):
    target = urlsplit(url)
    if (target.scheme != 'https' or target.hostname not in ('vidfast.pro', 'vidfast.vc')
            or target.username or target.password or target.port not in (None, 443)):
        raise ValueError('Unsupported passive player source.')
    executable = browser_executable()
    proxy = PublicProxy(('127.0.0.1', 0), Tunnel)
    proxy.stopping = threading.Event()
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='vidfast-') as directory:
            root = Path(directory)
            log = root / 'network.json'
            reader = LogReader()
            wrapper = root / 'player.html'
            wrapper.write_text('<!doctype html><html><body style="margin:0;background:#111">'
                               '<iframe allow="autoplay; fullscreen; picture-in-picture" allowfullscreen '
                               'style="border:0;width:100vw;height:100vh" src="' + html.escape(url, quote=True)
                               + '"></iframe></body></html>', encoding='utf-8')
            with (root / 'browser.log').open('wb') as output:
                display = subprocess.Popen(['Xvfb', '-displayfd', '1', '-screen', '0', '1280x800x24',
                                            '-nolisten', 'tcp'], stdout=subprocess.PIPE,
                                           stderr=output, start_new_session=True)
                process = None
                command = [executable, '--no-sandbox', '--disable-dev-shm-usage',
                           '--no-first-run', '--no-default-browser-check', '--autoplay-policy=no-user-gesture-required',
                           f'--proxy-server=http://127.0.0.1:{proxy.server_port}', '--proxy-bypass-list=<-loopback>',
                           f'--user-data-dir={root / "profile"}', f'--log-net-log={log}', wrapper.as_uri()]
                try:
                    if not select.select([display.stdout], [], [], 10)[0]:
                        raise ValueError('The browser display did not start.')
                    number = display.stdout.readline().decode().strip()
                    if not number.isdecimal():
                        raise ValueError('The browser display could not be initialized.')
                    environment = {**os.environ, 'DISPLAY': ':' + number,
                                   'XDG_CONFIG_HOME': str(root), 'XDG_CACHE_HOME': str(root),
                                   'XDG_DATA_HOME': str(root)}
                    process = subprocess.Popen(command, stdout=output, stderr=output,
                                               env=environment, start_new_session=True)
                    deadline = time.monotonic() + timeout
                    settle_until = None
                    while time.monotonic() < deadline and process.poll() is None:
                        cancelled()
                        reader.read(log)
                        if reader.urls:
                            if settle_until is None:
                                settle_until = time.monotonic() + 1
                            if time.monotonic() >= settle_until:
                                break
                        time.sleep(.5)
                finally:
                    stop_process(process)
                    stop_process(display)
                    display.stdout.close()
                reader.read(log)
            if not reader.urls:
                raise ValueError('Vidfast did not provide a video stream within its loading window.')
            return reader.candidates('https://vidfast.vc', 'Mozilla/5.0')
    finally:
        proxy.stopping.set()
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=2)
