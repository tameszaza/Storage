"""Cached, invisible OCR layers. Originals are read-only; imports stay lazy."""
import hashlib
import base64,json
import os
import re
import sys
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

DATA = Path('/srv/tamestorage/.tamestorage_state/lecture-search')
ROOT = DATA / 'searchable-pdfs'
POLICY = 'tesseract-eng-hocr-native-preserve-v1'
METHODS = ('ocr', 'ocr-empty', 'native+ocr', 'native+ocr-empty', 'native-image')


def fingerprint(file):
    s = file.stat()
    return f'{s.st_dev}:{s.st_ino}:{s.st_size}:{s.st_mtime_ns}'


def folder(path):
    if not path.startswith('PDF Library/') or any(p in ('', '.', '..') or p.startswith('.') for p in path.split('/')):
        raise ValueError('Invalid library PDF path')
    return ROOT / hashlib.sha256(path.encode()).hexdigest()


def read_json(file):
    try:
        return json.loads(file.read_text())
    except (OSError, ValueError):
        return {}


def atomic_json(file, data):
    temporary = file.with_suffix('.tmp')
    temporary.write_text(json.dumps(data))
    os.replace(temporary, file)


def prepare(path, fp):
    directory = folder(path)
    wanted = {'path': path, 'fingerprint': fp, 'policy': POLICY}
    if read_json(directory / 'source.json') != wanted:
        if directory.is_symlink():
            raise ValueError('Invalid PDF cache directory')
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, mode=0o700)
        atomic_json(directory / 'source.json', wanted)
    return directory


def queue(db, path, file, fp):
    """Use the existing durable OCR queue, including one-time geometry backfill."""
    manifest = read_json(folder(path) / 'manifest.json')
    if manifest.get('fingerprint') == fp and manifest.get('policy') == POLICY:
        if manifest.get('status') != 'ready' or (folder(path) / 'document.pdf').is_file():
            return False
    candidates = db.execute("SELECT 1 FROM pages WHERE path=? AND (method IN ('ocr','ocr-empty','native+ocr','native+ocr-empty','native-image') OR (method='native' AND trim(text)='')) LIMIT 1", (path,)).fetchone()
    if not candidates:
        return False
    prepare(path, fp)
    db.execute('INSERT OR REPLACE INTO ocr_jobs VALUES(?,?)', (path, fp))
    return True


def run(args, timeout):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=timeout)


