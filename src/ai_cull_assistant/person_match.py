"""Offline appearance assistance for finding a selected person in previews.

This module deliberately performs no biometric recognition.  It combines the
project's existing face and body-anchored head detectors with coarse colour and
texture descriptions of the selected face/head and upper body.  The result is
only a short, ranked list for human review.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading

import cv2
import numpy as np


MAX_WORKING_EDGE = 960
MAX_WORKING_PIXELS = 900_000
MAX_DETECTOR_CANDIDATES = 8
MIN_APPEARANCE_SCORE = .46
AMBIGUOUS_MARGIN = .065
TEMPLATE_SCALES = (.55, .75, 1.0, 1.3, 1.7)


@dataclass(frozen=True)
class RegionDescriptor:
    colour: np.ndarray
    texture: np.ndarray


@dataclass(frozen=True)
class PersonReference:
    face_box: tuple[float, float, float, float]
    face: RegionDescriptor | None
    head: RegionDescriptor | None
    upper_body: RegionDescriptor | None
    template_gray: np.ndarray
    template_edge: np.ndarray


@dataclass(frozen=True)
class PersonCandidate:
    box: tuple[float, float, float, float]
    score: float
    detector_score: float | None
    source: str
    appearance_score: float
    template_score: float | None = None
    ambiguous: bool = False


@dataclass(frozen=True)
class _Anchor:
    box: tuple[float, float, float, float]
    source: str
    detector_score: float | None
    template_score: float | None = None


def _stopped(stop_event: threading.Event | None) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


def _valid_box(box) -> bool:
    try:
        x, y, width, height = map(float, box)
    except (TypeError, ValueError):
        return False
    return bool(
        np.isfinite((x, y, width, height)).all()
        and 0 <= x < 1 and 0 <= y < 1 and width > 0 and height > 0
        and x + width <= 1.000001 and y + height <= 1.000001
    )


def _clip_box(box) -> tuple[float, float, float, float] | None:
    try:
        x, y, width, height = map(float, box)
    except (TypeError, ValueError):
        return None
    if not np.isfinite((x, y, width, height)).all() or min(width, height) <= 0:
        return None
    x0, y0 = max(0.0, x), max(0.0, y)
    x1, y1 = min(1.0, x + width), min(1.0, y + height)
    if x1 - x0 <= 1e-5 or y1 - y0 <= 1e-5:
        return None
    return x0, y0, x1 - x0, y1 - y0


def _resize_bounded(image_rgb: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Bound detector/template work and return image plus x/y source scales."""
    if not isinstance(image_rgb, np.ndarray) or image_rgb.ndim != 3 or image_rgb.size == 0:
        raise ValueError("empty RGB image")
    height, width = image_rgb.shape[:2]
    edge_scale = min(1.0, MAX_WORKING_EDGE / max(height, width))
    pixel_scale = min(1.0, math.sqrt(MAX_WORKING_PIXELS / max(1, height * width)))
    scale = min(edge_scale, pixel_scale)
    if scale >= .999:
        return np.ascontiguousarray(image_rgb), 1.0, 1.0
    working_width = max(1, round(width * scale))
    working_height = max(1, round(height * scale))
    working = cv2.resize(image_rgb, (working_width, working_height), interpolation=cv2.INTER_AREA)
    return working, width / working_width, height / working_height


def _relative_box(anchor, *, kind: str) -> tuple[float, float, float, float] | None:
    """Derive face/head/apparel regions from one normalized head-like anchor."""
    x, y, width, height = anchor
    center_x = x + width / 2
    if kind == "face":
        return _clip_box((x, y, width, height))
    if kind == "head":
        return _clip_box((center_x - .70 * width, y - .28 * height,
                          1.40 * width, 1.58 * height))
    if kind == "upper_body":
        # Start below most facial pixels so clothing contributes more than skin.
        return _clip_box((center_x - 1.25 * width, y + .78 * height,
                          2.50 * width, 3.15 * height))
    raise ValueError(f"unknown region kind: {kind}")


