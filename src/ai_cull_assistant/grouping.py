from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field, replace
import hashlib
from io import BytesIO
import os
from pathlib import Path
from threading import Lock
from typing import Collection

import numpy as np
from PIL import Image, ImageOps

from .models import PhotoAsset, RAW_EXTENSIONS, source_identity_path

# Stored for diagnostics, not used to invalidate or silently regroup old sessions.
GROUPING_VERSION = "workspace-dhash64-block-ssim-crop-v2"
GRAY_SIZE = 64
BLOCK_SIZE = 8
# One-sided central alignment only; at least 81% of the source area remains.
_CROP_FRACTIONS = (.025, .05)
_CROP_PREVIEW_EDGE = 512
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2


@dataclass(frozen=True, slots=True)
class GroupingConfig:
    hash_distance_threshold: int = 10
    structure_threshold: float = 0.78
    max_aspect_ratio: float = 1.15


PRESETS = {
    "strict": GroupingConfig(8, 0.86, 1.08),
    "standard": GroupingConfig(10, 0.78, 1.15),
    "loose": GroupingConfig(12, 0.68, 1.25),
}


@dataclass(slots=True)
class GroupingStats:
    """Optional diagnostics; counts actual comparisons, never a photo quality score."""
    photos: int = 0
    representatives: int = 0
    hash_candidates: int = 0
    structure_comparisons: int = 0
    missing_visuals: int = 0
    crop_candidates: int = 0
    crop_matches: int = 0


@dataclass(frozen=True, slots=True)
class VisualFeatures:
    whole: int
    aspect: float
    blocks: np.ndarray
    means: np.ndarray
    variances: np.ndarray
    crop_variants: tuple["VisualFeatures", ...] = ()


def _image_dhash(image: Image.Image, hash_size: int = 8) -> str:
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray)
    value = 0
    for bit in (pixels[:, :-1] > pixels[:, 1:]).flat:
        value = (value << 1) | int(bit)
    return f"{value:0{(hash_size * hash_size + 3) // 4}x}"


def dhash(path: Path, hash_size: int = 8) -> str:
    with Image.open(path) as source:
        return _image_dhash(ImageOps.exif_transpose(source), hash_size)


def hamming_distance(hash_a: str, hash_b: str) -> int:
    return (int(hash_a, 16) ^ int(hash_b, 16)).bit_count()


def _base_visual_from_image(image: Image.Image) -> VisualFeatures:
    gray = image.convert("L").resize((GRAY_SIZE, GRAY_SIZE), Image.Resampling.LANCZOS)
    grid = GRAY_SIZE // BLOCK_SIZE
    blocks = np.asarray(gray).reshape(grid, BLOCK_SIZE, grid, BLOCK_SIZE)
    blocks = blocks.transpose(0, 2, 1, 3).reshape(grid * grid, BLOCK_SIZE ** 2).copy()
    floating = blocks.astype(np.float32)
    means = floating.mean(axis=1)
    variances = floating.var(axis=1)
    for array in (blocks, means, variances):
        array.flags.writeable = False
    return VisualFeatures(int(_image_dhash(image), 16), image.width / image.height,
                          blocks, means, variances)


def _visual_from_image(image: Image.Image) -> VisualFeatures:
    # All views derive from the same oriented immutable source snapshot. Keep
    # only descriptors, never extra raster images, in the content-keyed cache.
    base = _base_visual_from_image(image)
    if min(image.size) < 128:
        return base  # Insufficient pixels for another approximate crop view.
    gray = image.convert("L")
    gray.thumbnail((_CROP_PREVIEW_EDGE, _CROP_PREVIEW_EDGE), Image.Resampling.LANCZOS)
    if min(gray.size) < 64:
        return base
    variants = []
    for fraction in _CROP_FRACTIONS:
        box = (round(gray.width * fraction), round(gray.height * fraction),
               round(gray.width * (1 - fraction)), round(gray.height * (1 - fraction)))
        variants.append(_base_visual_from_image(gray.crop(box)))
    return replace(base, crop_variants=tuple(variants))


_VISUAL_CACHE_SIZE = 256
_visual_cache: OrderedDict[bytes, VisualFeatures] = OrderedDict()
_visual_cache_lock = Lock()


def _file_version(stream) -> tuple[int, ...]:
    stat = os.fstat(stream.fileno())
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino


def _snapshot_matches(stream, snapshot: bytes) -> bool:
    """Check content again without retaining a second full encoded preview."""
    stream.seek(0)
    expected = memoryview(snapshot)
    offset = 0
    while chunk := stream.read(256 * 1024):
        if chunk != expected[offset:offset + len(chunk)]:
            return False
        offset += len(chunk)
    return offset == len(snapshot)


