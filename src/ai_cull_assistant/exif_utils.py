from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ExifTags

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
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}:
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
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            dt_val = exif.get(PIL_EXIF_NAME_TO_ID.get("DateTimeOriginal")) or exif.get(PIL_EXIF_NAME_TO_ID.get("DateTime"))
            subsec = exif.get(PIL_EXIF_NAME_TO_ID.get("SubsecTimeOriginal")) or exif.get(PIL_EXIF_NAME_TO_ID.get("SubsecTime"))
            if dt_val:
                return _parse_datetime(str(dt_val), str(subsec) if subsec else None)
    except Exception:
        return None
    return None


def _capture_time_from_exifread(path: Path) -> Optional[datetime]:
    try:
        with path.open("rb") as fh:
            tags = exifread.process_file(fh, stop_tag="EXIF DateTimeOriginal", details=False)
        dt_val = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
        subsec = tags.get("EXIF SubSecTimeOriginal") or tags.get("EXIF SubSecTime")
        if dt_val:
            return _parse_datetime(str(dt_val), str(subsec) if subsec else None)
    except Exception:
        return None
    return None