def recognize_page(file, path, fp, number, mixed):
    """Tesseract produces txt and text-only PDF in the SAME recognition pass."""
    if fingerprint(file) != fp:
        raise RuntimeError('PDF changed before OCR')
    directory = prepare(path, fp)
    checkpoint = directory / f'{number}.json'
    done = read_json(checkpoint)
    layer = directory / f'{number}.pdf'
    if done.get('status') == 'empty' or (done.get('status') == 'ready' and layer.is_file()):
        return done.get('text', '')
    if done.get('status') == 'error':
        raise RuntimeError('Cached PDF OCR page failure; explicit retry required')
    try:import helper_client
    except ImportError:helper_client=None
    if helper_client and helper_client.enabled():
        result=helper_client.pdf_page(file,path,fp,number,mixed)
        if fingerprint(file)!=fp:raise RuntimeError('PDF changed during OCR')
        text=str(result['text'])[:2000000]
        if text.strip():
            raw=base64.b64decode(result['layer'],validate=True)
            if not raw.startswith(b'%PDF-') or len(raw)>12*1024*1024:raise ValueError('Invalid OCR layer')
            temporary=layer.with_suffix('.next');temporary.write_bytes(raw);os.replace(temporary,layer)
        atomic_json(checkpoint,{'status':'ready' if text.strip() else 'empty','text':text})
        return text
    import pikepdf
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix='ocr-', dir=DATA) as temporary:
        base = Path(temporary) / 'page'
        raster = base.with_suffix('.png')
        run(['/usr/bin/pdftoppm', '-f', str(number), '-l', str(number), '-singlefile',
             '-cropbox', '-scale-to', '3600' if mixed else '2400', '-png', str(file), str(base)], 180)
        run(['/usr/bin/tesseract', str(raster), str(base), '-l', 'eng', '--oem', '1',
             '--psm', '11' if mixed else '3', '-c', 'textonly_pdf=1', 'txt', 'pdf', 'hocr'], 120)
        text = base.with_suffix('.txt').read_text()[:2000000]
        if mixed:
            # Recognize the unmodified image once. Suppress only OCR words whose
            # locations already have native/Goodnotes text, AFTER recognition.
            # Removing image pixels before recognition changes page segmentation.
            bbox = base.with_suffix('.html')
            run(['/usr/bin/pdftotext', '-f', str(number), '-l', str(number),
                 '-cropbox', '-bbox', str(file), str(bbox)], 60)
            native = ET.parse(bbox)
            ns = {'h': 'http://www.w3.org/1999/xhtml'}
            with pikepdf.open(file) as pdf:
                source = pdf.pages[number - 1]
                box = list(map(float, source.cropbox))
                width, height = box[2] - box[0], box[3] - box[1]
                if int(source.obj.get('/Rotate', 0)) % 180:
                    width, height = height, width
            with Image.open(raster) as image:
                sx, sy = image.width / width, image.height / height
            rectangles = [(float(w.get('xMin')) * sx, float(w.get('yMin')) * sy,
                           float(w.get('xMax')) * sx, float(w.get('yMax')) * sy)
                          for w in native.findall('.//h:word', ns)]
            hocr = ET.parse(base.with_suffix('.hocr'))
            for parent in hocr.iter():
                for word in list(parent):
                    if 'ocrx_word' not in word.get('class', '').split():
                        continue
                    match = re.search(r'bbox (\d+) (\d+) (\d+) (\d+)', word.get('title', ''))
                    if not match:
                        continue
                    x0, y0, x1, y1 = map(float, match.groups())
                    area = max(1, (x1 - x0) * (y1 - y0))
                    if any(max(0, min(x1, b[2]) - max(x0, b[0])) *
                           max(0, min(y1, b[3]) - max(y0, b[1])) / area > 0.5
                           for b in rectangles):
                        parent.remove(word)
            lines = []
            for line in hocr.iter():
                if 'ocr_line' in line.get('class', '').split():
                    words = [''.join(w.itertext()).strip() for w in line.iter()
                             if 'ocrx_word' in w.get('class', '').split()]
                    if words:
                        lines.append(' '.join(words))
            text = '\n'.join(lines)[:2000000]
            if text.strip():
                hocr.write(base.with_suffix('.hocr'), encoding='utf-8', xml_declaration=True)
                run([sys.executable, str(Path(__file__).resolve()), 'render',
                     str(base.with_suffix('.hocr')), str(base.with_suffix('.pdf'))], 90)
        if fingerprint(file) != fp:
            raise RuntimeError('PDF changed during OCR')
        if text.strip():
            os.replace(base.with_suffix('.pdf'), layer)
        atomic_json(checkpoint, {'status': 'ready' if text.strip() else 'empty', 'text': text})
        return text


