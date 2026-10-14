from __future__ import annotations

from collections import defaultdict, Counter
import hashlib
from pathlib import Path
from typing import Iterable

from .exif_utils import get_capture_time
from .models import IMAGE_EXTENSIONS, JPEG_EXTENSIONS, RAW_EXTENSIONS, PhotoAsset


def iter_image_files(folder: Path) -> Iterable[Path]:
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            yield path


def scan_folder(folder: str | Path) -> list[PhotoAsset]:
    """Pair RAW/JPEG only in the same directory; never drop format siblings.

    Display stems remain unchanged for the normal one-photo-per-name case.
    Collisions get a portable relative-path suffix; durable photo IDs continue
    to derive from the primary source path, never from this display label.
    """
    folder_path = Path(folder)
    pairs = defaultdict(list)
    for path in iter_image_files(folder_path):
        pairs[(str(path.parent), path.stem.casefold())].append(path)
    records = []
    for paths in pairs.values():
        raws = sorted(p for p in paths if p.suffix.lower() in RAW_EXTENSIONS)
        jpgs = sorted(p for p in paths if p.suffix.lower() in JPEG_EXTENSIONS)
        used = set()
        if raws and jpgs:
            records.append((raws[0], jpgs[0], raws[0]))
            used.update((raws[0], jpgs[0]))
        for path in paths:
            if path not in used:
                records.append((path if path.suffix.lower() in RAW_EXTENSIONS else None,
                                path if path.suffix.lower() in JPEG_EXTENSIONS else None, path))
    counts = Counter(primary.stem.casefold() for _, _, primary in records)
    assets = []
    for raw_path, jpg_path, primary_path in records:
        stem = primary_path.stem
        if counts[stem.casefold()] > 1:
            relative = primary_path.relative_to(folder_path).as_posix()
            stem += "__" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:10]
        assets.append(PhotoAsset(
            stem=stem, display_path=jpg_path or raw_path or primary_path,
            primary_path=primary_path, raw_path=raw_path, jpg_path=jpg_path,
            captured_at=get_capture_time(primary_path), ext=primary_path.suffix.lower(),
        ))
    assets.sort(key=lambda a: (a.captured_at, a.stem, str(a.primary_path)))
    return assets
