"""Shared data and matching helpers for the person-assist review workflow."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import threading

import numpy as np

DETECT_MIN_SCORE = .8


@dataclass
class FaceProposal:
    target_key: str
    source_key: str
    box: tuple[float, float, float, float] | None
    level: str  # reliable / review / uncertain / missing
    match_score: float | None
    detector_score: float | None
    reason: str
    candidate_boxes: tuple[tuple[float, float, float, float], ...] = ()


@dataclass(frozen=True)
class AssistImage:
    key: str
    stem: str
    preview_path: str | None


@dataclass(frozen=True)
class AssistTarget(AssistImage):
    cross_group: bool = False
    snapshot: str = ''  # JSON of the per-photo entry when the task started


def asset_signature(asset):
    """Cheap identity guard, including source, preview, group and subject state."""
    values = [getattr(asset, 'group_id', None), repr(getattr(asset, 'subject_features', None))]
    for path in (asset.primary_path, asset.preview_path):
        try:
            stat = Path(path).stat()
            values.append((str(path), stat.st_size, stat.st_mtime_ns))
        except (OSError, TypeError):
            values.append((str(path), None))
    return tuple(values)


def normalized_box_ok(box) -> bool:
    try:
        x, y, w, h = (float(value) for value in box)
    except (TypeError, ValueError):
        return False
    return (
        all(math.isfinite(value) for value in (x, y, w, h))
        and 0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0
        and x + w <= 1.000001 and y + h <= 1.000001
    )


def clamp_box(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    w = min(w, 1.0 - x)
    h = min(h, 1.0 - y)
    return (x, y, w, h)


def reference_box_from_entry(entry: dict) -> tuple[list | None, str]:
    """Return (box, state) for the reference photo.

    state is one of: 'ok', 'none' (no user-confirmed single face), 'multiple'
    (multi-person photos are out of scope for the first version).
    """
    if entry.get('hidden'):
        return None, 'none'
    if 'selected_faces' in entry:
        selected = entry['selected_faces']
        if not isinstance(selected, list) or not selected:
            return None, 'none'
        if len(selected) > 1:
            return None, 'multiple'
    manual = entry.get('manual_face')
    if normalized_box_ok(manual):
        return list(manual), 'ok'
    selected = entry.get('selected_faces')
    if isinstance(selected, list):
        valid = [box for box in selected if normalized_box_ok(box)]
        if len(valid) > 1:
            return None, 'multiple'
        if len(valid) == 1:
            return list(valid[0]), 'ok'
    return None, 'none'


def _entry_located(entry: dict, asset) -> bool:
    """Mirror the marking predicate used by “next unmarked” navigation."""
    if entry.get('hidden'):
        return True
    if 'selected_faces' in entry:
        # An explicit empty list is a user-visible "nobody selected" decision
        # and must not be auto-filled either.
        return True
    if entry.get('manual_face'):
        return True
    subject = getattr(asset, 'subject_features', None)
    if (getattr(subject, 'candidate_count', None) or 0) > 1:
        return False  # Automatic subject choice is not a human confirmation.
    return bool(subject and (getattr(subject, 'head', None) or getattr(subject, 'face', None)))


def collect_targets(reference_asset, group_assets, edits, key_for, *, allow_cross_group=False) -> list[AssistTarget]:
    """Photos in the same group that still need a face, nearest shots first.

    ``group_assets`` must be in capture order; the reference is excluded and
    targets are ordered by distance from it so adjacent frames are handled
    first.
    """
    reference_key = key_for(reference_asset)
    positions = {key_for(asset): index for index, asset in enumerate(group_assets)}
    reference_position = positions.get(reference_key, 0)
    targets: list[AssistTarget] = []
    for asset in group_assets:
        if not allow_cross_group and asset.group_id != reference_asset.group_id:
            continue
        key = key_for(asset)
        if key == reference_key:
            continue
        entry = edits.get(key, {})
        if _entry_located(entry, asset):
            continue
        preview = getattr(asset, 'preview_path', None)
        targets.append(AssistTarget(
            key=key,
            stem=getattr(asset, 'stem', Path(str(preview or key)).stem),
            preview_path=str(preview) if preview else None,
            snapshot=json.dumps(entry, sort_keys=True, default=str),
            cross_group=asset.group_id != reference_asset.group_id,
        ))
    # Neighbouring frames in capture order first; ties keep the earlier shot.
    targets.sort(key=lambda target: (abs(positions.get(target.key, 0) - reference_position),
                                     positions.get(target.key, 0)))
    return targets


def load_rgb(path: str) -> np.ndarray:
    """Read a preview file with the project's EXIF-orientation convention."""
    from PIL import Image, ImageOps
    with Image.open(path) as image:
        return np.asarray(ImageOps.exif_transpose(image).convert('RGB'))