def _cached_visual(path: Path) -> VisualFeatures:
    # Stats alone cannot detect equal-size rewrites with restored timestamps,
    # notably on Windows. Decode and hash the same immutable byte snapshot.
    # Unbuffered rereads detect in-place changes without stale Python buffers;
    # the single handle also follows atomic replacements consistently.
    with path.open('rb', buffering=0) as stream:
        before = _file_version(stream)
        snapshot = stream.read()
        signature = hashlib.file_digest(BytesIO(snapshot), 'sha256').digest()
        with _visual_cache_lock:
            visual = _visual_cache.get(signature)
        if visual is None:
            with Image.open(BytesIO(snapshot)) as image:
                visual = _visual_from_image(ImageOps.exif_transpose(image))
        if not _snapshot_matches(stream, snapshot) or _file_version(stream) != before:
            raise OSError('Preview changed while reading visual features')
    # Only derived features survive this call. Content validation also applies
    # to cache hits. A concurrent writer can still change a file after this
    # check; no stat/content check is a filesystem lock or a freshness promise.
    with _visual_cache_lock:
        _visual_cache[signature] = visual
        _visual_cache.move_to_end(signature)
        while len(_visual_cache) > _VISUAL_CACHE_SIZE:
            _visual_cache.popitem(last=False)
    return visual


def visual_features(asset: PhotoAsset) -> VisualFeatures | None:
    """Read only a prepared preview (or raster fallback); never trigger RAW decode/AI."""
    if any(str(note).startswith("decode_error:") for note in asset.notes):
        return None
    paths = dict.fromkeys((asset.preview_path, asset.jpg_path, asset.display_path))
    for path in paths:
        if not path or Path(path).suffix.lower() in RAW_EXTENSIONS:
            continue
        try:
            return _cached_visual(Path(path))
        except (OSError, ValueError, Image.DecompressionBombError):
            continue
    return None


def _structure_terms(left: VisualFeatures, right: VisualFeatures):
    luminance = (2 * left.means * right.means + _C1) / (left.means ** 2 + right.means ** 2 + _C1)
    denominator = left.variances + right.variances + _C2
    return luminance, denominator


def grayscale_structure_similarity(left: VisualFeatures, right: VisualFeatures) -> float:
    """Mean 8x8-block SSIM on a 64x64, EXIF-oriented grayscale image.

    Population covariance, explicit 255 data range, K1=.01, K2=.03.
    This is block SSIM, not scikit-image's sliding-window/Gaussian SSIM.
    """
    luminance, denominator = _structure_terms(left, right)
    products = np.einsum("ij,ij->i", left.blocks, right.blocks, dtype=np.float32)
    covariance = products / (BLOCK_SIZE ** 2) - left.means * right.means
    return float(np.clip(np.mean(luminance * (2 * covariance + _C2) / denominator), -1, 1))


def _structure_upper_bound(left: VisualFeatures, right: VisualFeatures) -> float:
    # Cauchy-Schwarz bounds covariance by sigma_x*sigma_y. This cheap, exact
    # upper bound rejects impossible matches before touching the 4096 pixels.
    luminance, denominator = _structure_terms(left, right)
    return float(np.mean(luminance * (2 * np.sqrt(left.variances * right.variances) + _C2) / denominator))


def _subject_compatible(a: PhotoAsset, b: PhotoAsset, cfg: GroupingConfig) -> bool:
    # Reuse cached detections only. Missing detections do not imply a pose change,
    # and grouping never redetects people or replaces manual subject selections.
    if getattr(a, "analysis_mode", "portrait") != "portrait" or getattr(b, "analysis_mode", "portrait") != "portrait":
        return True
    left, right = a.subject_features, b.subject_features
    if not (left and right and getattr(left, "face", None) and getattr(right, "face", None)
            and getattr(left, "body", None) and getattr(right, "body", None)):
        return True
    lx, ly, lw, lh = left.face
    rx, ry, rw, rh = right.face
    displacement = ((lx + lw / 2 - rx - rw / 2) ** 2 + (ly + lh / 2 - ry - rh / 2) ** 2) ** .5
    size_ratio = max(lw * lh, rw * rh) / max(1e-9, min(lw * lh, rw * rh))
    try:
        return (displacement <= .10 and size_ratio <= 1.8
                and hamming_distance(left.body, right.body) <= cfg.hash_distance_threshold)
    except (TypeError, ValueError):
        return True


