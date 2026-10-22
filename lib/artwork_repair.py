"""Repair artwork only for new imports and previously failed artwork jobs."""
from __future__ import annotations
import base64
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import mutagen
from mutagen.flac import Picture
from PIL import Image, ImageOps
from lib.artwork import _embed_cover

INTERVAL = 21600
SUPPORTED = {'.opus', '.ogg', '.m4a', '.mp3', '.flac'}
VIDEO_ID = re.compile(r'\[([A-Za-z0-9_-]{11})\]$')
log = logging.getLogger('artwork-repair')
SOURCE_ID = re.compile(r'(?:youtube\.com/watch\?(?:[^\s]*&)?v=|youtu\.be/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])')


def video_identifier(path: Path) -> str | None:
    match = VIDEO_ID.search(path.stem)
    if match:
        return match[1]
    # Older managed filenames omit the ID; yt-dlp normally stores the original URL.
    audio = mutagen.File(path)
    if audio is None:
        return None
    for key in ['purl', 'comment', 'description', 'url', 'website', '\u00a9cmt', 'COMM::eng']:
        for value in audio.get(key, []):
            match = SOURCE_ID.search(str(value))
            if match:
                return match[1]
    return None


def valid_cover(path: Path) -> bool:
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError('Audio tags could not be read')
    suffix = path.suffix.lower()
    if suffix in {'.opus', '.ogg'}:
        images = [Picture(base64.b64decode(x)).data for x in audio.get('metadata_block_picture', [])]
    elif suffix == '.m4a':
        images = audio.get('covr', [])
    elif suffix == '.mp3':
        images = [x.data for x in audio.tags.getall('APIC')] if audio.tags else []
    else:
        images = [x.data for x in audio.pictures]
    for data in images:
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            return True
        except Exception:
            continue
    return False


def thumbnail(identifier: str, output: Path) -> None:
    # Recover from the original video's thumbnail, avoiding uncertain album matching.
    for name in ['maxresdefault', 'hqdefault']:
        url = f'https://i.ytimg.com/vi/{identifier}/{name}.jpg'
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError('Thumbnail exceeds limit')
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                # YouTube uses a 120x90 stock image for some unavailable thumbnails.
                if image.width < 200 or image.height < 150:
                    continue
                edge = min(image.size)
                square = ImageOps.fit(image.convert('RGB'), (edge, edge))
                square.save(output, 'JPEG', quality=90)
            return
        except (urllib.error.URLError, ValueError, OSError):
            continue
    raise ValueError('Original video thumbnail is unavailable; retry later')


def embed(path: Path, cover: Path) -> None:
    if path.suffix.lower() in {'.flac', '.ogg'}:
        audio = mutagen.File(path)
        picture = Picture()
        picture.type, picture.mime = 3, 'image/jpeg'
        with Image.open(cover) as image:
            picture.width, picture.height = image.size
        picture.depth, picture.data = 24, cover.read_bytes()
        if path.suffix.lower() == '.flac':
            audio.clear_pictures()
            audio.add_picture(picture)
        else:
            audio['metadata_block_picture'] = [base64.b64encode(picture.write()).decode('ascii')]
        audio.save()
    else:
        _embed_cover(path, cover)


def repair(path: Path, identifier: str) -> None:
    before = path.stat()
    with tempfile.TemporaryDirectory(prefix='.artwork-repair-', dir=path.parent) as directory:
        folder = Path(directory)
        cover, copy = folder / 'cover.jpg', folder / path.name
        thumbnail(identifier, cover)
        shutil.copy2(path, copy)
        embed(copy, cover)
        if not valid_cover(copy):
            raise ValueError('Replacement artwork verification failed')
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('Audio changed during repair; original retained')
        os.replace(copy, path)
        # Watcher detects this tag-only replacement and regenerates the cover URL/cache.



def repair_imported_covers(report: Path, managed_dir: Path, state_path: Path) -> dict[str, int]:
    """Called by an existing playlist sync: no timer, polling, or library scan."""
    root = managed_dir.resolve()
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    candidates = dict(state)
    try:
        for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
            identifier, separator, raw_path = line.partition("\t")
            if not separator:
                continue
            raw = Path(raw_path.strip())
            if raw.is_symlink():
                continue
            path = raw.resolve()
            if path.is_relative_to(root) and path.suffix.lower() in SUPPORTED:
                key = str(path.relative_to(root))
                candidates.setdefault(key, {"identifier": identifier.strip()})
    except OSError:
        pass
    now = time.time()
    summary = {'valid': 0, 'repaired': 0, 'deferred': 0, 'errors': 0}
    attempted = 0
    for key, entry in candidates.items():
        raw = root / key
        if raw.is_symlink():
            state.pop(key, None)
            continue
        path = raw.resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in SUPPORTED or not path.is_file():
            state.pop(key, None)
            continue
        if now < entry.get('retry_at', 0) or attempted >= 10:
            state[key] = entry
            summary['deferred'] += 1
            continue
        try:
            if valid_cover(path):
                summary['valid'] += 1
                state.pop(key, None)
                continue
            attempted += 1
            identifier = entry.get('identifier', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
                identifier = video_identifier(path)
            if not identifier:
                raise ValueError('Original video ID unavailable; audio retained')
            repair(path, identifier)
            summary['repaired'] += 1
            state.pop(key, None)
        except Exception as error:
            summary['errors'] += 1
            previous = int(entry.get('failures', 0))
            delay = min(604800, INTERVAL * 2 ** min(previous, 5))
            state[key] = {**entry, 'retry_at': now + delay, 'failures': previous + 1}
            log.warning('Kept original audio for %s: %s', path.name, error)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2))
    os.replace(temporary, state_path)
    return summary
