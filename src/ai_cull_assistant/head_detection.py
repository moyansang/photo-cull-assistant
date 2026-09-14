"""Head-only fallback detection for profiles and rear-facing people.

This module deliberately keeps head evidence separate from face evidence.  The
bundled MediaPipe person detector can locate a head from body anchors even when
the facial surface is turned away.  It never invents facial landmarks.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import cv2
import numpy as np


@dataclass(frozen=True)
class HeadDetection:
    """A head region in source-image pixels, without face-landmark claims."""

    box: tuple[float, float, float, float]
    score: float
    source: str

    def normalized_box(self, width: int, height: int) -> tuple[float, float, float, float]:
        if width <= 0 or height <= 0:
            raise ValueError("image dimensions must be positive")
        x, y, w, h = self.box
        return x / width, y / height, w / width, h / height


def _clip_box(
    box: tuple[float, float, float, float], width: int, height: int,
    *, minimum_retained: float = 0.65,
) -> tuple[float, float, float, float] | None:
    x, y, w, h = box
    if not np.isfinite((x, y, w, h)).all() or min(w, h) <= 0:
        return None
    x0, y0 = max(0.0, x), max(0.0, y)
    x1, y1 = min(float(width), x + w), min(float(height), y + h)
    if x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) < w * h * minimum_retained:
        return None
    return float(x0), float(y0), float(x1 - x0), float(y1 - y0)


def _body_anchor_box(
    person: np.ndarray, image_shape: tuple[int, int],
) -> tuple[float, float, float, float] | None:
    """Validate MediaPipe's head rectangle against its independent body anchors.

    The detector returns ``xyxy`` plus hip/full-body and shoulder/upper-body
    anchor pairs.  OpenCV's upstream implementation calls the rectangle a
    ``face_bbox`` but explicitly documents that its detailed semantics are
    unknown.  We therefore expose the result only as head evidence.
    """
    if person.ndim != 1 or len(person) != 13 or not np.isfinite(person).all() or person[-1] < .65:
        return None
    x0, y0, x1, y1 = map(float, person[:4])
    box_w, box_h = x1 - x0, y1 - y0
    height, width = image_shape
    if min(box_w, box_h) < max(20.0, min(width, height) * .018) or not .55 <= box_w / box_h <= 1.80:
        return None
    center = np.array([(x0 + x1) / 2, (y0 + y1) / 2], dtype=np.float64)
    hip, full, shoulder, upper = person[4:12].reshape(4, 2).astype(np.float64)
    full_radius = float(np.linalg.norm(hip - full))
    upper_radius = float(np.linalg.norm(shoulder - upper))
    diagonal = math.hypot(width, height)
    if not (
        full_radius >= .9 * max(box_w, box_h)
        and upper_radius >= .55 * max(box_w, box_h)
        and full_radius <= 1.5 * diagonal
        and upper_radius <= 1.5 * diagonal
    ):
        return None
    # The shoulder centre must remain close to the detector's head rectangle,
    # and the hip centre must provide a real body extent away from that head.
    margin_x, margin_y = .35 * box_w, .35 * box_h
    if not (x0 - margin_x <= shoulder[0] <= x1 + margin_x and y0 - margin_y <= shoulder[1] <= y1 + margin_y):
        return None
    if np.linalg.norm(hip - center) < .65 * max(box_w, box_h):
        return None

    # The raw detector rectangle is intentionally loose and reaches into the
    # neck.  Tighten it slightly and move it toward the crown.
    head_w, head_h = .90 * box_w, .92 * box_h
    head_center = center + .10 * (shoulder - hip)
    return _clip_box(
        (head_center[0] - head_w / 2, head_center[1] - head_h / 2, head_w, head_h),
        width, height,
    )


def _body_anchor_heads(image: np.ndarray, max_people: int = 4) -> list[HeadDetection]:
    # Import lazily: ordinary YuNet success should not load optional models,
    # and missing body weights must degrade to no fallback.
    from .body_focus import BodyModelUnavailable, _get_models

    try:
        person_detector, _pose_estimator = _get_models()
        people = person_detector.infer(image)
    except (BodyModelUnavailable, cv2.error, OSError, ValueError):
        return []
    if people is None or len(people) == 0:
        return []
    people = sorted(people, key=lambda row: float(row[-1]), reverse=True)[:max(0, max_people)]
    found: list[HeadDetection] = []
    for person in people:
        box = _body_anchor_box(np.asarray(person), image.shape[:2])
        if box:
            found.append(HeadDetection(box, min(.93, .55 + .42 * float(person[-1])), "body_anchor_head"))
    return _deduplicate(found)


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    overlap = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return overlap / max(aw * ah + bw * bh - overlap, 1e-6)


def _deduplicate(candidates: Iterable[HeadDetection]) -> list[HeadDetection]:
    selected: list[HeadDetection] = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if all(_iou(candidate.box, existing.box) < 0.35 for existing in selected):
            selected.append(candidate)
    return selected


def detect_heads(
    image: np.ndarray,
    reliable_face_boxes: Iterable[tuple[float, float, float, float]] = (),
    *,
    max_people: int = 4,
) -> list[HeadDetection]:
    """Return head-only candidates from independently validated body anchors.

    ``reliable_face_boxes`` are pixel ``(x, y, width, height)`` rectangles and
    are removed from the result.  Callers must continue to use YuNet detections
    for real facial-landmark work.
    """
    if not isinstance(image, np.ndarray) or image.ndim not in (2, 3) or image.size == 0:
        return []
    height, width = image.shape[:2]
    if min(height, width) < 24:
        return []
    candidates = _body_anchor_heads(image, max_people=max_people)
    faces = [tuple(map(float, box)) for box in reliable_face_boxes]
    return [
        candidate for candidate in _deduplicate(candidates)
        if all(_iou(candidate.box, face) < 0.20 for face in faces)
    ]


def refine_face_in_head(
    image: np.ndarray,
    head: HeadDetection,
    score_threshold: float = .8,
):
    """Retry YuNet within a body-anchored head ROI and return real 5-point evidence.

    The ROI gives a small/profile face more useful pixels.  A result is returned
    only when the normal YuNet landmark validation succeeds and its centre lies
    inside the independently detected head.
    """
    from .yunet import FaceDetection, detect as detect_faces

    if not isinstance(image, np.ndarray) or image.ndim not in (2, 3) or image.size == 0:
        return None
    image_h, image_w = image.shape[:2]
    x, y, width, height = head.box
    # Keep some shoulders/hair/background around difficult profiles.  YuNet is
    # materially less stable when the body detector's already-tight head box
    # is enlarged by only ~20%, especially after the preview's JPEG encoding.
    margin_x, margin_y = .30 * width, .30 * height
    clipped = _clip_box(
        (x - margin_x, y - margin_y, width + 2 * margin_x, height + 2 * margin_y),
        image_w, image_h,
        minimum_retained=.45,
    )
    if clipped is None:
        return None
    rx, ry, rw, rh = clipped
    x0, y0 = int(math.floor(rx)), int(math.floor(ry))
    x1, y1 = int(math.ceil(rx + rw)), int(math.ceil(ry + rh))
    roi = image[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    requested_scale = min(4.0, 640.0 / max(roi.shape[:2]))
    if abs(requested_scale - 1.0) > .02:
        target_w = max(1, round(roi.shape[1] * requested_scale))
        target_h = max(1, round(roi.shape[0] * requested_scale))
        interpolation = cv2.INTER_LANCZOS4 if requested_scale > 1 else cv2.INTER_AREA
        resized = cv2.resize(roi, (target_w, target_h), interpolation=interpolation)
    else:
        resized = roi
    scale_x = resized.shape[1] / roi.shape[1]
    scale_y = resized.shape[0] / roi.shape[0]
    candidates = detect_faces(resized, score_threshold)
    mapped: list[FaceDetection] = []
    for candidate in candidates:
        fx, fy, fw, fh = candidate.box
        absolute = FaceDetection(
            (x0 + fx / scale_x, y0 + fy / scale_y, fw / scale_x, fh / scale_y),
            tuple((x0 + px / scale_x, y0 + py / scale_y) for px, py in candidate.landmarks),
            candidate.score,
        )
        ax, ay, aw, ah = absolute.box
        center_x, center_y = ax + aw / 2, ay + ah / 2
        if x <= center_x <= x + width and y <= center_y <= y + height:
            mapped.append(absolute)
    if not mapped:
        return None
    return max(mapped, key=lambda item: item.score + .05 * min(1.0, item.box[2] * item.box[3] / max(width * height, 1)))