def _similar(a: PhotoAsset, b: PhotoAsset, left: VisualFeatures, right: VisualFeatures,
             cfg: GroupingConfig, stats: GroupingStats) -> bool:
    if (left.whole ^ right.whole).bit_count() > cfg.hash_distance_threshold:
        return False
    if max(left.aspect, right.aspect) / min(left.aspect, right.aspect) > cfg.max_aspect_ratio:
        return False
    if not _subject_compatible(a, b, cfg):
        return False
    if _structure_upper_bound(left, right) + 1e-6 < cfg.structure_threshold:
        return False
    stats.structure_comparisons += 1
    return grayscale_structure_similarity(left, right) + 1e-6 >= cfg.structure_threshold


def _batch_structure_matches(left: list[VisualFeatures], right: VisualFeatures,
                             threshold: float, stats: GroupingStats) -> np.ndarray:
    """Same block SSIM decision in bounded batches; no approximate rejection.

    Float32 arithmetic follows the scalar formula. Near either decision boundary
    use the scalar result, so vector reduction rounding cannot change membership.
    Counts include speculatively evaluated pairs and scalar boundary rechecks.
    """
    matches = np.zeros(len(left), dtype=bool)
    if not left:
        return matches
    means = np.stack([value.means for value in left])
    variances = np.stack([value.variances for value in left])
    luminance = (2 * means * right.means + _C1) / (means ** 2 + right.means ** 2 + _C1)
    denominator = variances + right.variances + _C2
    upper = np.mean(luminance * (2 * np.sqrt(variances * right.variances) + _C2)
                    / denominator, axis=1).astype(np.float64)
    for index in np.flatnonzero(np.abs(upper + 1e-6 - threshold) < 1e-5):
        upper[index] = _structure_upper_bound(left[index], right)
    positions = np.flatnonzero(upper + 1e-6 >= threshold)
    if not len(positions):
        return matches
    blocks = np.stack([left[index].blocks for index in positions])
    products = np.einsum('kij,ij->ki', blocks, right.blocks, dtype=np.float32)
    covariance = products / (BLOCK_SIZE ** 2) - means[positions] * right.means
    scores = np.clip(np.mean(luminance[positions] * (2 * covariance + _C2)
                             / denominator[positions], axis=1), -1, 1).astype(np.float64)
    stats.structure_comparisons += len(positions)
    for index in np.flatnonzero(np.abs(scores + 1e-6 - threshold) < 1e-5):
        scores[index] = grayscale_structure_similarity(left[positions[index]], right)
        stats.structure_comparisons += 1
    matches[positions] = scores + 1e-6 >= threshold
    return matches


def _first_matching_representative(asset: PhotoAsset, visual: VisualFeatures,
                                   candidates: list[tuple[int, int]], representatives,
                                   cfg: GroupingConfig, stats: GroupingStats) -> int | None:
    if len(candidates) < 16:
        for _distance, position in candidates:
            anchor, anchor_visual, _group_id = representatives[position]
            if _similar(anchor, asset, anchor_visual, visual, cfg, stats):
                return position
        return None
    # Even an adversarial hash collision does not allocate an all-pairs matrix.
    # Candidate order is unchanged: closest hash, then stable insertion order.
    for start in range(0, len(candidates), 64):
        positions, visuals = [], []
        for distance, position in candidates[start:start + 64]:
            anchor, anchor_visual, _group_id = representatives[position]
            if (distance <= cfg.hash_distance_threshold
                    and max(anchor_visual.aspect, visual.aspect) / min(anchor_visual.aspect, visual.aspect)
                    <= cfg.max_aspect_ratio and _subject_compatible(anchor, asset, cfg)):
                positions.append(position)
                visuals.append(anchor_visual)
        matches = _batch_structure_matches(visuals, visual, cfg.structure_threshold, stats)
        matching = np.flatnonzero(matches)
        if len(matching):
            return positions[matching[0]]
    return None


def _crop_aligned_similar(a: PhotoAsset, b: PhotoAsset, left: VisualFeatures,
                          right: VisualFeatures, cfg: GroupingConfig,
                          stats: GroupingStats) -> bool:
    """Fallback with stricter gates; never crop both sides or change an anchor.

    The whole-frame aspect and subject gates still apply. Only 2.5%/5%
    central trims are supported: this is not arbitrary crop/rotation matching.
    """
    if (max(left.aspect, right.aspect) / min(left.aspect, right.aspect) > cfg.max_aspect_ratio
            or not _subject_compatible(a, b, cfg)):
        return False
    aligned_cfg = GroupingConfig(min(cfg.hash_distance_threshold, 8),
                                 max(cfg.structure_threshold, .86), cfg.max_aspect_ratio)
    return (any(_similar(a, b, view, right, aligned_cfg, stats) for view in left.crop_variants)
            or any(_similar(a, b, left, view, aligned_cfg, stats) for view in right.crop_variants))


