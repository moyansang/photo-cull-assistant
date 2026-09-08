from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .exif_utils import get_capture_time
from .models import IMAGE_EXTENSIONS, JPEG_EXTENSIONS, RAW_EXTENSIONS, PhotoAsset


def iter_image_files(folder: Path) -> Iterable[Path]:
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            yield path


def scan_folder(folder: str | Path) -> list[PhotoAsset]:
    folder_path = Path(folder)
    stems: dict[str, dict[str, Path]] = defaultdict(dict)
    for path in iter_image_files(folder_path):
        stems[path.stem][path.suffix.lower()] = path

    assets: list[PhotoAsset] = []
    for stem, mapping in stems.items():
        raw_path = next((p for ext, p in mapping.items() if ext in RAW_EXTENSIONS), None)
        jpg_path = next((p for ext, p in mapping.items() if ext in JPEG_EXTENSIONS), None)
        primary_path = raw_path or jpg_path or next(iter(mapping.values()))
        display_path = jpg_path or raw_path or primary_path
        captured_at = get_capture_time(primary_path)
        asset = PhotoAsset(
            stem=stem,
            display_path=display_path,
            primary_path=primary_path,
            raw_path=raw_path,
            jpg_path=jpg_path,
            captured_at=captured_at,
            ext=primary_path.suffix.lower(),
        )
        assets.append(asset)

    assets.sort(key=lambda a: (a.captured_at, a.stem))
    return assets
