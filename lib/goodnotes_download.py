"""On-demand PDF ZIP using stdlib streaming; no staging archive or compression."""
from collections import deque
import os
from pathlib import Path
import time
import zipfile

from lib.goodnotes_status import fingerprint, open_pdf

CHUNK_SIZE = 256 * 1024


class _ZipOutput:
    def __init__(self):
        self.chunks = deque()
        self.offset = 0

    def write(self, data):
        if data:
            self.chunks.append(data)
            self.offset += len(data)
        return len(data)

    def tell(self):
        return self.offset

    def flush(self):
        pass

    def drain(self):
        while self.chunks:
            yield self.chunks.popleft()


def stream_import_zip(upload_folder, files):
    """Paths are relative to Goodnotes, with no extra wrapper or hidden metadata.

    Keep one source open at a time. Abort on concurrent source changes so a partial
    or inconsistent library is not silently delivered as a valid complete ZIP.
    Closing the iterator releases source/root handles without leaving disk files.
    """
    output = _ZipOutput()
    root_fd = os.open(Path(upload_folder) / 'Goodnotes', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for item in files:
                fd = open_pdf(root_fd, item['path'])
                with os.fdopen(fd, 'rb') as source:
                    before = os.fstat(source.fileno())
                    if fingerprint(before) != tuple(item['fingerprint']):
                        raise OSError('Goodnotes library changed during download; retry')
                    modified = time.localtime(max(315532800, min(before.st_mtime, 4354819199)))[:6]
                    info = zipfile.ZipInfo(item['path'], date_time=modified)
                    info.file_size = before.st_size
                    info.compress_type = zipfile.ZIP_STORED
                    info.external_attr = 0o100644 << 16
                    with archive.open(info, 'w') as member:
                        yield from output.drain()
                        remaining = before.st_size
                        while remaining:
                            chunk = source.read(min(CHUNK_SIZE, remaining))
                            if not chunk:
                                raise OSError('Goodnotes PDF changed during download; retry')
                            member.write(chunk)
                            remaining -= len(chunk)
                            yield from output.drain()
                        if fingerprint(os.fstat(source.fileno())) != fingerprint(before):
                            raise OSError('Goodnotes PDF changed during download; retry')
                    yield from output.drain()
        yield from output.drain()
    finally:
        os.close(root_fd)