def _first_crop_matching_representative(asset: PhotoAsset, visual: VisualFeatures,
                                        candidates, representatives, cfg: GroupingConfig,
                                        stats: GroupingStats) -> int | None:
    if len(candidates) < 16:
        for _distance, position in candidates:
            anchor, anchor_visual, _group_id = representatives[position]
            if _crop_aligned_similar(anchor, asset, anchor_visual, visual, cfg, stats):
                return position
        return None
    aligned = GroupingConfig(min(cfg.hash_distance_threshold, 8),
                             max(cfg.structure_threshold, .86), cfg.max_aspect_ratio)
    # Four bounded alignment passes retain exact candidate order. In a hash
    # collision pool, vectorize structure products rather than doing four
    # Python/scalar comparisons per representative. Never build all-pairs data.
    for start in range(0, len(candidates), 64):
        eligible = []
        for _distance, position in candidates[start:start + 64]:
            anchor, left, _group_id = representatives[position]
            if (max(left.aspect, visual.aspect) / min(left.aspect, visual.aspect) <= cfg.max_aspect_ratio
                    and _subject_compatible(anchor, asset, cfg)
                    and _subject_compatible(anchor, asset, aligned)):
                eligible.append((position, left))
        matched = set()
        for side in ('anchor', 'query'):
            for scale in range(len(_CROP_FRACTIONS)):
                positions, lefts = [], []
                if side == 'query' and scale >= len(visual.crop_variants):
                    continue
                right = visual if side == 'anchor' else visual.crop_variants[scale]
                for position, whole in eligible:
                    if position in matched:
                        continue
                    if side == 'anchor' and scale >= len(whole.crop_variants):
                        continue
                    left = whole.crop_variants[scale] if side == 'anchor' else whole
                    if ((left.whole ^ right.whole).bit_count() <= aligned.hash_distance_threshold
                            and max(left.aspect, right.aspect) / min(left.aspect, right.aspect)
                            <= aligned.max_aspect_ratio):
                        positions.append(position)
                        lefts.append(left)
                matches = _batch_structure_matches(lefts, right, aligned.structure_threshold, stats)
                matched.update(positions[i] for i in np.flatnonzero(matches))
        for position, _left in eligible:
            if position in matched:
                return position
    return None


def _crop_candidate_positions(visual: VisualFeatures, index, crop_index,
                              cfg: GroupingConfig) -> list[tuple[int, int]]:
    # Separate whole/crop indexes avoid admitting crop-to-crop-only matches.
    # Deduplicate positions so each fixed representative is verified once.
    radius = min(cfg.hash_distance_threshold, 8)
    distances = {}
    for distance, position in crop_index.find(visual.whole, radius):
        distances[position] = min(distance, distances.get(position, distance))
    for view in visual.crop_variants:
        for distance, position in index.find(view.whole, radius):
            distances[position] = min(distance, distances.get(position, distance))
    return sorted((distance, position) for position, distance in distances.items())


@dataclass(slots=True)
class _HashNode:
    value: int
    representatives: list[int] = field(default_factory=list)
    children: dict[int, "_HashNode"] = field(default_factory=dict)


class _HashIndex:
    """Exact Hamming-radius BK-tree of representatives, not all image pairs."""
    def __init__(self):
        self.root: _HashNode | None = None

    def add(self, value: int, representative: int) -> None:
        if self.root is None:
            self.root = _HashNode(value, [representative])
            return
        node = self.root
        while True:
            distance = (value ^ node.value).bit_count()
            if distance == 0:
                node.representatives.append(representative)
                return
            child = node.children.get(distance)
            if child is None:
                node.children[distance] = _HashNode(value, [representative])
                return
            node = child

    def find(self, value: int, radius: int) -> list[tuple[int, int]]:
        pending = [self.root] if self.root is not None else []
        matches = []
        while pending:
            node = pending.pop()
            distance = (value ^ node.value).bit_count()
            if distance <= radius:
                matches.extend((distance, item) for item in node.representatives)
            pending.extend(child for edge, child in node.children.items()
                           if distance - radius <= edge <= distance + radius)
        return sorted(matches)


def source_photo_id(asset: PhotoAsset) -> str:
    """Stable source ID shared with AI/project identity, including relocation."""
    identity = source_identity_path(asset, asset.primary_path).casefold()
    return 'P' + hashlib.sha256(identity.encode()).hexdigest()[:12].upper()


