from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Optional

from PIL import Image, ImageOps

from .models import PhotoAsset, RAW_EXTENSIONS
from .scan_timing import measure
from .scan_diagnostics import note
from . import image_decode
from .shared_decode import preview_image

# Kept as a compatibility hook for integrations testing a rawpy backend.
rawpy = image_decode.rawpy
THUMB_SIZE = (1600, 1600)
PREVIEW_VERSION = 'v05'
_STAMP_PREFIX = b'photo-cull-source-v1:'
_validated_previews = OrderedDict()
_validation_lock = RLock()
_VALIDATED_PREVIEW_LIMIT = 256


def _preview_stamp(asset, legacy_orientation):
    sources = image_decode.asset_source_signature(asset)
    payload = (image_decode.DECODE_VERSION, legacy_orientation, THUMB_SIZE, sources)
    stamp = _STAMP_PREFIX + hashlib.sha256(json.dumps(payload).encode('utf-8')).hexdigest().encode('ascii')
    available = any(signature[1] is not None for _, signature in sources)
    return stamp, available


def _cached_preview_usable(path, stamp, source_available, *, allow_unstamped=False):
    try:
        signature = image_decode.source_signature(path)
        with _validation_lock:
            if signature in _validated_previews:
                comment = _validated_previews[signature]
                _validated_previews.move_to_end(signature)
            else:
                with Image.open(path) as cached:
                    image_decode._check_dimensions(cached.size)
                    # JPEG verify() checks headers only and accepts truncated
                    # entropy data. Load once per cache-file version instead.
                    cached.load()
                    comment = cached.info.get('comment', b'')
                if image_decode.source_signature(path) != signature:
                    return False
                _validated_previews[signature] = comment
                while len(_validated_previews) > _VALIDATED_PREVIEW_LIMIT:
                    _validated_previews.popitem(last=False)
        # An offline source must not make a usable workspace preview disappear.
        # This cache is for display only, never proof for new image analysis.
        if not source_available:
            note('preview_source', 'cached_preview_source_unavailable')
            return True
        if not isinstance(comment, bytes) or not comment.startswith(_STAMP_PREFIX):
            return allow_unstamped
        return comment == stamp
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return False


@measure('preview')
def build_preview(asset: PhotoAsset, cache_dir: Path, *, legacy_orientation: bool = False) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Never rotate existing manual selections when restoring legacy workspaces.
    cache_dir = cache_dir / ('v04' if legacy_orientation else PREVIEW_VERSION)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{asset.stem}.jpg"
    stamp, source_available = _preview_stamp(asset, legacy_orientation)
    if _cached_preview_usable(out_path, stamp, source_available,
                              allow_unstamped=legacy_orientation):
        note('preview_source', 'cached_preview')
        asset.preview_path = out_path
        return out_path

    def load():
        img = None
        raw_path = asset.raw_path or (asset.primary_path if asset.primary_path.suffix.lower() in RAW_EXTENSIONS else None)
        jpg_failed = False
        if asset.jpg_path is not None:
            img = _load_standard_image(asset.jpg_path)
            jpg_failed = img is None
            if img is not None:
                note('preview_source', 'paired_jpeg')
                if raw_path:
                    img.info.get('decode_evidence', {})['source_kind'] = 'paired_jpeg'
        if img is None and raw_path:
            img = _load_raw_preview(raw_path, apply_orientation=not legacy_orientation, strict=True)
            if img is not None and jpg_failed:
                img.info.get('decode_evidence', {}).setdefault('warnings', []).append('paired_jpeg_unreadable')
        elif img is None and asset.jpg_path is None:
            note('preview_source', 'standard_image')
            # Raise the concrete codec error so the scan can show a useful reason.
            img = image_decode.load_standard_image(asset.primary_path)
        if img is None:
            raise image_decode.DecodeError(f"无法读取图片或 RAW 预览：{asset.primary_path.name}")
        return img

    with preview_image(asset, load) as source:
        # Thumbnailing must never shrink the shared native source in place.
        img = ImageOps.exif_transpose(source)
        try:
            img.thumbnail(THUMB_SIZE)
            if img.mode not in ('RGB', 'L'):
                converted = img.convert('RGB')
                img.close()
                img = converted
            # Publish a complete JPEG and its version stamp together. Failed or
            # concurrent writers cannot leave a partially overwritten preview.
            temporary = None
            try:
                with NamedTemporaryFile(dir=cache_dir, prefix=f'.{out_path.name}.',
                                        suffix='.tmp', delete=False) as output:
                    temporary = Path(output.name)
                    img.save(output, 'JPEG', quality=88, comment=stamp)
                if _preview_stamp(asset, legacy_orientation)[0] != stamp:
                    raise image_decode.DecodeError('源文件在生成预览时发生变化；请重试')
                os.replace(temporary, out_path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        finally:
            img.close()
    asset.preview_path = out_path
    return out_path


def ensure_preview(asset: PhotoAsset, preview_dir: Path | None = None) -> Path:
    """Return the saved preview, or recreate it within the active workspace."""
    cache_dir = Path(preview_dir) if preview_dir is not None else None
    existing = Path(asset.preview_path) if asset.preview_path else None
    if existing is not None and existing.is_file():
        eligible = cache_dir is None
        if cache_dir is not None:
            try:
                existing.resolve().relative_to(cache_dir.resolve())
                eligible = True
            except ValueError:
                pass
        if eligible:
            stamp, available = _preview_stamp(asset, existing.parent.name == 'v04')
            # Existing pre-stamp workspaces retain their image/selection layout;
            # regenerated previews opt into source-version validation.
            if _cached_preview_usable(existing, stamp, available, allow_unstamped=True):
                return existing
    if cache_dir is None and existing is not None and existing.parent.name in {'v04', PREVIEW_VERSION}:
        cache_dir = existing.parent.parent
    if cache_dir is None:
        raise ValueError('无法确定预览图缓存目录')
    return build_preview(asset, cache_dir, legacy_orientation=bool(existing and existing.parent.name == 'v04'))


def _load_standard_image(path: Path) -> Optional[Image.Image]:
    try:
        return image_decode.load_standard_image(path)
    except (OSError, ValueError):
        return None


def _orient_raw_thumbnail(image, flip):
    return image_decode.orient_raw_thumbnail(image, flip)


def _load_raw_preview(path: Path, *, apply_orientation: bool = True, strict: bool = False) -> Optional[Image.Image]:
    if rawpy is None:
        if strict:
            raise image_decode.CodecUnavailable("RAW 解码器不可用；请安装 raw 可选依赖")
        return None
    try:
        image = image_decode.load_raw_image(path, apply_orientation=apply_orientation, backend=rawpy)
        note('preview_source', image.info['decode_evidence']['source_kind'])
        return image
    except (OSError, ValueError):
        if strict:
            raise
        return None
