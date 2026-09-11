from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np

from .models import PhotoAsset

FaceBox = tuple[int, int, int, int]


@dataclass(slots=True, frozen=True)
class ScreeningConfig:
    """Conservative thresholds: reject only obviously blurred detected faces."""

    min_face_size: int = 40
    min_face_confidence: float = 1.0
    max_detection_side: int = 720
    normalized_face_size: int = 192
    # Two independent low-detail gates reduce false positives.
    severe_laplacian_threshold: float = 4.5
    severe_tenengrad_threshold: float = 27.0
    very_low_laplacian_threshold: float = 3.5
    soft_tenengrad_threshold: float = 31.0
    downsampled_laplacian_threshold: float = 20.0
    downsampled_tenengrad_threshold: float = 22.0


@dataclass(slots=True)
class ScreeningResult:
    rejected: bool
    reason: str
    face_found: bool
    laplacian_variance: float | None = None
    tenengrad: float | None = None
    face_box: FaceBox | None = None
    analysis_version: str = "legacy-preview"
    source_size: tuple[int, int] | None = None
    detail_ratio: float | None = None
    focus_evidence: dict | None = None


DEFAULT_CONFIG = ScreeningConfig()


def assess_subject_blur(
    preview_path: str | Path,
    *,
    face_boxes: list[FaceBox] | None = None,
    config: ScreeningConfig = DEFAULT_CONFIG,
) -> ScreeningResult:
    # OpenCV's Windows path reader cannot reliably open Chinese filenames.
    try:
        image = cv2.imdecode(np.frombuffer(Path(preview_path).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    except (OSError, cv2.error):
        image = None
    if image is None:
        return ScreeningResult(False, "preview_unreadable", False)

    if face_boxes is None:
        face_boxes = detect_faces(image, config=config)
    if not face_boxes:
        return ScreeningResult(False, "no_reliable_face", False)

    face_box = choose_primary_face(face_boxes, image.shape[1], image.shape[0])
    crop = _crop_box(image, face_box)
    if crop.size == 0:
        return ScreeningResult(False, "no_reliable_face", False)

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(
        gray,
        (config.normalized_face_size, config.normalized_face_size),
        interpolation=cv2.INTER_AREA,
    )
    laplacian_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    tenengrad = float(np.sqrt(np.mean(gx * gx + gy * gy)))

    rejected = (
        laplacian_variance < config.severe_laplacian_threshold
        and tenengrad < config.severe_tenengrad_threshold
    ) or (
        laplacian_variance < config.very_low_laplacian_threshold
        and tenengrad < config.soft_tenengrad_threshold
    ) or (
        laplacian_variance < config.downsampled_laplacian_threshold
        and tenengrad < config.downsampled_tenengrad_threshold
    )
    reason = "obvious_subject_blur" if rejected else "subject_not_obviously_blurred"
    return ScreeningResult(
        rejected,
        reason,
        True,
        laplacian_variance,
        tenengrad,
        face_box,
    )


def detect_faces(image: np.ndarray, *, config: ScreeningConfig = DEFAULT_CONFIG) -> list[FaceBox]:
    from .yunet import detect
    return [tuple(round(v) for v in face.box) for face in detect(image, .8)]


def choose_primary_face(boxes: Iterable[FaceBox], image_width: int, image_height: int) -> FaceBox:
    cx = image_width / 2.0
    cy = image_height / 2.0
    diagonal = max(1.0, (image_width**2 + image_height**2) ** 0.5)

    def score(box: FaceBox) -> float:
        x, y, w, h = box
        area = float(w * h)
        fx = x + w / 2.0
        fy = y + h / 2.0
        distance = ((fx - cx) ** 2 + (fy - cy) ** 2) ** 0.5 / diagonal
        return area * (1.15 - min(distance, 0.9))

    return max(boxes, key=score)


def screen_assets(
    assets: list[PhotoAsset],
    *,
    config: ScreeningConfig = DEFAULT_CONFIG,
    face_provider: Callable[[PhotoAsset], list[FaceBox]] | None = None,
    crop_settings=None,
    cache_dir=None,
) -> dict[str, ScreeningResult]:
    results: dict[str, ScreeningResult] = {}
    for asset in assets:
        if asset.preview_path is None:
            result = ScreeningResult(False, "preview_unavailable", False)
        elif face_provider is not None:
            result = assess_subject_blur(asset.preview_path, face_boxes=face_provider(asset), config=config)
        else:
            from .face_focus import assess_asset_focus
            result = assess_asset_focus(asset, crop_settings=crop_settings, cache_dir=cache_dir)
        asset.auto_rejected = result.rejected
        asset.screening_reason = result.reason
        asset.focus_score = result.laplacian_variance
        asset.face_found = result.face_found
        results[asset.stem] = result
    return results


def save_screening_results(results: dict[str, ScreeningResult], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "policy": "only_obvious_subject_blur_auto_rejected",
        "results": {stem: asdict(result) for stem, result in results.items()},
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _crop_box(image: np.ndarray, box: FaceBox) -> np.ndarray:
    x, y, w, h = box
    ih, iw = image.shape[:2]
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(iw, x + w)
    y1 = min(ih, y + h)
    return image[y0:y1, x0:x1]
