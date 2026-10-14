from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ExifTags

from .image_decode import register_image_codecs

try:
    import exifread  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    exifread = None

PIL_EXIF_NAME_TO_ID = {v: k for k, v in ExifTags.TAGS.items()}


def _parse_datetime(base: str, subsec: Optional[str] = None) -> Optional[datetime]:
    base = str(base).strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(base, fmt)
            if subsec:
                digits = "".join(ch for ch in str(subsec) if ch.isdigit())
                if digits:
                    micros = int((digits + "000000")[:6])
                    dt = dt.replace(microsecond=micros)
            return dt
        except ValueError:
            continue
    return None


def get_capture_time(path: Path) -> datetime:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".heic", ".heif", ".avif"}:
        dt = _capture_time_from_pillow(path)
        if dt is not None:
            return dt
    if exifread is not None:
        dt = _capture_time_from_exifread(path)
        if dt is not None:
            return dt
    return datetime.fromtimestamp(path.stat().st_mtime)


def _capture_time_from_pillow(path: Path) -> Optional[datetime]:
    try:
        register_image_codecs()
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            # DateTimeOriginal lives in the nested EXIF IFD on many HEIF/TIFF files.
            try:
                nested = exif.get_ifd(34665)
            except (KeyError, OSError, ValueError, TypeError):
                nested = {}
            dt_val = nested.get(36867) or exif.get(36867) or exif.get(306)
            subsec = nested.get(37521) or exif.get(37521) or nested.get(37520) or exif.get(37520)
            if dt_val:
                return _parse_datetime(str(dt_val), str(subsec) if subsec else None)
    except Exception:
        return None
    return None


def _capture_time_from_exifread(path: Path) -> Optional[datetime]:
    try:
        with path.open("rb") as fh:
            tags = exifread.process_file(fh, details=False)
        dt_val = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
        subsec = tags.get("EXIF SubSecTimeOriginal") or tags.get("EXIF SubSecTime")
        if dt_val:
            return _parse_datetime(str(dt_val), str(subsec) if subsec else None)
    except Exception:
        return None
    return None
