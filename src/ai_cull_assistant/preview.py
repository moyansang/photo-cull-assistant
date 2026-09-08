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


def build_preview(asset: PhotoAsset, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Separate cache prevents old 640px previews from limiting face detail.
    cache_dir = cache_dir / "v04"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{asset.stem}.jpg"
    if out_path.exists():
        asset.preview_path = out_path
        return out_path

    img = None
    if asset.jpg_path is not None:
        img = _load_standard_image(asset.jpg_path)
    elif asset.primary_path.suffix.lower() in RAW_EXTENSIONS:
        img = _load_raw_preview(asset.primary_path)
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


def _load_standard_image(path: Path) -> Optional[Image.Image]:
    try:
        with Image.open(path) as img:
            return img.copy()
    except Exception:
        return None


def _load_raw_preview(path: Path) -> Optional[Image.Image]:
    if rawpy is None:
        return None
    try:
        with rawpy.imread(str(path)) as raw:
            try:
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    from io import BytesIO

                    return Image.open(BytesIO(thumb.data)).copy()
                if thumb.format == rawpy.ThumbFormat.BITMAP:
                    return Image.fromarray(thumb.data)
            except Exception:
                rgb = raw.postprocess(use_camera_wb=True, half_size=True)
                return Image.fromarray(rgb)
    except Exception:
        return None
    return None
