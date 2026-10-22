"""Read a ready OCR PDF; never run OCR, load an index, or queue work in HTTP."""
import hashlib
import json
from pathlib import Path

PDF_ROOT = Path('/srv/tamestorage/uploads/Goodnotes')
CACHE_ROOT = Path('/srv/tamestorage/.tamestorage_state/lecture-search/searchable-pdfs')
POLICY = 'tesseract-eng-hocr-native-preserve-v1'


def searchable_pdf(source):
    source = Path(source)
    try:
        relative = source.resolve(strict=True).relative_to(PDF_ROOT.resolve(strict=True))
        if source.is_symlink() or source.suffix.lower() != '.pdf':
            return str(source)
        path = 'PDF Library/' + relative.as_posix()
        directory = CACHE_ROOT / hashlib.sha256(path.encode()).hexdigest()
        manifest = json.loads((directory / 'manifest.json').read_text())
        s = source.stat()
        fp = f'{s.st_dev}:{s.st_ino}:{s.st_size}:{s.st_mtime_ns}'
        derived = directory / 'document.pdf'
        if (manifest.get('path') == path and manifest.get('fingerprint') == fp
                and manifest.get('policy') == POLICY and manifest.get('status') == 'ready'
                and not directory.is_symlink() and not derived.is_symlink() and derived.is_file()):
            return str(derived)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return str(source)
