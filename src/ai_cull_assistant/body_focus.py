"""Conservative native-resolution body focus evidence for one selected subject.

The detector and pose networks are loaded lazily per worker thread.  This file
contains no download side effects; see ``body_models/download.py`` for the
explicit repair step. The pinned baseline weights ship with the source package.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import sys
import threading
from typing import Any, Iterable

import cv2
import numpy as np
from PIL import Image, ImageOps


VERSION = "body-focus-v2"
MODEL_FILENAMES = (
    "person_detection_mediapipe_2023mar.onnx",
    "pose_estimation_mediapipe_2023mar.onnx",
)
MODEL_SHA256 = (
    "47fd5599d6fa17608f03e0eb0ae230baa6e597d7e8a2c8199fe00abea55a701f",
    "9d89c599319a18fb7d2e28451a883476164543182bafca5f09eb2cf767ed2f3f",
)

_worker_state = threading.local()


class BodyModelUnavailable(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _model_directories() -> Iterable[Path]:
    configured = os.environ.get("AI_CULL_BODY_MODEL_DIR")
    if configured:
        yield Path(configured)
    package = Path(__file__).resolve().parent
    yield package / "data" / "body_models"
    # Backward compatibility for older checkouts; builds use tracked assets only.
    yield package.parents[1] / "build" / "body_models"
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        yield Path(bundle) / "ai_cull_assistant" / "data" / "body_models"


def find_body_models() -> tuple[Path, Path]:
    """Find and validate both pinned model files without downloading them."""
    invalid: list[str] = []
    for directory in _model_directories():
        paths = tuple(directory / name for name in MODEL_FILENAMES)
        if not all(path.is_file() for path in paths):
            continue
        if all(_sha256(path) == digest for path, digest in zip(paths, MODEL_SHA256)):
            return paths  # type: ignore[return-value]
        invalid.append(str(directory))
    suffix = f"（校验失败：{', '.join(invalid)}）" if invalid else ""
    raise BodyModelUnavailable("找不到已校验的身体检测模型" + suffix)


def _ssd_anchors() -> np.ndarray:
    """Generate MediaPipe's fixed 2254 BlazePose detector anchors."""
    anchors: list[tuple[float, float]] = []
    strides = (8, 16, 32, 32, 32)
    layer = 0
    while layer < len(strides):
        same_stride = 1
        while layer + same_stride < len(strides) and strides[layer + same_stride] == strides[layer]:
            same_stride += 1
        stride = strides[layer]
        feature = math.ceil(224 / stride)
        anchors_per_cell = 2 * same_stride
        for y in range(feature):
            for x in range(feature):
                center = ((x + 0.5) / feature, (y + 0.5) / feature)
                anchors.extend([center] * anchors_per_cell)
        layer += same_stride
    result = np.asarray(anchors, dtype=np.float32)
    if result.shape != (2254, 2):
        raise AssertionError(f"unexpected anchor shape {result.shape}")
    return result


