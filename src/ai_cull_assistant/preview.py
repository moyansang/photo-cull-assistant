from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from .models import PhotoAsset, RAW_EXTENSIONS

try:
    import rawpy  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    rawpy = None


THUMB_SIZE = (1600, 1600)
PREVIEW_VERSION = 'v05'


def build_preview(asset: PhotoAsset, cache_dir: Path, *, legacy_orientation: bool = False) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Keep old manual selections in their original coordinate system. New
    # scans use RAW-oriented previews; reopening an old session never rotates it.
    cache_dir = cache_dir / ('v04' if legacy_orientation else PREVIEW_VERSION)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{asset.stem}.jpg"
    if out_path.exists():
        asset.preview_path = out_path
        return out_path

    img = None
    if asset.jpg_path is not None:
        img = _load_standard_image(asset.jpg_path)
    elif asset.primary_path.suffix.lower() in RAW_EXTENSIONS:
        img = _load_raw_preview(asset.primary_path, apply_orientation=not legacy_orientation)
    else:
        img = _load_standard_image(asset.primary_path)

    if img is None:
        raise RuntimeError(f"Unable to build preview for {asset.primary_path}")

    img = ImageOps.exif_transpose(img)
    img.thumbnail(THUMB_SIZE)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.save(out_path, "JPEG", quality=88)
    asset.preview_path = out_path
    return out_path


def ensure_preview(asset: PhotoAsset, preview_dir: Path | None = None) -> Path:
    """Return an existing preview or recreate one from the original on demand.

    Archived sessions retain the intended preview path.  This lets callers use
    the helper with only an asset while still keeping regenerated files inside
    the workspace-owned preview directory.
    """
    existing = Path(asset.preview_path) if asset.preview_path else None
    if existing is not None and existing.is_file():
        return existing
    cache_dir = Path(preview_dir) if preview_dir is not None else None
    if cache_dir is None and existing is not None and existing.parent.name in {"v04", PREVIEW_VERSION}:
        cache_dir = existing.parent.parent
    if cache_dir is None:
        raise ValueError("无法确定预览图缓存目录")
    return build_preview(asset, cache_dir, legacy_orientation=bool(existing and existing.parent.name == 'v04'))


def _load_standard_image(path: Path) -> Optional[Image.Image]:
    try:
        with Image.open(path) as img:
            return img.copy()
    except Exception:
        return None


def _orient_raw_thumbnail(image, flip):
    # Embedded JPEG orientation, when present, describes the thumbnail itself.
    # Do not rotate twice. LibRaw flip values differ from EXIF orientation values.
    if image.getexif().get(274) in range(1, 9):
        return ImageOps.exif_transpose(image)
    transform = {3: Image.Transpose.ROTATE_180,
                 5: Image.Transpose.ROTATE_90,
                 6: Image.Transpose.ROTATE_270}.get(flip)
    return image.transpose(transform) if transform is not None else image


def _load_raw_preview(path: Path, *, apply_orientation: bool = True) -> Optional[Image.Image]:
    if rawpy is None:
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    from io import BytesIO

                    with Image.open(BytesIO(thumb.data)) as decoded:
                        image = decoded.copy()
                    return _orient_raw_thumbnail(image, raw.sizes.flip) if apply_orientation else image
                if thumb.format == rawpy.ThumbFormat.BITMAP:
                    image = Image.fromarray(thumb.data)
                    return _orient_raw_thumbnail(image, raw.sizes.flip) if apply_orientation else image
            except Exception:
                rgb = raw.postprocess(use_camera_wb=True, half_size=True)
                return Image.fromarray(rgb)
    except Exception:
        return None
    return None
