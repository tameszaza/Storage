"""Read the event-produced Goodnotes import summary; no request-time tree walk."""
import json
import os
import stat
from pathlib import Path, PurePosixPath

REPORT_LIMIT = 1024 * 1024


class ImportStatusUnavailable(OSError):
    """No authoritative import projection is available; never export all as fallback."""


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def open_pdf(root_fd, relative):
    """Open below the library without following symlinks, including parent folders."""
    parts = PurePosixPath(relative).parts
    directory = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError('Not a regular PDF')
        return fd
    finally:
        os.close(directory)


def import_warning(upload_folder, *, strict=False):
    report = Path(upload_folder).parent / '.tamestorage_state/goodnotes-obsidian-links/ipad-import-status.json'
    try:
        with report.open() as handle:
            raw = handle.read(REPORT_LIMIT + 1)
        if len(raw) > REPORT_LIMIT:
            raise ValueError('Import summary too large')
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('version') not in (1,2) or not isinstance(data.get('files'), list):
            raise ValueError('Unsupported import summary')
        root_fd = os.open(Path(upload_folder) / 'Goodnotes', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (OSError, ValueError) as exc:
        if strict:
            raise ImportStatusUnavailable('Goodnotes import list unavailable') from exc
        return None
    files = []
    seen = set()
    try:
        for item in data['files']:
            if not isinstance(item, dict):
                continue
            name = item.get('path')
            if (not isinstance(name, str) or not name or name.startswith('/')
                    or '\\' in name or '\x00' in name or not name.lower().endswith('.pdf')):
                continue
            if any(part in ('', '.', '..') for part in name.split('/')) or name in seen:
                continue
            seen.add(name)
            parts = PurePosixPath(name).parts
            try:
                fd = open_pdf(root_fd, name)
                try:
                    info = os.fstat(fd)
                finally:
                    os.close(fd)
            except OSError:
                # Deleted files and unsafe paths disappear from both warning and download.
                continue
            files.append({
                'path': name,
                'folder': 'Goodnotes/' + str(PurePosixPath(name).parent) if len(parts) > 1 else 'Goodnotes',
                'reason': 'manual' if item.get('reason') == 'manual' else 'unconfirmed',
                'confidence': 'missing' if data.get('version')==2 and item.get('confidence')=='missing' else 'unconfirmed',
                'size': info.st_size,
                'fingerprint': fingerprint(info),
            })
    finally:
        os.close(root_fd)
    missing=[item for item in files if item['confidence']=='missing']
    return {'count':len(files),'files':files,'total_bytes':sum(item['size'] for item in files),
            'missing_count':len(missing),'unconfirmed_count':len(files)-len(missing),
            'missing_bytes':sum(item['size'] for item in missing),'inventory_at':data.get('inventory_at')} if files else None
