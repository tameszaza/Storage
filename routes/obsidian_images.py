"""Tailnet Obsidian images: scoped uploads, browser-safe image conversion."""
import hashlib
import hmac
import io
import os
import re
import tempfile
from pathlib import Path
from flask import abort, jsonify, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener(thumbnails=False)

ROOT = Path('/srv/tamestorage/uploads/Admin/Obsidian/Attachments')
KEY = Path('/srv/tamestorage/.tamestorage_state/obsidian-image-upload.key')
BASE = 'https://server.tailb7f57c.ts.net/obsidian-images/'
LIMIT = 20 * 1024 * 1024
# Keep legacy BMP/TIFF URLs readable. New uploads of these formats become PNG.
FORMATS = {'PNG': ('png','image/png'), 'JPEG': ('jpg','image/jpeg'), 'GIF': ('gif','image/gif'), 'WEBP': ('webp','image/webp'), 'AVIF': ('avif','image/avif'), 'BMP': ('bmp','image/bmp'), 'TIFF': ('tiff','image/tiff')}
ORIGINAL_FORMATS = {'PNG', 'JPEG', 'MPO', 'GIF', 'WEBP', 'AVIF'}
CONVERT_FORMATS = {'HEIF', 'BMP', 'DIB', 'TIFF', 'ICO', 'ICNS', 'JPEG2000', 'PSD', 'TGA', 'PPM', 'QOI', 'PCX', 'SGI'}
VALID = re.compile(r'^[0-9a-f]{64}\.(png|jpg|gif|webp|avif|bmp|tiff)$')

def browser_image(data):
    """Preserve browser images byte-for-byte; convert other supported rasters."""
    with Image.open(io.BytesIO(data)) as image:
        fmt = image.format
        if fmt in ORIGINAL_FORMATS:
            image.verify()
            # iPhone HDR JPEGs can contain an MPF gain-map image. Pillow calls
            # these MPO, but browsers still read the original JPEG correctly.
            return data, FORMATS['JPEG' if fmt == 'MPO' else fmt][0], False
        if fmt not in CONVERT_FORMATS:
            raise ValueError('Unsupported raster image')
        oriented = ImageOps.exif_transpose(image)
        alpha = 'A' in oriented.getbands() or 'transparency' in oriented.info
        converted = oriented.convert('RGBA' if alpha else 'RGB')
        output = io.BytesIO()
        options = {}
        if image.info.get('icc_profile'):
            options['icc_profile'] = image.info['icc_profile']
        # HEIC photographs stay compact as JPEG. Lossless PNG retains diagrams,
        # scanned images and transparency in other pasted raster formats.
        if fmt == 'HEIF' and not alpha:
            converted.save(output, format='JPEG', quality=92, **options)
            extension = 'jpg'
        else:
            converted.save(output, format='PNG', **options)
            extension = 'png'
            if output.tell() > LIMIT and not alpha:
                output = io.BytesIO()
                converted.save(output, format='JPEG', quality=92, **options)
                extension = 'jpg'
        result = output.getvalue()
        if len(result) > LIMIT:
            raise OverflowError('Converted image exceeds upload limit')
        return result, extension, True

def upload():
    supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
    try:
        valid = hmac.compare_digest(supplied, KEY.read_text().strip())
    except OSError:
        abort(503)
    if not valid:
        abort(401)
    if request.content_length is not None and request.content_length > LIMIT:
        abort(413)
    source = request.stream.read(LIMIT + 1)
    if not source or len(source) > LIMIT:
        abort(413)
    try:
        metadata = store_image(source)
    except OverflowError:
        abort(413)
    except (UnidentifiedImageError, OSError, ValueError, KeyError, EOFError, SyntaxError, Image.DecompressionBombError):
        abort(415)
    except RuntimeError:
        abort(503)
    response = jsonify(**metadata)
    response.headers['Cache-Control'] = 'no-store'
    return response


def store_image(source):
    """Reuse validated, deduplicated image storage for an authenticated caller."""
    if not source or len(source) > LIMIT:
        raise OverflowError("Image exceeds upload limit")
    data, extension, converted = browser_image(source)
    # No implicit mkdir: offline NAS storage must not fall back to internal disk.
    if not ROOT.is_dir():
        raise RuntimeError("Image storage is unavailable")
    digest = hashlib.sha256(data).hexdigest()
    name = digest + '.' + extension
    target = ROOT / name
    if target.is_symlink():
        raise RuntimeError("Invalid stored image")
    if not target.exists():
        fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=ROOT)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return dict(url=BASE + name, sha256=digest, sourceSha256=hashlib.sha256(source).hexdigest(), bytes=len(data), converted=converted)

def raw(name):
    if not VALID.fullmatch(name):
        abort(404)
    target = ROOT / name
    if not target.is_file() or target.is_symlink():
        abort(404)
    mime = next(v[1] for v in FORMATS.values() if v[0] == name.rsplit('.',1)[1])
    response = send_file(target, mimetype=mime, conditional=True)
    response.headers['Cache-Control'] = 'private, no-store, max-age=0'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

def register_routes(app):
    app.add_url_rule('/obsidian-images/upload', 'obsidian_image_upload', upload, methods=['POST'])
    app.add_url_rule('/obsidian-images/<name>', 'obsidian_image_raw', raw)