def propose_person_faces(
    reference: AssistImage,
    reference_box,
    targets,
    stop_event: threading.Event,
    on_progress=None,
    detection_confidence=DETECT_MIN_SCORE,
    additional_references=(),
    negative_references=(),
) -> list[FaceProposal]:
    """Find appearance-similar person locations across preview images.

    This is a bounded, offline review aid rather than identity recognition.
    Candidates remain ``review`` or ``uncertain`` and never establish identity.
    Uncertain proposals retain inspectable alternatives without a default box.
    """
    from .person_match import (
        MAX_PERSON_REFERENCES,
        build_reference,
        rank_candidates,
        rank_candidates_multi,
    )

    if not reference.preview_path or not Path(reference.preview_path).is_file():
        raise ValueError('参考照片预览不可读，无法查找人物')
    if not normalized_box_ok(reference_box):
        raise ValueError('参考人物框无效')
    reference_rgb = load_rgb(reference.preview_path)
    try:
        appearance_reference = build_reference(reference_rgb, reference_box, stop_event)
    except InterruptedError:
        return []

    appearance_references = [appearance_reference]
    for extra_image, extra_box in additional_references or ():
        if len(appearance_references) >= MAX_PERSON_REFERENCES:
            break
        if stop_event.is_set():
            return []
        try:
            if (not extra_image.preview_path
                    or not Path(extra_image.preview_path).is_file()
                    or not normalized_box_ok(extra_box)):
                continue
            extra_rgb = load_rgb(extra_image.preview_path)
            appearance_references.append(build_reference(extra_rgb, extra_box, stop_event))
        except InterruptedError:
            return []
        except Exception:  # noqa: BLE001 - an optional example must not invalidate the root
            continue

    exclusion_references = []
    for extra_image, extra_box in negative_references or ():
        if len(exclusion_references) >= MAX_PERSON_REFERENCES:
            break
        if stop_event.is_set():
            return []
        try:
            if (not extra_image.preview_path
                    or not Path(extra_image.preview_path).is_file()
                    or not normalized_box_ok(extra_box)):
                continue
            extra_rgb = load_rgb(extra_image.preview_path)
            exclusion_references.append(build_reference(extra_rgb, extra_box, stop_event))
        except InterruptedError:
            return []
        except Exception:  # noqa: BLE001 - optional negative evidence is best effort
            continue

    reference_summary = f'基于 {len(appearance_references)} 张已确认正向参考'
    if exclusion_references:
        reference_summary += f'和 {len(exclusion_references)} 张反向参考'
    proposals: list[FaceProposal] = []
    total = len(targets)
    for done, target in enumerate(targets, 1):
        if stop_event.is_set():
            break

        def missing(reason):
            return FaceProposal(target.key, reference.key, None, 'missing', None, None, reason)

        if not target.preview_path or not Path(target.preview_path).is_file():
            proposal = missing('预览缺失或不可读')
        else:
            try:
                image_rgb = load_rgb(target.preview_path)
                if len(appearance_references) == 1 and not exclusion_references:
                    candidates = rank_candidates(
                        image_rgb,
                        appearance_reference,
                        stop_event,
                        detection_confidence=detection_confidence,
                    )
                else:
                    candidates = rank_candidates_multi(
                        image_rgb,
                        appearance_references,
                        exclusion_references,
                        stop_event,
                        detection_confidence=detection_confidence,
                    )
                if stop_event.is_set():
                    break
                if not candidates:
                    proposal = missing('没有可靠候选：未找到足够的外观证据；目标可能不在本图，也可能漏检，请手动核对')
                else:
                    best = candidates[0]
                    source_text = {
                        'face': '人脸检测',
                        'head': '头部检测',
                        'template': '局部模板',
                    }.get(best.source, '候选检测')
                    reason_text = {
                        'reference_achromatic': '参考缺少可区分的颜色信息',
                        'candidate_achromatic': '候选缺少可区分的颜色信息',
                        'reference_low_texture': '参考纹理不足',
                        'candidate_low_texture': '候选纹理不足',
                        'near_threshold': '外观证据接近候选门槛',
                        'negative_conflict': '候选与已排除的参考同样或更相似',
                        'template_only': '只有局部模板证据，缺少独立检测支持',
                        'ambiguous': '多个候选分数接近',
                    }
                    reasons = tuple(getattr(best, 'review_reasons', ()))
                    if best.ambiguous and 'ambiguous' not in reasons:
                        reasons += ('ambiguous',)
                    uncertain = bool(reasons)
                    explanation = '；'.join(reason_text.get(reason, reason) for reason in reasons)
                    guidance = (
                        f'不确定：{explanation}；目标可能不在本图。请从待核对候选中手动选择或直接画框，再确认'
                        if uncertain else '目标仍可能不在本图，请逐张核对'
                    )
                    proposal = FaceProposal(
                        target.key,
                        reference.key,
                        None if uncertain else best.box,
                        'uncertain' if uncertain else 'review',
                        round(best.score, 3),
                        round(best.detector_score, 3) if best.detector_score is not None else None,
                        f'{reference_summary}；{source_text}结合脸/头部/上身颜色与纹理排序；'
                        f'{guidance}；分数不是身份概率，请人工确认，不代表身份识别',
                        tuple(candidate.box for candidate in candidates),
                    )
            except Exception as exc:  # noqa: BLE001 - one damaged preview must not stop the batch
                proposal = missing(f'本张检查失败：{exc}')
        if stop_event.is_set():
            break
        proposals.append(proposal)
        if on_progress is not None:
            on_progress(done, total, proposal)
    return proposals
