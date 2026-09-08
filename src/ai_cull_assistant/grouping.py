from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps

from .models import PhotoAsset
from .subject import SubjectFeatures, features


@dataclass(slots=True)
class GroupingConfig:
    strong_same_group_seconds: float = 0.8
    maybe_same_group_seconds: float = 2.5
    hash_distance_threshold: int = 10


PRESETS = {
    "strict": GroupingConfig(0.5, 1.5, 8),
    "standard": GroupingConfig(0.8, 2.5, 10),
    "loose": GroupingConfig(1.2, 4.0, 12),
}


def assign_groups(assets: list[PhotoAsset], preset: str = "standard") -> list[PhotoAsset]:
    cfg = PRESETS[preset]
    if not assets:
        return assets

    current_group = 1
    assets[0].group_id = current_group
    anchor = assets[0]
    visual = {id(a): features(Path(a.preview_path)) if a.preview_path else None for a in assets}
    for prev, curr in zip(assets, assets[1:]):
        delta = (curr.captured_at - prev.captured_at).total_seconds()
        same_group = False
        if 0 <= delta <= cfg.maybe_same_group_seconds:
            short = delta <= cfg.strong_same_group_seconds
            same_group = _similar(prev, curr, visual, cfg, short=short)
            # Compare against the group's first photo to prevent gradual pose drift.
            if same_group and anchor is not prev:
                same_group = _similar(anchor, curr, visual, cfg, short=short)

        if not same_group:
            current_group += 1
            anchor = curr
        curr.group_id = current_group

    return assets


def _similar(a: PhotoAsset, b: PhotoAsset, visual: dict, cfg: GroupingConfig, *, short: bool) -> bool:
    left: SubjectFeatures | None = visual[id(a)]
    right: SubjectFeatures | None = visual[id(b)]
    threshold = cfg.hash_distance_threshold + (3 if short else 0)
    if left and right:
        if left.face and right.face and left.body and right.body:
            lx, ly, lw, lh = left.face
            rx, ry, rw, rh = right.face
            displacement = ((lx + lw / 2 - rx - rw / 2) ** 2 + (ly + lh / 2 - ry - rh / 2) ** 2) ** .5
            size_ratio = max(lw * lh, rw * rh) / max(1e-9, min(lw * lh, rw * rh))
            return (displacement <= .10 and size_ratio <= 1.8
                    and hamming_distance(left.body, right.body) <= threshold
                    and hamming_distance(left.whole, right.whole) <= threshold + 8)
        # Missing detections are not evidence of a pose change. Use both views.
        return (hamming_distance(left.whole, right.whole) <= threshold
                and hamming_distance(left.center, right.center) <= threshold)
    ah, bh = _ensure_dhash(a), _ensure_dhash(b)
    if ah is not None and bh is not None:
        return hamming_distance(ah, bh) <= threshold
    return short


def _ensure_dhash(asset: PhotoAsset) -> str | None:
    if asset.dhash:
        return asset.dhash
    if not asset.preview_path or not Path(asset.preview_path).exists():
        return None
    try:
        asset.dhash = dhash(Path(asset.preview_path))
        return asset.dhash
    except Exception:
        return None


def dhash(path: Path, hash_size: int = 8) -> str:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("L").resize((hash_size + 1, hash_size))
        pixels = list(img.getdata())
    rows = []
    width = hash_size + 1
    for row in range(hash_size):
        row_bits = []
        for col in range(hash_size):
            left = pixels[row * width + col]
            right = pixels[row * width + col + 1]
            row_bits.append("1" if left > right else "0")
        rows.append("".join(row_bits))
    bits = "".join(rows)
    return f"{int(bits, 2):0{hash_size * hash_size // 4}x}"


def hamming_distance(hash_a: str, hash_b: str) -> int:
    a = int(hash_a, 16)
    b = int(hash_b, 16)
    return (a ^ b).bit_count()


def group_summary(assets: Iterable[PhotoAsset]) -> list[tuple[int, list[str]]]:
    summary: dict[int, list[str]] = {}
    for asset in assets:
        summary.setdefault(asset.group_id, []).append(asset.stem)
    return sorted(summary.items())


def renumber_groups(assets: list[PhotoAsset]) -> list[PhotoAsset]:
    """Renumber group ids sequentially while preserving asset order and group boundaries."""
    if not assets:
        return assets
    mapping: dict[int, int] = {}
    next_id = 1
    for asset in assets:
        old = asset.group_id
        if old not in mapping:
            mapping[old] = next_id
            next_id += 1
        asset.group_id = mapping[old]
    return assets


def split_group_at(assets: list[PhotoAsset], stem: str) -> bool:
    """Split the selected photo and following photos in its group into a new group."""
    index = next((i for i, asset in enumerate(assets) if asset.stem == stem), None)
    if index is None:
        return False
    selected = assets[index]
    group_id = selected.group_id
    group_indices = [i for i, asset in enumerate(assets) if asset.group_id == group_id]
    if not group_indices or index == group_indices[0]:
        return False
    new_group_id = max((a.group_id for a in assets), default=0) + 1
    for i in group_indices:
        if i >= index:
            assets[i].group_id = new_group_id
    renumber_groups(assets)
    return True


def merge_adjacent_groups(assets: list[PhotoAsset], first_group_id: int, second_group_id: int) -> bool:
    """Merge two adjacent groups. Returns False when the ids are not adjacent."""
    if abs(first_group_id - second_group_id) != 1:
        return False
    present = {a.group_id for a in assets}
    if first_group_id not in present or second_group_id not in present:
        return False
    target = min(first_group_id, second_group_id)
    source = max(first_group_id, second_group_id)
    for asset in assets:
        if asset.group_id == source:
            asset.group_id = target
    renumber_groups(assets)
    return True
