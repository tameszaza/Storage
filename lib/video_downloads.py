"""Persistent queue for direct, public HTTPS video downloads."""
import http.client
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import ssl
import subprocess
import time
from urllib.parse import urlsplit, urljoin
import uuid

ROOT = Path(os.environ.get('UPLOAD_FOLDER', '/srv/tamestorage/uploads')) / 'Admin' / 'Movies'
DB = Path(os.environ.get('USER_DATA_FILE', '/srv/tamestorage/.tamestorage_state/users.json')).parent / 'video_downloads.sqlite'
LIMIT = 100 * 1024**3
RESERVE = 2 * 1024**3

def connection():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS downloads (id TEXT PRIMARY KEY, title TEXT, url TEXT, status TEXT, received INTEGER DEFAULT 0, total INTEGER DEFAULT 0, filename TEXT DEFAULT "", message TEXT DEFAULT "", created REAL)')
    return db

def items():
    with connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM downloads ORDER BY created DESC')]

def update(key, **fields):
    with connection() as db:
        db.execute('UPDATE downloads SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?', [*fields.values(), key])

def folder(key):
    if str(uuid.UUID(key)) != key:
        raise ValueError('Invalid download')
    path = ROOT / key
    if ROOT.is_symlink() or path.is_symlink() or ROOT.parent.is_symlink():
        raise ValueError('The Movies folder cannot be a symbolic link')
    return path

def validate_url(url):
    u = urlsplit(url)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.port not in (None, 443):
        raise ValueError('Use a public HTTPS link to a video file.')
    return u

def enqueue(title, url):
    validate_url(url)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT count(*) FROM downloads WHERE status IN ('queued','downloading')").fetchone()[0] >= 20:
            raise ValueError('The queue is full. Wait for a download to finish.')
        key = str(uuid.uuid4())
        db.execute('INSERT INTO downloads (id,title,url,status,created) VALUES (?,?,?,?,?)', (key,title,url,'queued',time.time()))
    return key

def remove(key):
    folder(key)
    with connection() as db:
        db.execute("UPDATE downloads SET status='deleting', message='Stopping and removing files…' WHERE id=?", (key,))

def deleting(key):
    with connection() as db:
        row = db.execute('SELECT status FROM downloads WHERE id=?', (key,)).fetchone()
        return not row or row[0] == 'deleting'

def public_response(url, headers=None):
    # Resolve and pin each connection to a public address, including redirects.
    for _ in range(6):
        u = validate_url(url)
        addresses = socket.getaddrinfo(u.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError('Private network addresses are not supported.')
        conn = http.client.HTTPSConnection(u.hostname, timeout=20)
        raw = socket.create_connection(addresses[0][4][:2], timeout=20)
        try:
            conn.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname)
            conn.request('GET', (u.path or '/') + ('?' + u.query if u.query else ''), headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity', **(headers or {})})
            response = conn.getresponse()
        except Exception:
            raw.close()
            conn.close()
            raise
        if response.status in (301,302,303,307,308):
            url = urljoin(url, response.getheader('Location', ''))
            conn.close()
            continue
        if response.status != 200:
            conn.close()
            raise ValueError('The source returned HTTP ' + str(response.status) + '. Check the link or try again later.')
        return conn, response
    raise ValueError('The source redirects too many times.')

def download(row):
    key = row['id']
    path = folder(key)
    path.mkdir(parents=True, exist_ok=True)
    partial = path / 'video.part'
    conn = None
    try:
        if urlsplit(row['url']).hostname in ('hdwatch.pro', 'www.hdwatch.pro'):
            from lib.video_streams import download_page
            received = download_page(row, partial)
            if deleting(key):
                return
            total = received
        else:
            received = direct_download(row, partial)
        if deleting(key):
            return
        finish(row, partial, received)
    except Exception as exc:
        if not deleting(key):
            message = str(exc) if isinstance(exc, ValueError) else 'The download failed or timed out. Check the source and retry.'
            update(key, status='failed', message=message[:300])
    finally:
        partial.unlink(missing_ok=True)
        for name in ('segments.ts', 'remux.mp4', 'stream.log'):
            (path / name).unlink(missing_ok=True)

def direct_download(row, partial):
    key = row['id']
    conn = None
    try:
        conn, response = public_response(row['url'])
        mime = response.getheader('Content-Type', '').split(';')[0].lower()
        if mime in ('text/html','application/json','application/vnd.apple.mpegurl','application/x-mpegurl'):
            raise ValueError('This is a webpage or stream manifest. Paste a direct MP4, MOV, WebM or MKV file link.')
        total = int(response.getheader('Content-Length', '0'))
        if total < 0 or total > LIMIT or shutil.disk_usage(ROOT).free < total + RESERVE:
            raise ValueError('Not enough free space, or the video exceeds the 100 GB limit.')
        update(key, total=total, received=0, message='Downloading…')
        received, last = 0, 0
        with partial.open('wb') as output:
            while True:
                if deleting(key):
                    return
                data = response.read(256 * 1024)
                if not data:
                    break
                received += len(data)
                if received > LIMIT or shutil.disk_usage(ROOT).free < RESERVE:
                    raise ValueError('Download stopped to preserve 2 GB of free space.')
                output.write(data)
                if time.monotonic() - last > 1:
                    update(key, received=received)
                    last = time.monotonic()
        if total and received != total:
            raise ValueError('The connection ended before the full video arrived. Retry the download.')
        return received
    finally:
        if conn:
            conn.close()

def finish(row, partial, received):
        key = row['id']
        path = folder(key)
        probe = subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file','-show_entries','stream=codec_type,height:format=format_name','-of','json',str(partial)], capture_output=True, timeout=30)
        metadata = json.loads(probe.stdout or '{}')
        formats = metadata.get('format',{}).get('format_name','')
        if probe.returncode or not any(s.get('codec_type') == 'video' for s in metadata.get('streams',[])):
            raise ValueError('The downloaded file is not a recognized video.')
        if not any(s.get('codec_type')=='video' and s.get('height',0)>=720 for s in metadata.get('streams',[])):
            raise ValueError('The video is below 720p; it was not saved.')
        extension = '.mp4' if 'mp4' in formats or 'mov' in formats else '.webm' if 'webm' in formats else '.mkv' if 'matroska' in formats else None
        if not extension:
            raise ValueError('Use an MP4, MOV, WebM or MKV video file.')
        from werkzeug.utils import secure_filename
        name = (secure_filename(row['title'])[:100] or 'Video') + extension
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT status FROM downloads WHERE id=?',(key,)).fetchone()[0] == 'deleting':
                return
            partial.replace(path / name)
            db.execute("UPDATE downloads SET status='ready', received=?, filename=?, message=? WHERE id=?", (received,name,row.get('_ready_message','Ready to watch'),key))

def worker():
    import fcntl
    lock = DB.with_suffix('.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with connection() as db:
        db.execute("UPDATE downloads SET status='queued', received=0 WHERE status='downloading'")
    while True:
        for row in items():
            if row['status'] == 'deleting':
                path = folder(row['id'])
                if path.exists():
                    shutil.rmtree(path)
                with connection() as db:
                    db.execute('DELETE FROM downloads WHERE id=?', (row['id'],))
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM downloads WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE downloads SET status='downloading' WHERE id=?", (row['id'],))
        if row:
            download(dict(row))
        else:
            time.sleep(2)
