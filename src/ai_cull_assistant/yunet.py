"""Offline YuNet face detection with conservative landmark validation."""
from dataclasses import dataclass
from pathlib import Path
import threading

import cv2
import numpy as np

MODEL_PATH = Path(__file__).with_name("data") / "face_detection_yunet_2023mar.onnx"
_local = threading.local()


@dataclass(frozen=True)
class FaceDetection:
    box: tuple[float, float, float, float]
    landmarks: tuple[tuple[float, float], ...]
    score: float


def detector():
    if not hasattr(_local, "detector"):
        # Read bytes in Python so Chinese installation paths work on Windows.
        weights = np.frombuffer(MODEL_PATH.read_bytes(), dtype=np.uint8)
        _local.detector = cv2.FaceDetectorYN.create(
            "onnx", weights, np.empty(0, dtype=np.uint8), (320, 320), .9, .3, 5000)
    return _local.detector


def valid_detection(row: np.ndarray, width: int, height: int, score_threshold: float = .9) -> bool:
    if len(row) != 15 or not np.isfinite(row).all() or row[14] < score_threshold:
        return False
    x, y, w, h = row[:4]
    if min(w, h) < 12 or not .4 <= w / h <= 1.8:
        return False
    # Require the face itself to lie mostly within the source image.
    if x < -w * .1 or y < -h * .1 or x + w > width + w * .1 or y + h > height + h * .1:
        return False
    points = row[4:14].reshape(5, 2)
    if ((points < (x - .15 * w, y - .15 * h)) | (points > (x + 1.15 * w, y + 1.15 * h))).any():
        return False
    eyes = points[:2].mean(axis=0)
    mouth = points[3:].mean(axis=0)
    vertical = mouth - eyes
    distance = np.linalg.norm(vertical)
    eye_gap = np.linalg.norm(points[0] - points[1])
    if not .12 * h <= distance <= .85 * h or not .08 * w <= eye_gap <= 1.1 * w:
        return False
    # Nose should lie between the eye and mouth levels, including tilted faces.
    nose_projection = np.dot(points[2] - eyes, vertical) / (distance * distance)
    return bool(-.2 <= nose_projection <= 1.3)


def _map_from_quarter_turn(
    point: tuple[float, float], turns: int, width: int, height: int,
) -> tuple[float, float]:
    """Map a point from a ``np.rot90`` image back to the unrotated image."""
    x, y = point
    turns %= 4
    if turns == 1:
        return width - y, x
    if turns == 2:
        return width - x, height - y
    if turns == 3:
        return y, height - x
    return x, y


def _map_detection(row: np.ndarray, turns: int, width: int, height: int) -> FaceDetection:
    x, y, w, h = map(float, row[:4])
    corners = [
        _map_from_quarter_turn(point, turns, width, height)
        for point in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))
    ]
    low = np.min(corners, axis=0)
    high = np.max(corners, axis=0)
    points = tuple(
        _map_from_quarter_turn((float(px), float(py)), turns, width, height)
        for px, py in row[4:14].reshape(5, 2)
    )
    return FaceDetection(
        (float(low[0]), float(low[1]), float(high[0] - low[0]), float(high[1] - low[1])),
        points,
        float(row[14]),
    )


def _iou(a: FaceDetection, b: FaceDetection) -> float:
    ax, ay, aw, ah = a.box
    bx, by, bw, bh = b.box
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return intersection / max(aw * ah + bw * bh - intersection, 1e-6)


def _deduplicate(candidates: list[FaceDetection]) -> list[FaceDetection]:
    selected: list[FaceDetection] = []
    for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
        if all(_iou(candidate, current) < .35 for current in selected):
            selected.append(candidate)
    return selected


def detect(image: np.ndarray, score_threshold: float = .9) -> list[FaceDetection]:
    from .scan_diagnostics import operation
    h, w = image.shape[:2]
    if not h or not w:
        return []
    scale = min(1.0, 1600 / max(h, w))
    resized = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale)))) if scale < 1 else image
    rh, rw = resized.shape[:2]
    model = detector()
    model.setScoreThreshold(score_threshold)
    results: list[FaceDetection] = []

    def run(turns: int) -> None:
        rotated = np.ascontiguousarray(np.rot90(resized, turns)) if turns else resized
        rotated_h, rotated_w = rotated.shape[:2]
        model.setInputSize((rotated_w, rotated_h))
        with operation('yunet_rotation' if turns else 'yunet_normal'):
            _, rows = model.detect(rotated)
        if rows is None:
            return
        for row in rows:
            if valid_detection(row, rotated_w, rotated_h, score_threshold):
                results.append(_map_detection(row, turns, rw, rh))

    run(0)
    # RAW embedded previews and images from some cameras can reach this layer
    # without orientation metadata.  Only pay for the extra passes when the
    # first pass has no reasonably prominent face; a tiny logo-like candidate
    # must not prevent the orientation rescue.
    prominent_area = rw * rh * .002
    if not any(face.box[2] * face.box[3] >= prominent_area for face in results):
        run(1)
        run(3)
        if not any(face.box[2] * face.box[3] >= prominent_area for face in results):
            run(2)

    sx, sy = w / rw, h / rh
    scaled = [
        FaceDetection(
            (face.box[0] * sx, face.box[1] * sy, face.box[2] * sx, face.box[3] * sy),
            tuple((px * sx, py * sy) for px, py in face.landmarks),
            face.score,
        )
        for face in _deduplicate(results)
    ]
    return scaled


def head_box(face: FaceDetection, width: int, height: int) -> tuple[float, float, float, float]:
    x, y, w, h = face.box
    eye_x = (face.landmarks[0][0] + face.landmarks[1][0]) / 2
    eye_y = (face.landmarks[0][1] + face.landmarks[1][1]) / 2
    cx = .65 * (x + w / 2) + .35 * eye_x
    cy = .5 * (y + .35 * h) + .5 * (eye_y + .1 * h)
    crop_h = max(1.85 * h, 1.65 * w * 150 / 124)
    crop_w = crop_h * 124 / 150
    # Translate at edges before clipping; do not crop off the detected face.
    crop_w, crop_h = min(crop_w, width), min(crop_h, height)
    left = max(0, min(cx - crop_w / 2, width - crop_w))
    top = max(0, min(cy - crop_h / 2, height - crop_h))
    return left / width, top / height, crop_w / width, crop_h / height