def _asset_order(asset: PhotoAsset) -> tuple[str, str, str]:
    # Do not depend on timestamps, input order, or an unstable object id.
    identity = source_identity_path(asset, asset.primary_path)
    return identity.casefold(), identity, asset.stem


def assign_groups(assets: list[PhotoAsset], preset: str = "standard", *,
                  preserve_existing: bool = True,
                  protected_group_ids: Collection[int] = (),
                  stats: GroupingStats | None = None) -> list[PhotoAsset]:
    """Group workspace-wide by hash AND structure, with fixed representatives.

    Positive existing IDs are retained by default. New photos can join an
    existing automatic group, but never a protected/manual one. The caller must
    pass saved manual IDs as protected_group_ids before any automatic regroup.
    An explicit regroup can set preserve_existing=False. No photo is reordered,
    discarded, rated, or decoded from RAW here. Missing evidence stays singleton.
    """
    cfg = PRESETS[preset]
    stats = stats if stats is not None else GroupingStats()
    stats.photos = len(assets)
    stats.representatives = stats.hash_candidates = stats.structure_comparisons = stats.missing_visuals = 0
    stats.crop_candidates = stats.crop_matches = 0
    protected = set(protected_group_ids)
    ordered = sorted(assets, key=_asset_order)
    existing: dict[int, list[PhotoAsset]] = {}
    pending = []
    for asset in ordered:
        if asset.group_id > 0 and (preserve_existing or asset.group_id in protected):
            existing.setdefault(asset.group_id, []).append(asset)
        else:
            pending.append(asset)
    # A temporarily absent manual group is still reserved by its saved store.
    # Reusing its ID would let an addition appear to join protected membership.
    next_id = max((*existing, *protected), default=0) + 1
    representatives: list[tuple[PhotoAsset, VisualFeatures, int]] = []
    index = _HashIndex()
    crop_index = _HashIndex()

    def add_representative(asset, visual, group_id):
        index.add(visual.whole, len(representatives))
        for view in visual.crop_variants:
            crop_index.add(view.whole, len(representatives))
        representatives.append((asset, visual, group_id))

    for group_id, members in sorted(existing.items()):
        if group_id in protected:
            continue
        saved_anchors = {asset.grouping_anchor_id for asset in members if asset.grouping_anchor_id}
        if len(saved_anchors) > 1:
            # Inconsistent legacy/manual merges need explicit review, not an
            # arbitrary new representative and another possible chain.
            continue
        if saved_anchors:
            anchor_id = next(iter(saved_anchors))
            anchor = next((asset for asset in members if source_photo_id(asset) == anchor_id), None)
            if anchor is None:
                continue  # A removed/unavailable anchor must not silently move.
        else:
            anchor = members[0]
            anchor_id = source_photo_id(anchor)
        for asset in members:
            asset.grouping_anchor_id = anchor_id
        visual = visual_features(anchor)
        if visual is not None:
            add_representative(anchor, visual, group_id)

    for asset in pending:
        visual = visual_features(asset)
        chosen = None
        anchor_id = source_photo_id(asset)
        if visual is None:
            stats.missing_visuals += 1
        else:
            candidates = index.find(visual.whole, cfg.hash_distance_threshold)
            stats.hash_candidates += len(candidates)
            # Closest hash first, stable representative insertion order breaks ties.
            # Every addition matches the original representative, never a chain.
            position = _first_matching_representative(asset, visual, candidates, representatives, cfg, stats)
            if position is None:
                crop_candidates = _crop_candidate_positions(visual, index, crop_index, cfg)
                stats.crop_candidates += len(crop_candidates)
                position = _first_crop_matching_representative(
                    asset, visual, crop_candidates, representatives, cfg, stats)
                if position is not None:
                    stats.crop_matches += 1
            if position is not None:
                anchor, anchor_visual, group_id = representatives[position]
                chosen = group_id
                anchor_id = source_photo_id(anchor)
        if chosen is None:
            chosen = next_id
            next_id += 1
            if visual is not None:
                add_representative(asset, visual, chosen)
        asset.group_id = chosen
        asset.grouping_anchor_id = anchor_id
    stats.representatives = len(representatives)
    return assets


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
    return True


def merge_adjacent_groups(assets: list[PhotoAsset], first_group_id: int, second_group_id: int) -> bool:
    """Merge neighboring displayed groups without renumbering unrelated groups."""
    order = list(dict.fromkeys(asset.group_id for asset in assets))
    if first_group_id not in order or second_group_id not in order:
        return False
    left, right = sorted((order.index(first_group_id), order.index(second_group_id)))
    if right - left != 1:
        return False
    target, source = order[left], order[right]
    for asset in assets:
        if asset.group_id == source:
            asset.group_id = target
    return True
