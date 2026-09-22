"""Same-group face assist: propose face boxes for missed detections in one group.

Pure computation: no Tk, no writes to persisted settings.  The reference photo
is one whose face the user has already boxed by hand; other photos in the same
group that have no valid face box are matched against that reference region
with multi-scale grayscale+edge template matching, and the strongest matches
are verified with the existing YuNet detector on an enlarged local crop.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import threading

import cv2
import numpy as np

from .yunet import detect

# Initial conservative thresholds, pending real-photo calibration.
# A match score is not a probability.
MIN_MATCH = .55
RELIABLE_MATCH = .72
UNIQUE_MARGIN = .12
DETECT_MIN_SCORE = .8
DETECT_IOU = .3
ROI_PAD = .4          # search window padding around the expected position
TEMPLATE_SCALES = (.8, .9, 1.0, 1.15, 1.35)
MIN_TEMPLATE_PIXELS = 16


@dataclass
class FaceProposal:
    target_key: str
    source_key: str
    box: tuple[float, float, float, float] | None
    level: str  # reliable / review / missing
    match_score: float | None
    detector_score: float | None
    reason: str


@dataclass(frozen=True)
class AssistImage:
    key: str
    stem: str
    preview_path: str | None


@dataclass(frozen=True)
class AssistTarget(AssistImage):
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
    return bool(subject and (getattr(subject, 'head', None) or getattr(subject, 'face', None)))


def collect_targets(reference_asset, group_assets, edits, key_for) -> list[AssistTarget]:
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
        if asset.group_id != reference_asset.group_id:
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


def _gradient(image: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(image, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(image, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def _search_region(image_shape, expected_box) -> tuple[int, int, int, int]:
    """Pixel ROI around the expected position (reference box in normalized coords)."""
    height, width = image_shape
    ex, ey, ew, eh = clamp_box(*expected_box)
    pad_x = max(ew, .05) * ROI_PAD * width
    pad_y = max(eh, .05) * ROI_PAD * height
    left = max(0, int(ex * width - pad_x))
    top = max(0, int(ey * height - pad_y))
    right = min(width, max(left + 1, int((ex + ew) * width + pad_x)))
    bottom = min(height, max(top + 1, int((ey + eh) * height + pad_y)))
    return left, top, right, bottom


def _iou_px(a, b) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    x0, y0 = max(ax1, bx1), max(ay1, by1)
    x1, y1 = min(ax1 + aw, bx1 + bw), min(ay1 + ah, by1 + bh)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _match_peaks(gray, edge, template_gray, template_edge, roi, stop_event):
    """Multi-scale peaks inside the ROI: list of (score, x, y, w, h) in pixels."""
    rx, ry, rr, rb = roi
    region_gray = gray[ry:rb, rx:rr]
    region_edge = edge[ry:rb, rx:rr]
    peaks: list[tuple[float, float, float, float, float]] = []
    base_h, base_w = template_gray.shape
    for scale in TEMPLATE_SCALES:
        if stop_event.is_set():
            break
        tw = max(8, int(round(base_w * scale)))
        th = max(8, int(round(base_h * scale)))
        if tw >= region_gray.shape[1] or th >= region_gray.shape[0]:
            continue
        t_gray = cv2.resize(template_gray, (tw, th), interpolation=cv2.INTER_AREA)
        t_edge = cv2.resize(template_edge, (tw, th), interpolation=cv2.INTER_AREA)
        gray_resp = cv2.matchTemplate(region_gray, t_gray, cv2.TM_CCOEFF_NORMED)
        edge_resp = cv2.matchTemplate(region_edge, t_edge, cv2.TM_CCOEFF_NORMED)
        response = np.nan_to_num((gray_resp + edge_resp) / 2, nan=-1.0)
        for _ in range(3):
            _min, max_value, _minloc, max_loc = cv2.minMaxLoc(response)
            if max_value < MIN_MATCH:
                break
            x, y = max_loc
            peaks.append((float(max_value), float(x + rx), float(y + ry), float(tw), float(th)))
            sx0 = max(0, x - tw // 2)
            sy0 = max(0, y - th // 2)
            response[sy0:min(response.shape[0], sy0 + th), sx0:min(response.shape[1], sx0 + tw)] = -1.0
    peaks.sort(key=lambda peak: peak[0], reverse=True)
    kept: list[tuple[float, float, float, float, float]] = []
    for peak in peaks:
        if all(_iou_px(peak[1:], item[1:]) < .3 for item in kept):
            kept.append(peak)
        if len(kept) >= 4:
            break
    return kept


def _verify_with_detector(image_rgb, match_box_px, detection_confidence=DETECT_MIN_SCORE):
    """Run YuNet on an enlarged local crop; return (pixel_box, score) or None."""
    x, y, w, h = match_box_px
    pad_x, pad_y = w * .3, h * .3
    height, width = image_rgb.shape[:2]
    left = int(max(0, x - pad_x))
    top = int(max(0, y - pad_y))
    right = int(min(width, x + w + pad_x))
    bottom = int(min(height, y + h + pad_y))
    if right - left < 4 or bottom - top < 4:
        return None
    crop = np.ascontiguousarray(image_rgb[top:bottom, left:right])
    original_h, original_w = crop.shape[:2]
    zoom = max(1.0, min(4.0, 320 / max(1, min(crop.shape[:2]))))
    if zoom > 1.0:
        crop = cv2.resize(crop, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
    sx, sy = crop.shape[1] / original_w, crop.shape[0] / original_h
    detections = detect(cv2.cvtColor(crop, cv2.COLOR_RGB2BGR), detection_confidence, rotate_rescue=False)
    best = None
    best_iou = 0.0
    for face in detections:
        fx, fy, fw, fh = face.box
        mapped = ((fx / sx) + left, (fy / sy) + top, fw / sx, fh / sy)
        iou = _iou_px(mapped, match_box_px)
        if iou > best_iou:
            best, best_iou = (mapped, float(face.score)), iou
    if best is not None and best_iou >= DETECT_IOU:
        return best
    return None


def _propose_for_target(template_gray, template_edge, target, expected_box,
                        source_key, stop_event, detection_confidence) -> FaceProposal:
    def missing(reason):
        return FaceProposal(target.key, source_key, None, 'missing', None, None, reason)

    if not target.preview_path or not Path(target.preview_path).is_file():
        return missing('预览缺失或不可读')
    try:
        image_rgb = load_rgb(target.preview_path)
    except Exception as exc:  # noqa: BLE001 - report per-photo, keep going
        return missing(f'预览不可读：{exc}')
    height, width = image_rgb.shape[:2]
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    edge = _gradient(gray)
    if stop_event.is_set():
        return missing('已停止')
    roi = _search_region((height, width), expected_box)
    tw = max(8, round(expected_box[2] * width))
    th = max(8, round(expected_box[3] * height))
    scaled_template = cv2.resize(template_gray, (tw, th), interpolation=cv2.INTER_AREA)
    peaks = _match_peaks(gray, edge, scaled_template, _gradient(scaled_template), roi, stop_event)
    if stop_event.is_set():
        return missing('已停止')
    if not peaks or peaks[0][0] < MIN_MATCH:
        return missing('未找到足够强的匹配')
    best = peaks[0]
    ambiguous = (
        len(peaks) > 1
        and peaks[1][0] >= best[0] - UNIQUE_MARGIN
    )
    detection = _verify_with_detector(image_rgb, best[1:], detection_confidence)
    if detection is not None:
        box_px = detection[0]
        detector_score = detection[1]
    else:
        box_px = best[1:]
        detector_score = None
    box = clamp_box(box_px[0] / width, box_px[1] / height, box_px[2] / width, box_px[3] / height)
    if not normalized_box_ok(box) or box[2] * box[3] < 1e-5:
        return missing('匹配结果尺寸无效')
    unique = bool(peaks) and (not ambiguous)
    if best[0] >= RELIABLE_MATCH and unique and detection is not None:
        return FaceProposal(target.key, source_key, box, 'reliable', round(best[0], 3),
                            round(detector_score, 3), '匹配唯一且通过人脸检测')
    if detection is None:
        return FaceProposal(target.key, source_key, box, 'review', round(best[0], 3), None,
                            '存在多个相似候选，且未通过人脸检测' if ambiguous else '存在局部匹配，但未通过人脸检测')
    return FaceProposal(target.key, source_key, box, 'review', round(best[0], 3),
                        round(detector_score, 3),
                        '存在多个相似候选' if ambiguous else '匹配分数偏低，请逐张确认')


def propose_group_faces(
    reference: AssistImage,
    reference_box,
    targets,
    stop_event: threading.Event,
    on_progress=None,
    detection_confidence=DETECT_MIN_SCORE,
) -> list[FaceProposal]:
    """Match the reference face region against each target; grade proposals.

    ``on_progress(done, total, proposal)`` is called from the worker thread for
    every completed target; the full list is also returned.
    """
    if not reference.preview_path or not Path(reference.preview_path).is_file():
        raise ValueError('参考照片预览不可读，无法进行同组匹配')
    if not normalized_box_ok(reference_box):
        raise ValueError('参考人脸框无效')
    reference_rgb = load_rgb(reference.preview_path)
    height, width = reference_rgb.shape[:2]
    x, y, w, h = clamp_box(*reference_box)
    gray = cv2.cvtColor(reference_rgb, cv2.COLOR_RGB2GRAY)
    template = gray[int(y * height):int((y + h) * height), int(x * width):int((x + w) * width)]
    if template.size == 0 or min(template.shape[:2]) < MIN_TEMPLATE_PIXELS:
        raise ValueError('参考人脸框过小，无法作为匹配模板')
    template_edge = _gradient(template)
    if float(template.std()) < 2 or float(template_edge.std()) < 2:
        raise ValueError('参考区域纹理不足，请重新选择包含人脸细节的范围')
    proposals: list[FaceProposal] = []
    total = len(targets)
    for done, target in enumerate(targets, 1):
        if stop_event.is_set():
            break
        try:
            proposal = _propose_for_target(template, template_edge, target,
                                           (x, y, w, h), reference.key, stop_event, detection_confidence)
        except Exception as exc:
            proposal = FaceProposal(target.key, reference.key, None, 'missing',
                                    None, None, f'本张检查失败：{exc}')
        if stop_event.is_set():
            break
        proposals.append(proposal)
        if on_progress is not None:
            on_progress(done, total, proposal)
    return proposals
