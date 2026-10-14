"""Lossless, disposable thumbnails shared by focus preparation and sheets."""
import hashlib
from pathlib import Path
import uuid

from PIL import Image, ImageOps


def thumbnail(asset, image, size):
    preview = Path(asset.preview_path)
    stat = preview.stat()
    key = hashlib.sha256(repr((preview.name, stat.st_size, stat.st_mtime_ns,
                              size, 'sheet-thumb-v1')).encode()).hexdigest()
    path = preview.parent / '.sheet-thumbs' / (key + '.png')
    try:
        with Image.open(path) as cached:
            cached.load()
            return cached.convert('RGB')
    except (OSError, ValueError):
        pass
    result = ImageOps.contain(image, size)
    temporary = path.with_name(key + '.' + uuid.uuid4().hex + '.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        result.save(temporary, format='PNG')
        temporary.replace(path)
    except OSError:
        # Cache writes are optional; a read-only disk must not block rendering.
        pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return result


def prepare_thumbnails(asset):
    from .contact_sheet import THUMB_BOX
    with Image.open(asset.preview_path) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
    try:
        for size in ((330, THUMB_BOX[1]), THUMB_BOX):
            thumbnail(asset, image, size).close()
    finally:
        image.close()
