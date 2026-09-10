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


def detect(image: np.ndarray, score_threshold: float = .9) -> list[FaceDetection]:
    h, w = image.shape[:2]
    if not h or not w:
        return []
    scale = min(1.0, 1600 / max(h, w))
    resized = cv2.resize(image, (max(1, round(w * scale)), max(1, round(h * scale)))) if scale < 1 else image
    rh, rw = resized.shape[:2]
    model = detector()
    model.setScoreThreshold(score_threshold)
    model.setInputSize((rw, rh))
    _, rows = model.detect(resized)
    results = []
    if rows is not None:
        for row in rows:
            if valid_detection(row, rw, rh, score_threshold):
                sx, sy = w / rw, h / rh
                x, y, fw, fh = row[:4]
                points = tuple((float(px * sx), float(py * sy)) for px, py in row[4:14].reshape(5, 2))
                results.append(FaceDetection((float(x * sx), float(y * sy), float(fw * sx), float(fh * sy)), points, float(row[14])))
    return results


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
