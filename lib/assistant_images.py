"""Bounded image batches shared by the assistant upload and provider paths."""
import io
from contextlib import ExitStack, contextmanager
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGES = 8
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 40 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000

class ImageInputError(ValueError):
    pass

def read_uploads(files):
    files = [file for file in files if file.filename]
    if len(files) > MAX_IMAGES:
        raise OverflowError(f'Attach up to {MAX_IMAGES} images per message.')
    images = []
    total = 0
    for file in files:
        data = file.read(min(MAX_IMAGE_BYTES, MAX_BATCH_BYTES - total) + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise OverflowError('Each image must be under 20 MB.')
        total += len(data)
        if total > MAX_BATCH_BYTES:
            raise OverflowError('Keep the images under 40 MB combined.')
        if not data:
            raise ImageInputError('An attached image is empty. Remove it and try again.')
        images.append(data)
    return images

@contextmanager
def open_images(value):
    images = [value] if isinstance(value, bytes) else value
    if not isinstance(images, list) or not 1 <= len(images) <= MAX_IMAGES:
        raise ImageInputError(f'Attach between 1 and {MAX_IMAGES} images.')
    if any(not isinstance(data, bytes) or len(data) > MAX_IMAGE_BYTES for data in images) or sum(map(len, images)) > MAX_BATCH_BYTES:
        raise ImageInputError('The image batch exceeds the upload limits.')
    with ExitStack() as stack:
        prepared = []
        for index, data in enumerate(images, 1):
            try:
                image = Image.open(io.BytesIO(data));stack.callback(image.close)
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ImageInputError(f'Image {index} is too large. Use a smaller copy.')
                # Decode one at a time; retain only bounded provider previews.
                image.draft('RGB', (2048, 2048))
                ImageOps.exif_transpose(image, in_place=True)
                image.thumbnail((2048, 2048))
                image.load()
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
                raise ImageInputError(f'Image {index} could not be read. Try a PNG or JPEG copy.') from exc
            prepared.append(image)
        yield prepared

def browser_preview(data):
    """Only for formats a browser cannot preview; no file or image-store write."""
    with open_images(data) as images:
        image = images[0]
        alpha = 'A' in image.getbands() or 'transparency' in image.info
        with image.convert('RGBA' if alpha else 'RGB') as preview:
            output = io.BytesIO()
            preview.save(output, format='PNG' if alpha else 'JPEG', quality=85)
            return output.getvalue(), 'image/png' if alpha else 'image/jpeg'