def _crop(image_rgb: np.ndarray, box) -> np.ndarray | None:
    clipped = _clip_box(box)
    if clipped is None:
        return None
    height, width = image_rgb.shape[:2]
    x, y, box_width, box_height = clipped
    x0, y0 = max(0, int(math.floor(x * width))), max(0, int(math.floor(y * height)))
    x1 = min(width, int(math.ceil((x + box_width) * width)))
    y1 = min(height, int(math.ceil((y + box_height) * height)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return image_rgb[y0:y1, x0:x1]


def _descriptor(image_rgb: np.ndarray, box) -> RegionDescriptor | None:
    region = _crop(image_rgb, box)
    if region is None:
        return None
    # Trim rectangle edges, which are often background after pose changes.
    height, width = region.shape[:2]
    trim_x, trim_y = round(width * .08), round(height * .05)
    if width - 2 * trim_x >= 8 and height - 2 * trim_y >= 8:
        region = region[trim_y:height - trim_y, trim_x:width - trim_x]
    small = cv2.resize(region, (48, 48), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    colour = cv2.calcHist([hsv], [0, 1], None, [12, 5], [0, 180, 0, 256]).reshape(-1)
    colour = colour.astype(np.float32)
    colour /= max(float(colour.sum()), 1e-6)

    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    magnitude, angle = cv2.cartToPolar(gx, gy, angleInDegrees=False)
    bins = np.floor(angle * (8 / (2 * np.pi))).astype(np.int32) % 8
    orientation = np.bincount(bins.ravel(), weights=magnitude.ravel(), minlength=8).astype(np.float32)
    orientation /= max(float(orientation.sum()), 1e-6)
    intensity = cv2.calcHist([gray], [0], None, [8], [0, 256]).reshape(-1).astype(np.float32)
    intensity /= max(float(intensity.sum()), 1e-6)
    texture = np.concatenate((orientation, intensity))
    return RegionDescriptor(colour, texture)


def _gradient(gray: np.ndarray) -> np.ndarray:
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def build_reference(
    image_rgb: np.ndarray,
    reference_box,
    stop_event: threading.Event | None = None,
) -> PersonReference:
    """Describe a manually selected person using only the bounded preview."""
    if not _valid_box(reference_box):
        raise ValueError("invalid person reference box")
    working, _sx, _sy = _resize_bounded(image_rgb)
    box = tuple(map(float, reference_box))
    face_crop = _crop(working, box)
    if face_crop is None or min(face_crop.shape[:2]) < 12:
        raise ValueError("person reference box is too small")
    if _stopped(stop_event):
        raise InterruptedError("person matching stopped")
    gray = cv2.cvtColor(face_crop, cv2.COLOR_RGB2GRAY)
    scale = min(1.0, 96 / max(gray.shape[:2]))
    if scale < 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return PersonReference(
        face_box=box,
        face=_descriptor(working, _relative_box(box, kind="face")),
        head=_descriptor(working, _relative_box(box, kind="head")),
        upper_body=_descriptor(working, _relative_box(box, kind="upper_body")),
        template_gray=gray,
        template_edge=_gradient(gray),
    )


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator > 1e-8 else 0.0


def _region_similarity(reference: RegionDescriptor | None, candidate: RegionDescriptor | None) -> float | None:
    if reference is None or candidate is None:
        return None
    # Histogram intersection is stable for large colour blocks such as clothing.
    colour = float(np.minimum(reference.colour, candidate.colour).sum())
    texture = max(0.0, min(1.0, _cosine(reference.texture, candidate.texture)))
    return max(0.0, min(1.0, .68 * colour + .32 * texture))


def _appearance_similarity(image_rgb: np.ndarray, reference: PersonReference, anchor: _Anchor) -> float:
    face = _region_similarity(reference.face, _descriptor(image_rgb, _relative_box(anchor.box, kind="face")))
    head = _region_similarity(reference.head, _descriptor(image_rgb, _relative_box(anchor.box, kind="head")))
    upper = _region_similarity(
        reference.upper_body,
        _descriptor(image_rgb, _relative_box(anchor.box, kind="upper_body")),
    )
    values: list[tuple[float, float]] = []
    if face is not None:
        values.append((face, .18 if anchor.source == "head" else .25))
    if head is not None:
        values.append((head, .35))
    if upper is not None:
        values.append((upper, .47 if anchor.source != "head" else .52))
    weight = sum(item[1] for item in values)
    return sum(value * item_weight for value, item_weight in values) / weight if weight else 0.0


def _iou(left, right) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    x0, y0 = max(lx, rx), max(ly, ry)
    x1, y1 = min(lx + lw, rx + rw), min(ly + lh, ry + rh)
    overlap = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return overlap / max(lw * lh + rw * rh - overlap, 1e-9)


def _detector_anchors(image_rgb: np.ndarray, detection_confidence: float) -> list[_Anchor]:
    """Run the two existing offline candidate detectors on a bounded preview."""
    from .head_detection import detect_heads
    from .yunet import detect as detect_faces

    height, width = image_rgb.shape[:2]
    bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    faces = []
    try:
        faces = detect_faces(bgr, detection_confidence, rotate_rescue=False)
    except Exception:  # noqa: BLE001 - optional detector is best-effort evidence
        faces = []
    anchors: list[_Anchor] = []
    face_boxes_px = []
    for face in faces[:MAX_DETECTOR_CANDIDATES]:
        x, y, box_width, box_height = map(float, face.box)
        normalized = _clip_box((x / width, y / height, box_width / width, box_height / height))
        if normalized is not None:
            face_boxes_px.append((x, y, box_width, box_height))
            anchors.append(_Anchor(normalized, "face", float(face.score)))
    try:
        heads = detect_heads(bgr, face_boxes_px, max_people=MAX_DETECTOR_CANDIDATES)
    except Exception:  # noqa: BLE001 - missing/failed body model still permits template evidence
        heads = []
    for head in heads:
        x, y, box_width, box_height = map(float, head.box)
        normalized = _clip_box((x / width, y / height, box_width / width, box_height / height))
        if normalized is not None:
            anchors.append(_Anchor(normalized, "head", float(head.score)))
    return anchors


def _template_anchors(
    image_rgb: np.ndarray,
    reference: PersonReference,
    stop_event: threading.Event | None,
) -> list[_Anchor]:
    """Return at most two full-preview local-template fallbacks."""
    gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    edge = _gradient(gray)
    height, width = gray.shape
    base_width = max(12, round(reference.face_box[2] * width))
    base_height = max(12, round(reference.face_box[3] * height))
    peaks: list[tuple[float, tuple[float, float, float, float]]] = []
    for scale in TEMPLATE_SCALES:
        if _stopped(stop_event):
            break
        target_width = max(10, round(base_width * scale))
        target_height = max(10, round(base_height * scale))
        if target_width >= width or target_height >= height:
            continue
        template_gray = cv2.resize(reference.template_gray, (target_width, target_height), interpolation=cv2.INTER_AREA)
        template_edge = cv2.resize(reference.template_edge, (target_width, target_height), interpolation=cv2.INTER_AREA)
        gray_response = cv2.matchTemplate(gray, template_gray, cv2.TM_CCOEFF_NORMED)
        edge_response = cv2.matchTemplate(edge, template_edge, cv2.TM_CCOEFF_NORMED)
        response = np.nan_to_num(.42 * gray_response + .58 * edge_response, nan=-1.0)
        for _ in range(2):
            _minimum, value, _min_location, location = cv2.minMaxLoc(response)
            if value < .42:
                break
            x, y = location
            box = (x / width, y / height, target_width / width, target_height / height)
            peaks.append((float(value), box))
            left, top = max(0, x - target_width // 2), max(0, y - target_height // 2)
            response[top:min(response.shape[0], top + target_height),
                     left:min(response.shape[1], left + target_width)] = -1
    selected: list[_Anchor] = []
    for score, box in sorted(peaks, reverse=True):
        if all(_iou(box, current.box) < .35 for current in selected):
            selected.append(_Anchor(box, "template", None, score))
        if len(selected) >= 2:
            break
    return selected


def rank_candidates(
    image_rgb: np.ndarray,
    reference: PersonReference,
    stop_event: threading.Event | None = None,
    *,
    detection_confidence: float = .8,
) -> list[PersonCandidate]:
    """Rank plausible locations; callers must require manual confirmation."""
    working, _sx, _sy = _resize_bounded(image_rgb)
    if _stopped(stop_event):
        return []
    anchors = _detector_anchors(working, detection_confidence)
    if _stopped(stop_event):
        return []
    anchors.extend(_template_anchors(working, reference, stop_event))
    if _stopped(stop_event):
        return []
    ranked: list[PersonCandidate] = []
    for anchor in anchors:
        if _stopped(stop_event):
            return []
        appearance = _appearance_similarity(working, reference, anchor)
        if anchor.detector_score is not None:
            score = .90 * appearance + .10 * max(0.0, min(1.0, anchor.detector_score))
        else:
            template_evidence = max(0.0, min(1.0, ((anchor.template_score or 0.0) + 1) / 2))
            score = .76 * appearance + .24 * template_evidence
        if score >= MIN_APPEARANCE_SCORE:
            ranked.append(PersonCandidate(
                box=anchor.box,
                score=float(score),
                detector_score=anchor.detector_score,
                source=anchor.source,
                appearance_score=float(appearance),
                template_score=anchor.template_score,
            ))
    deduplicated: list[PersonCandidate] = []
    for candidate in sorted(ranked, key=lambda item: item.score, reverse=True):
        if all(_iou(candidate.box, current.box) < .38 for current in deduplicated):
            deduplicated.append(candidate)
        if len(deduplicated) >= MAX_DETECTOR_CANDIDATES:
            break
    if not deduplicated:
        return []
    ambiguous = len(deduplicated) > 1 and deduplicated[1].score >= deduplicated[0].score - AMBIGUOUS_MARGIN
    if ambiguous:
        first = deduplicated[0]
        deduplicated[0] = PersonCandidate(
            box=first.box,
            score=first.score,
            detector_score=first.detector_score,
            source=first.source,
            appearance_score=first.appearance_score,
            template_score=first.template_score,
            ambiguous=True,
        )
    return deduplicated
