"""Stage incoming bytes beside their destination; publish only a complete upload."""
import os
from pathlib import Path
import tempfile


def save_atomic(uploaded, destination, before_replace=None):
    path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
    original=path.stat() if path.exists() else None
    expected=(original.st_dev,original.st_ino,original.st_size,original.st_mtime_ns) if original else None
    fd,temporary=tempfile.mkstemp(prefix='.upload-',suffix='.partial',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as output:
            uploaded.save(output);output.flush();os.fsync(output.fileno())
            os.fchmod(output.fileno(),original.st_mode & 0o777 if original else 0o664)
        current=path.stat() if path.exists() else None
        actual=(current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns) if current else None
        if actual!=expected:raise OSError('Destination changed during upload. Retry with the current file.')
        if before_replace:before_replace()
        os.replace(temporary,path)
    finally:
        try:os.unlink(temporary)
        except FileNotFoundError:pass