def assemble(file, path, fp, numbers):
    """pikepdf/QPDF grafts Tesseract layers without rasterizing source content."""
    import pikepdf

    directory = prepare(path, fp)
    output = directory / 'document.pdf'
    temporary = directory / 'document.tmp'
    try:
        with pikepdf.open(file) as pdf:
            def signed(fields):
                return any(str(f.get('/FT')) == '/Sig' or signed(f.get('/Kids', [])) for f in fields)
            fields = pdf.Root.get('/AcroForm', {}).get('/Fields', [])
            if pdf.is_encrypted or '/Perms' in pdf.Root or signed(fields):
                raise ValueError('Protected/signed PDF left unchanged')
            for number in numbers:
                with pikepdf.open(directory / f'{number}.pdf') as layer:
                    page = pdf.pages[number - 1]
                    # Page.add_overlay uses QPDF's native page transformation,
                    # including /Rotate and nonzero crop origins.
                    page.add_overlay(layer.pages[0], pikepdf.Rectangle(page.cropbox))
            pdf.save(temporary, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.preserve)
        if fingerprint(file) != fp:
            raise RuntimeError('PDF changed during assembly')
        with pikepdf.open(temporary) as check, pikepdf.open(file) as original:
            if len(check.pages) != len(original.pages):
                raise RuntimeError('PDF page count changed')
            for a, b in zip(original.pages, check.pages):
                for key in ('/MediaBox', '/CropBox', '/Rotate', '/UserUnit'):
                    if str(a.obj.get(key)) != str(b.obj.get(key)):
                        raise RuntimeError('PDF page geometry changed')
                if len(a.obj.get('/Annots', [])) != len(b.obj.get('/Annots', [])):
                    raise RuntimeError('PDF annotations changed')
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def build(db, path, file, fp):
    """Per-page resumable work; no OCR/model loads while reading finished PDFs."""
    directory = prepare(path, fp)
    manifest = read_json(directory / 'manifest.json')
    if manifest.get('fingerprint') == fp and manifest.get('policy') == POLICY:
        if manifest.get('status') != 'ready' or (directory / 'document.pdf').is_file():
            return True
    rows = list(db.execute("SELECT page,method FROM pages WHERE path=? AND method IN ('ocr','ocr-empty','native+ocr','native+ocr-empty','native-image') ORDER BY page", (path,)))
    errors = []
    for number, method in rows:
        if fingerprint(file) != fp:
            return False
        try:
            recognize_page(file, path, fp, number, mixed=method.startswith('native'))
        except Exception as error:
            if error.__class__.__name__=="Paused":raise
            if fingerprint(file) != fp:
                return False
            atomic_json(directory / f'{number}.json', {'status': 'error', 'error': type(error).__name__})
            errors.append(number)
        print(json.dumps({'pdf_text_layer_page': number, 'path': path}), flush=True)
        if path.startswith('PDF Library/Books/') and db.execute("SELECT 1 FROM ocr_jobs WHERE path NOT LIKE 'PDF Library/Books/%' LIMIT 1").fetchone():
            return False
    numbers = [n for n, _ in rows if (directory / f'{n}.pdf').is_file()]
    status = 'ready' if numbers else 'error' if errors else 'no-additional-text'
    if numbers:
        try:
            assemble(file, path, fp, numbers)
        except Exception as error:
            if error.__class__.__name__=="Paused":raise
            if fingerprint(file) != fp:
                return False
            status = 'error'
            print(json.dumps({'pdf_text_layer_error': type(error).__name__, 'path': path}), flush=True)
    if fingerprint(file) != fp:
        return False
    atomic_json(directory / 'manifest.json', {'path': path, 'fingerprint': fp,
                'policy': POLICY, 'status': status, 'addedPages': len(numbers), 'failedPages': errors})
    # Only the final derived PDF and its tiny manifests are retained.
    for item in directory.iterdir():
        if item.stem.isdigit() and item.suffix in ('.pdf', '.json'):
            item.unlink()
    print(json.dumps({'pdf_text_layer_complete': path, 'status': status, 'addedPages': len(numbers)}), flush=True)
    return True


def prune(valid_paths):
    if not ROOT.is_dir():
        return
    keep = {folder(path).name for path in valid_paths if path.startswith('PDF Library/')}
    for directory in ROOT.iterdir():
        if directory.name not in keep and len(directory.name) == 64 and not directory.is_symlink():
            shutil.rmtree(directory)


def summary():
    result = {'ready': 0, 'pending': 0, 'failed': 0, 'unchanged': 0}
    if ROOT.is_dir():
        for file in ROOT.glob('*/source.json'):
            manifest = read_json(file.with_name('manifest.json'))
            key = {'ready': 'ready', 'error': 'failed', 'no-additional-text': 'unchanged'}.get(manifest.get('status'), 'pending')
            result[key] += 1
    return result


def render_hocr(hocr, output):
    # Published OCRmyPDF renderer, isolated so its imports/font state are released
    # before embedding or assembling another PDF. No CLI Ghostscript/PDF-A pass.
    import ocrmypdf
    from ocrmypdf.font import MultiFontManager
    from ocrmypdf.fpdf_renderer import Fpdf2PdfRenderer
    from ocrmypdf.hocrtransform.hocr_parser import HocrParser
    page = HocrParser(Path(hocr)).parse()
    fonts = MultiFontManager(Path(ocrmypdf.__file__).parent / 'data')
    renderer = Fpdf2PdfRenderer(page=page, dpi=page.dpi or 300,
                               multi_font_manager=fonts, invisible_text=True)
    renderer.render(Path(output))


if __name__ == '__main__' and sys.argv[1] == 'render':
    render_hocr(sys.argv[2], sys.argv[3])
