from __future__ import annotations

import shutil
from pathlib import Path

from .models import PhotoAsset


def copy_selected(assets: list[PhotoAsset], stem_to_rating: dict[str, int], out_dir: str | Path, min_rating: int = 4) -> int:
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    copied = 0
    for asset in assets:
        rating = stem_to_rating.get(asset.stem)
        if rating is None or rating < min_rating:
            continue
        subfolder = output / f"{rating}_star"
        subfolder.mkdir(parents=True, exist_ok=True)
        for path in asset.rating_target_paths:
            if path.exists():
                shutil.copy2(path, subfolder / path.name)
                copied += 1
    return copied