class _PersonDetector:
    def __init__(self, model_path: Path, score_threshold: float = 0.5):
        # OpenCV's native filename loader cannot reliably open Chinese paths on Windows.
        self.net = cv2.dnn.readNetFromONNX(np.frombuffer(model_path.read_bytes(), dtype=np.uint8))
        self.score_threshold = score_threshold
        self.anchors = _ssd_anchors()

    def infer(self, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        scale = min(224 / height, 224 / width)
        resized = cv2.resize(image, (round(width * scale), round(height * scale)))
        pad_x = (224 - resized.shape[1]) // 2
        pad_y = (224 - resized.shape[0]) // 2
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 127.5 - 1.0
        canvas = np.zeros((224, 224, 3), dtype=np.float32)
        canvas[pad_y:pad_y + resized.shape[0], pad_x:pad_x + resized.shape[1]] = rgb
        self.net.setInput(np.transpose(canvas, (2, 0, 1))[None])
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        regression, logits = outputs[0], outputs[1]
        scores = 1.0 / (1.0 + np.exp(-np.clip(logits[0, :, 0], -80, 80)))
        delta = regression[0]
        normalizer = np.array([224.0, 224.0], dtype=np.float32)
        centers = delta[:, :2] / normalizer + self.anchors
        sizes = delta[:, 2:4] / normalizer
        boxes = np.c_[centers - sizes / 2, centers + sizes / 2] * 224
        landmarks = delta[:, 4:].reshape(-1, 4, 2) / normalizer + self.anchors[:, None, :]
        landmarks *= 224
        boxes[:, (0, 2)] -= pad_x
        boxes[:, (1, 3)] -= pad_y
        landmarks[:, :, 0] -= pad_x
        landmarks[:, :, 1] -= pad_y
        boxes /= scale
        landmarks /= scale
        keep = np.flatnonzero(scores >= self.score_threshold)
        if not len(keep):
            return np.empty((0, 13), dtype=np.float32)
        candidates = boxes[keep]
        # OpenCV accepts x/y/w/h here; the upstream example passes xyxy, which
        # over-suppresses distant detections.  Convert explicitly.
        nms_boxes = np.c_[candidates[:, :2], candidates[:, 2:] - candidates[:, :2]].tolist()
        picked = cv2.dnn.NMSBoxes(nms_boxes, scores[keep].tolist(), self.score_threshold, 0.3)
        if len(picked) == 0:
            return np.empty((0, 13), dtype=np.float32)
        selected = keep[np.asarray(picked).reshape(-1)]
        return np.c_[boxes[selected], landmarks[selected].reshape(-1, 8), scores[selected]].astype(np.float32)


class _PoseEstimator:
    def __init__(self, model_path: Path, confidence: float = 0.5):
        self.net = cv2.dnn.readNetFromONNX(np.frombuffer(model_path.read_bytes(), dtype=np.uint8))
        self.confidence = confidence

    def infer(self, image: np.ndarray, person: np.ndarray) -> dict[str, Any] | None:
        hip = person[4:6].astype(np.float32)
        full = person[6:8].astype(np.float32)
        radius = float(np.linalg.norm(hip - full))
        if not np.isfinite(radius) or radius < 8:
            return None
        raw_box = np.array([hip - radius, hip + radius], dtype=np.float32)
        left, top = np.floor(raw_box[0]).astype(int)
        right, bottom = np.ceil(raw_box[1]).astype(int)
        ih, iw = image.shape[:2]
        clip = (max(left, 0), max(top, 0), min(right, iw), min(bottom, ih))
        if clip[2] <= clip[0] or clip[3] <= clip[1]:
            return None
        crop = image[clip[1]:clip[3], clip[0]:clip[2]]
        pads = (clip[0] - left, clip[1] - top, right - clip[2], bottom - clip[3])
        crop = cv2.copyMakeBorder(crop, pads[1], pads[3], pads[0], pads[2], cv2.BORDER_CONSTANT)
        bias = np.array([left, top], dtype=np.float32)
        local_hip = hip - bias
        local_full = full - bias
        radians = np.pi / 2 - np.arctan2(-(local_full[1] - local_hip[1]), local_full[0] - local_hip[0])
        angle = float(np.rad2deg(radians - 2 * np.pi * np.floor((radians + np.pi) / (2 * np.pi))))
        rotation = cv2.getRotationMatrix2D(tuple(local_hip), angle, 1.0)
        rotated = cv2.warpAffine(crop, rotation, (crop.shape[1], crop.shape[0]))
        blob = cv2.resize(rotated, (256, 256), interpolation=cv2.INTER_AREA)
        blob = cv2.cvtColor(blob, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        self.net.setInput(blob[None])
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        landmarks, confidence, raw_mask = outputs[0], outputs[1], outputs[2]
        score = float(confidence.reshape(-1)[0])
        if score < self.confidence:
            return None
        points = landmarks[0].reshape(-1, 5)
        points[:, 3:] = 1.0 / (1.0 + np.exp(-points[:, 3:]))
        scale = np.array([rotated.shape[1] / 256, rotated.shape[0] / 256], dtype=np.float32)
        local_xy = (points[:, :2] - 128.0) * scale
        coord_rotation = cv2.getRotationMatrix2D((0, 0), angle, 1.0)[:, :2]
        local_xy = local_xy @ coord_rotation
        rc = np.array([rotated.shape[1] / 2, rotated.shape[0] / 2, 1.0])
        inv = cv2.invertAffineTransform(rotation)
        original_center = rc @ inv.T
        points[:, :2] = local_xy + original_center + bias

        mask = raw_mask[0].reshape(256, 256)
        unrotate = cv2.getRotationMatrix2D((128, 128), -angle, 1.0)
        mask = cv2.warpAffine(mask, unrotate, (256, 256))
        mask = cv2.resize(mask, (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_LINEAR)
        full_mask = np.zeros((ih, iw), dtype=np.uint8)
        sx0, sy0 = max(-left, 0), max(-top, 0)
        sx1, sy1 = sx0 + clip[2] - clip[0], sy0 + clip[3] - clip[1]
        full_mask[clip[1]:clip[3], clip[0]:clip[2]] = (mask[sy0:sy1, sx0:sx1] > 0).astype(np.uint8) * 255
        return {"landmarks": points[:33].copy(), "mask": full_mask, "confidence": score,
                "mask_kind": "binary_foreground", "instance_ownership_verified": False}


def _get_models() -> tuple[_PersonDetector, _PoseEstimator]:
    current = getattr(_worker_state, "models", None)
    if current is None:
        detector_path, pose_path = find_body_models()
        current = (_PersonDetector(detector_path), _PoseEstimator(pose_path))
        _worker_state.models = current
    return current


def _iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    xy1 = np.maximum(box_a[:2], box_b[:2])
    xy2 = np.minimum(box_a[2:], box_b[2:])
    intersection = float(np.prod(np.maximum(xy2 - xy1, 0)))
    union = float(np.prod(np.maximum(box_a[2:] - box_a[:2], 0))) + float(np.prod(np.maximum(box_b[2:] - box_b[:2], 0))) - intersection
    return intersection / max(union, 1e-6)


def _associate_person(detections: np.ndarray, face_px: np.ndarray) -> tuple[np.ndarray | None, dict[str, Any]]:
    if detections is None or len(detections) == 0:
        return None, {"candidate_count": 0, "association_score": 0.0}
    face_center = (face_px[:2] + face_px[2:]) / 2
    face_diag = max(float(np.linalg.norm(face_px[2:] - face_px[:2])), 1.0)
    ranked: list[tuple[float, np.ndarray, float, float]] = []
    for row in detections:
        detected_face = row[:4]
        overlap = _iou(face_px, detected_face)
        center = (detected_face[:2] + detected_face[2:]) / 2
        distance = float(np.linalg.norm(center - face_center) / face_diag)
        score = overlap * 2.0 + max(0.0, 1.0 - distance) + float(row[-1]) * 0.10
        ranked.append((score, row, overlap, distance))
    ranked.sort(key=lambda item: item[0], reverse=True)
    score, row, overlap, distance = ranked[0]
    diagnostics = {
        "candidate_count": len(ranked), "association_score": score,
        "face_iou": overlap, "normalized_center_distance": distance,
    }
    if overlap < 0.05 and distance > 0.75:
        return None, diagnostics
    if len(ranked) > 1 and score - ranked[1][0] < 0.08:
        diagnostics["ambiguous"] = True
        return None, diagnostics
    return row, diagnostics


def _segment_box(a: np.ndarray, b: np.ndarray, image_shape: tuple[int, int], width_factor: float = 0.22) -> tuple[int, int, int, int] | None:
    length = float(np.linalg.norm(a - b))
    if not np.isfinite(length) or length < 24:
        return None
    margin = max(12.0, length * width_factor)
    low = np.minimum(a, b) - margin
    high = np.maximum(a, b) + margin
    height, width = image_shape
    x0, y0 = np.maximum(np.floor(low).astype(int), 0)
    x1, y1 = np.minimum(np.ceil(high).astype(int), (width, height))
    return (x0, y0, x1, y1) if x1 - x0 >= 32 and y1 - y0 >= 32 else None


def _pose_regions(landmarks: np.ndarray, image_shape: tuple[int, int]) -> list[dict[str, Any]]:
    # MediaPipe: shoulders 11/12, elbows 13/14, wrists 15/16,
    # hips 23/24, knees 25/26, ankles 27/28.
    result: list[dict[str, Any]] = []

    def visible(index: int) -> bool:
        point = landmarks[index]
        return bool(np.isfinite(point[:2]).all() and point[3] >= 0.55 and point[4] >= 0.55)

    torso_ids = (11, 12, 23, 24)
    if all(visible(i) for i in torso_ids):
        xy = landmarks[list(torso_ids), :2]
        diagonal = np.linalg.norm(xy.max(axis=0) - xy.min(axis=0))
        margin = max(12.0, diagonal * 0.10)
        low, high = xy.min(axis=0) - margin, xy.max(axis=0) + margin
        height, width = image_shape
        x0, y0 = np.maximum(np.floor(low).astype(int), 0)
        x1, y1 = np.minimum(np.ceil(high).astype(int), (width, height))
        if x1 - x0 >= 48 and y1 - y0 >= 48:
            result.append({"name": "torso", "family": "torso", "box": (x0, y0, x1, y1)})

    segments = (
        ("left_upper_arm", "left_arm", 11, 13), ("left_lower_arm", "left_arm", 13, 15),
        ("right_upper_arm", "right_arm", 12, 14), ("right_lower_arm", "right_arm", 14, 16),
        ("left_upper_leg", "left_leg", 23, 25), ("left_lower_leg", "left_leg", 25, 27),
        ("right_upper_leg", "right_leg", 24, 26), ("right_lower_leg", "right_leg", 26, 28),
    )
    for name, family, first, second in segments:
        if not (visible(first) and visible(second)):
            continue
        # Trim the terminal 12% so wrists/ankles, fingers, and footwear do not
        # dominate the decision.
        start, end = landmarks[first, :2], landmarks[second, :2]
        start, end = start * 0.88 + end * 0.12, start * 0.12 + end * 0.88
        box = _segment_box(start, end, image_shape)
        if box:
            result.append({"name": name, "family": family, "box": box})
    return result


def _region_metrics(image: np.ndarray, mask: np.ndarray, box: tuple[int, int, int, int]) -> dict[str, Any]:
    x0, y0, x1, y1 = box
    crop = image[y0:y1, x0:x1]
    valid = mask[y0:y1, x0:x1] > 0
    if crop.size == 0 or valid.size == 0:
        return {"state": "insufficient", "reason": "empty_region", "texture_sufficiency": "unknown"}
    # Suppress the segmentation outline, which otherwise looks like a sharp
    # subject edge even when the subject itself is blurred.
    valid = cv2.erode(valid.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    if int(valid.sum()) < 1024 or valid.mean() < 0.20:
        return {"state": "insufficient", "reason": "insufficient_subject_pixels", "texture_sufficiency": "unknown"}
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    values = gray[valid]
    p05, p95 = np.percentile(values, (5, 95))
    contrast = float(p95 - p05)
    fine = cv2.GaussianBlur(gray, (0, 0), 0.65)
    coarse = cv2.GaussianBlur(gray, (0, 0), 1.60)
    gx = cv2.Sobel(fine, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(fine, cv2.CV_32F, 0, 1, ksize=3)
    gradient = np.hypot(gx, gy)
    lap = np.abs(cv2.Laplacian(fine, cv2.CV_32F, ksize=3))
    coarse_lap = np.abs(cv2.Laplacian(coarse, cv2.CV_32F, ksize=3))
    useful = valid & (gradient >= max(float(np.percentile(gradient[valid], 70)), 0.025))
    texture_fraction = float(useful.sum() / max(valid.sum(), 1))
    lap_norm = float(np.mean(lap[valid] ** 2) / max(contrast * contrast, 0.0025))
    coarse_norm = float(np.mean(coarse_lap[valid] ** 2) / max(contrast * contrast, 0.0025))
    curvature = float(np.mean(lap[useful]) / (np.mean(gradient[useful]) + 1e-6)) if useful.any() else 0.0
    gx_energy = float(np.mean(gx[valid] ** 2))
    gy_energy = float(np.mean(gy[valid] ** 2))
    anisotropy = max(gx_energy, gy_energy) / max(min(gx_energy, gy_energy), 1e-8)

    # A region is severe only when adequately textured pixels lose fine detail
    # at both scales.  Low texture and anisotropy are diagnostics, never sole
    # rejection evidence.
    if contrast < 0.10 or texture_fraction < 0.08:
        state, reason = "insufficient", "low_texture_or_contrast"
    elif lap_norm < 0.0011 and coarse_norm < 0.00042 and curvature < 0.115:
        state, reason = "severe_blur", "weak_detail_at_both_scales"
    elif lap_norm >= 0.0038 and coarse_norm >= 0.00072 and curvature >= 0.16:
        state, reason = "clear", "strong_consistent_detail"
    else:
        state, reason = "uncertain", "borderline_body_detail"
    return {
        "state": state, "reason": reason, "contrast": contrast,
        "texture_sufficiency": "insufficient" if contrast < 0.10 or texture_fraction < 0.08 else "sufficient",
        "texture_fraction": texture_fraction, "laplacian_normalized": lap_norm,
        "coarse_laplacian_normalized": coarse_norm, "edge_curvature": curvature,
        "edge_anisotropy": anisotropy,
    }


def _region_ownership(box: Iterable[int], person: np.ndarray | None,
                      detections: np.ndarray | None) -> dict[str, Any]:
    """Conservative geometric support, never semantic instance ownership proof.

    Detector rectangles describe heads. A competing person's hip/full-body
    radius is a deliberately broad exclusion envelope, including limbs that a
    failed or occluded pose could miss. Invalid competitor geometry fails closed.
    """
    base = {"basis": "geometric_association", "semantic_ownership_verified": False,
            "mask_kind": "binary_foreground", "competing_envelopes": []}
    if person is None or detections is None or not len(detections):
        return dict(base, status="unverified", reason="association_not_available")
    if len(person) != 13 or not np.isfinite(person).all() or not any(np.array_equal(row, person) for row in detections):
        return dict(base, status="unverified", reason="association_not_available")
    bounds = np.asarray(box, dtype=float)
    skipped_selected = False
    for other in detections:
        if not skipped_selected and np.array_equal(other, person):
            skipped_selected = True
            continue
        if len(other) != 13 or not np.isfinite(other).all():
            return dict(base, status="unverified", reason="competitor_geometry_unavailable")
        hip, full = other[4:6], other[6:8]
        radius = float(np.linalg.norm(hip - full))
        if radius < 8:
            return dict(base, status="unverified", reason="competitor_geometry_unavailable")
        envelope = np.r_[np.minimum(hip - radius, other[:2]),
                         np.maximum(hip + radius, other[2:4])]
        base["competing_envelopes"].append([float(v) for v in envelope])
        if np.all(np.minimum(bounds[2:], envelope[2:]) > np.maximum(bounds[:2], envelope[:2])):
            return dict(base, status="ambiguous", reason="competing_person_envelope_overlap")
    return dict(base, status="geometrically_supported", reason="no_competing_person_envelope_overlap")


def _measured_region(image: np.ndarray, pose: dict[str, Any], spec: dict[str, Any],
                     person: np.ndarray, detections: np.ndarray) -> dict[str, Any]:
    """Keep measured detail separate from geometric permission to use it."""
    height, width = image.shape[:2]
    box = [int(value) for value in spec["box"]]
    ownership = _region_ownership(box, person, detections)
    measured = _region_metrics(image, pose["mask"], spec["box"])
    evidence = dict(measured, measurement_state=measured["state"], measurement_reason=measured["reason"])
    if ownership["status"] != "geometrically_supported":
        evidence.update(state="insufficient", reason="region_ownership_" + ownership["status"])
    return {
        **spec, "box": box, "evidence": evidence,
        "coordinate_space": "exif_transposed_image_pixels_xyxy",
        "coordinate_image_size": [width, height],
        "measurement_image_size": [width, height], "measurement_box": list(box),
        "measurement_origin_px": [0, 0], "ownership": ownership,
        "visibility": "unknown", "occlusion": "unknown",
        "texture_sufficiency": measured.get("texture_sufficiency", "unknown"),
        "version": VERSION,
    }


def _region_is_usable(region: dict[str, Any]) -> bool:
    # Existing legacy results stay readable; all newly produced regions have an
    # explicit ownership gate. This is not an upgrade of old measurement data.
    ownership = region.get("ownership")
    if region.get("version") == VERSION and not isinstance(ownership, dict):
        return False
    if ownership is not None and (not isinstance(ownership, dict) or
                                  ownership.get("status") != "geometrically_supported"):
        return False
    return region.get("evidence", {}).get("state") in {"clear", "uncertain", "severe_blur"}


def _aggregate_regions(regions: list[dict[str, Any]]) -> tuple[str, list[str]]:
    ownership_unresolved = any(
        (region.get("version") == VERSION or "ownership" in region) and
        (not isinstance(region.get("ownership"), dict) or
         region["ownership"].get("status") != "geometrically_supported")
        for region in regions
    )
    ownership_reasons = ["body_region_ownership_unresolved"] if ownership_unresolved else []
    usable = [region for region in regions if _region_is_usable(region)]
    severe = [region for region in usable if region["evidence"]["state"] == "severe_blur"]
    clear = [region for region in usable if region["evidence"]["state"] == "clear"]
    borderline = [region for region in usable if region["evidence"]["state"] == "uncertain"]
    severe_families = {region["family"] for region in severe}
    clear_families = {region["family"] for region in clear}
    if ("torso" in severe_families and len(severe_families) >= 2) or len(severe_families) >= 3:
        return "severe_blur", ["multiple_major_body_regions_severely_blurred"] + ownership_reasons
    if severe:
        return "uncertain", ["isolated_body_blur_evidence"] + ownership_reasons
    if borderline:
        return "uncertain", ["at_least_one_major_body_region_uncertain"] + ownership_reasons
    # Excluding unsafe pixels must never manufacture a clear result from the
    # remaining regions. Reliable multi-family severe evidence above still wins.
    if any(not _region_is_usable(region) for region in regions):
        return "uncertain", ["insufficient_consistent_body_evidence"] + ownership_reasons
    if len(clear_families) >= 2:
        return "clear", ["multiple_major_body_regions_clear"] + ownership_reasons
    return "uncertain", ["insufficient_consistent_body_evidence"] + ownership_reasons


def _headshot(face: tuple[float, float, float, float]) -> bool:
    _x, y, width, height = face
    return height >= 0.32 or (width * height >= 0.075 and y + height >= 0.52)


def assess_body_focus(image: Image.Image, face: tuple[float, float, float, float]) -> dict[str, Any]:
    """Assess major-body sharpness for the person matching ``face``.

    ``face`` uses normalized ``(x, y, width, height)`` coordinates.  The
    returned ``state`` is ``clear``, ``severe_blur``, or ``uncertain``.
    ``clear`` on a headshot means the unavailable body does not block the
    already-separate face decision; it does not claim invisible limbs are sharp.
    """
    result: dict[str, Any] = {
        "version": VERSION, "state": "uncertain", "reasons": [],
        "review_kind": "unsupported", "regions": [], "diagnostics": {},
    }
    if not isinstance(image, Image.Image):
        result["reasons"] = ["invalid_image"]
        return result
    try:
        x, y, fw, fh = map(float, face)
    except (TypeError, ValueError):
        result["reasons"] = ["invalid_face_box"]
        return result
    if not all(np.isfinite((x, y, fw, fh))) or fw <= 0 or fh <= 0 or x >= 1 or y >= 1 or x + fw <= 0 or y + fh <= 0:
        result["reasons"] = ["invalid_face_box"]
        return result
    rgb = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    face_px = np.array([x * width, y * height, (x + fw) * width, (y + fh) * height], dtype=np.float32)
    face_px[(0, 2),] = np.clip(face_px[(0, 2),], 0, width)
    face_px[(1, 3),] = np.clip(face_px[(1, 3),], 0, height)
    result["diagnostics"].update({"source_size": [width, height], "face_box_px": face_px.tolist()})
    try:
        detector, pose = _get_models()
        detections = detector.infer(bgr)
        person, association = _associate_person(detections, face_px)
        result["diagnostics"].update(association)
        if person is None:
            if _headshot((x, y, fw, fh)):
                result.update(state="clear", review_kind="none", reasons=["body_not_visible_headshot"])
            else:
                result["reasons"] = ["selected_person_not_associated"]
            return result
        pose_result = pose.infer(bgr, person)
        if pose_result is None:
            if _headshot((x, y, fw, fh)):
                result.update(state="clear", review_kind="none", reasons=["body_not_visible_headshot"])
            else:
                result["reasons"] = ["pose_not_reliable"]
            return result
        result["diagnostics"]["person_confidence"] = float(person[-1])
        result["diagnostics"]["pose_confidence"] = pose_result["confidence"]
        region_specs = _pose_regions(pose_result["landmarks"], (height, width))
        for spec in region_specs:
            result["regions"].append(_measured_region(bgr, pose_result, spec, person, detections))
        if not region_specs and _headshot((x, y, fw, fh)):
            result.update(state="clear", review_kind="none", reasons=["body_not_visible_headshot"])
            return result
        state, reasons = _aggregate_regions(result["regions"])
        if state == "severe_blur":
            review_kind = "motion_confirmed"
        elif state == "uncertain" and any(
            region["evidence"]["state"] in {"uncertain", "severe_blur"}
            for region in result["regions"]
        ):
            review_kind = "motion_suspected"
        else:
            review_kind = "none" if state == "clear" else "unsupported"
        result.update(state=state, review_kind=review_kind, reasons=reasons)
        return result
    except BodyModelUnavailable as exc:
        result["reasons"] = ["body_models_unavailable"]
        result["diagnostics"]["error"] = str(exc)
        return result
    except (cv2.error, ValueError, OverflowError) as exc:
        result["reasons"] = ["body_analysis_failed"]
        result["diagnostics"]["error"] = type(exc).__name__
        return result


def body_review_boxes(evidence: dict[str, Any], limit: int = 3) -> list[tuple[int, int, int, int]]:
    """Return native-pixel crops that substantiate a body-motion review."""
    if evidence.get("review_kind") not in {"motion_suspected", "motion_confirmed"}:
        return []
    rank = {"severe_blur": 0, "uncertain": 1}
    candidates = [
        region for region in evidence.get("regions", [])
        if _region_is_usable(region) and region.get("evidence", {}).get("state") in rank
    ]
    candidates.sort(key=lambda region: (
        rank[region["evidence"]["state"]],
        0 if region.get("family") == "torso" else 1,
        -(region["box"][2] - region["box"][0]) * (region["box"][3] - region["box"][1]),
    ))
    return [tuple(map(int, region["box"])) for region in candidates[:max(0, int(limit))]]


def detect_body_candidates(image: Image.Image, *, max_people: int = 8) -> list[dict[str, Any]]:
    """Locate actual body extents using the already-bundled pose model.

    MediaPipe's detector rectangle is a head rectangle, not a body rectangle.
    Only a supported pose may produce a visible body candidate. All coordinates
    exposed here are normalized xywh; neither candidates nor body measurements
    claim that a face or an eye is visible.
    """
    if not isinstance(image, Image.Image):
        return []
    bgr = cv2.cvtColor(np.asarray(ImageOps.exif_transpose(image).convert('RGB')), cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    try:
        detector, pose = _get_models()
        detections = detector.infer(bgr)
        if detections is None:
            return []
        found = []
        for person in sorted(detections, key=lambda row: float(row[-1]), reverse=True)[:max_people]:
            if len(person) != 13 or not np.isfinite(person).all() or float(person[-1]) < .65:
                continue
            estimated = pose.infer(bgr, person)
            if estimated is None or estimated.get('confidence', 0) < .55:
                continue
            points = np.asarray(estimated['landmarks'])
            if points.shape[0] < 29 or points.shape[1] < 5:
                continue
            major = points[[11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]]
            visible = np.isfinite(major).all(axis=1) & (major[:, 3] >= .55) & (major[:, 4] >= .55)
            if int(visible.sum()) < 4:
                continue
            xy = major[visible, :2]
            low, high = xy.min(axis=0), xy.max(axis=0)
            if min(high - low) < 20 or (high[1] - low[1]) < .06 * height:
                continue
            # Include the detector's head extent for a complete subject display,
            # but never relabel that extent as detected facial evidence.
            low = np.minimum(low, person[:2])
            high = np.maximum(high, person[2:4])
            margin = np.maximum((high - low) * .06, 4)
            low = np.maximum(low - margin, (0, 0))
            high = np.minimum(high + margin, (width, height))
            if min(high - low) <= 0:
                continue
            box = (float(low[0] / width), float(low[1] / height),
                   float((high[0] - low[0]) / width), float((high[1] - low[1]) / height))
            if any(_iou(np.array([box[0], box[1], box[0]+box[2], box[1]+box[3]]),
                        np.array([other['box'][0], other['box'][1], other['box'][0]+other['box'][2], other['box'][1]+other['box'][3]])) > .70
                   for other in found):
                continue
            regions = []
            for spec in _pose_regions(points, (height, width)):
                regions.append(_measured_region(bgr, estimated, spec, person, detections))
            state, reasons = _aggregate_regions(regions)
            found.append({'box': box, 'source': 'mediapipe_pose',
                          'confidence': min(float(person[-1]), float(estimated['confidence'])),
                          'state': state, 'reasons': reasons, 'regions': regions, 'version': VERSION,
                          'evidence_kind': 'body', 'eye_evidence': False,
                          'source_size': [width, height],
                          'head_box': [float(person[0]/width),float(person[1]/height),
                                       float((person[2]-person[0])/width),float((person[3]-person[1])/height)]})
        return found
    except (BodyModelUnavailable, cv2.error, OSError, ValueError, OverflowError):
        return []


def assess_body_region(image: Image.Image, box: tuple[float, float, float, float]) -> dict[str, Any]:
    """Measure a manually selected body only when pose/segmentation supports it."""
    from .subject import _valid_normalized_box
    result = {'box': tuple(box), 'source': 'manual', 'evidence_kind': 'body', 'version': VERSION,
              'eye_evidence': False, 'state': 'uncertain', 'regions': [],
              'reasons': ['manual_body_without_reliable_pose'], 'confidence': None}
    if not _valid_normalized_box(box):
        result['reasons'] = ['invalid_body_box']
        return result
    image = ImageOps.exif_transpose(image)
    width, height = image.size
    x, y, w, h = box
    bounds = (round(x*width), round(y*height), round((x+w)*width), round((y+h)*height))
    with image.crop(bounds) as crop:
        candidates = detect_body_candidates(crop)
    if len(candidates) != 1:
        if len(candidates) > 1:
            result['reasons'] = ['multiple_people_in_manual_body_box']
        return result
    candidate = candidates[0]
    # Require meaningful correspondence, rather than measuring a tiny bystander
    # inside a large user-selected subject rectangle.
    if candidate['box'][2] * candidate['box'][3] < .12:
        return result
    result.update(state=candidate['state'], reasons=candidate['reasons'],
                  confidence=candidate['confidence'], source_size=[width, height])
    if candidate.get('head_box'):
        hx,hy,hw,hh=candidate['head_box']
        crop_width, crop_height = bounds[2]-bounds[0], bounds[3]-bounds[1]
        result['head_box'] = [(bounds[0]+hx*crop_width)/width, (bounds[1]+hy*crop_height)/height,
                              hw*crop_width/width, hh*crop_height/height]
    result['version'] = VERSION
    result['regions'] = [dict(region, box=[region['box'][0]+bounds[0], region['box'][1]+bounds[1],
                                         region['box'][2]+bounds[0], region['box'][3]+bounds[1]],
                             coordinate_image_size=[width, height],
                             measurement_origin_px=[bounds[0], bounds[1]])
                         for region in candidate['regions']]
    for region in result['regions']:
        region['evidence'] = dict(region['evidence'])
        region['ownership'] = dict(region.get('ownership', {'status': 'unverified'}))
        region['ownership']['competing_envelopes'] = [
            [b[0]+bounds[0], b[1]+bounds[1], b[2]+bounds[0], b[3]+bounds[1]]
            for b in region['ownership'].get('competing_envelopes', [])]
    # Cropping can hide a neighboring person's head while retaining their arm.
    # Recheck competition in the full image; crop-local uniqueness is not enough.
    try:
        detector, _pose = _get_models()
        full_bgr = cv2.cvtColor(np.asarray(image.convert('RGB')), cv2.COLOR_RGB2BGR)
        detections = detector.infer(full_bgr)
        head = result.get('head_box')
        selected = None
        if head:
            hx, hy, hw, hh = head
            face_px = np.asarray([hx*width, hy*height, (hx+hw)*width, (hy+hh)*height])
            selected, _association = _associate_person(detections, face_px)
        for region in result['regions']:
            ownership = _region_ownership(region['box'], selected, detections)
            region['full_image_ownership'] = ownership
            if ownership['status'] != 'geometrically_supported':
                region['ownership'] = ownership
                region['evidence'].update(state='insufficient', reason='region_ownership_' + ownership['status'])
    except (BodyModelUnavailable, cv2.error, OSError, ValueError, OverflowError):
        for region in result['regions']:
            region['ownership'] = {'status': 'unverified', 'basis': 'full_image_check_unavailable',
                                   'semantic_ownership_verified': False}
            region['evidence'].update(state='insufficient', reason='region_ownership_unverified')
    result['state'], result['reasons'] = _aggregate_regions(result['regions'])
    return result
