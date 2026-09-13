"""Landmark-guided, multi-scale detail evidence for native face crops."""
from __future__ import annotations

from typing import Any, Iterable

import cv2
import numpy as np


TARGET_REGION_WIDTH = 384
MIN_NATIVE_EYE_DISTANCE = 56.0


def _validated_landmarks(
    landmarks: Iterable[Iterable[float]] | None,
    width: int,
    height: int,
) -> np.ndarray | None:
    if landmarks is None:
        return None
    try:
        points = np.asarray(tuple(landmarks), dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if points.shape != (5, 2) or not np.isfinite(points).all():
        return None
    if ((points < (-.05 * width, -.05 * height)) | (points > (1.05 * width, 1.05 * height))).any():
        return None
    eye_distance = float(np.linalg.norm(points[0] - points[1]))
    eye_mid = points[:2].mean(axis=0)
    mouth_mid = points[3:].mean(axis=0)
    face_height = float(np.linalg.norm(mouth_mid - eye_mid))
    if eye_distance < 4 or face_height < 4:
        return None
    if not .22 <= face_height / eye_distance <= 2.8:
        return None
    return points


def _bounded_crop(gray: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    height, width = gray.shape
    left = max(0, min(width - 1, int(np.floor(x0))))
    top = max(0, min(height - 1, int(np.floor(y0))))
    right = max(left + 1, min(width, int(np.ceil(x1))))
    bottom = max(top + 1, min(height, int(np.ceil(y1))))
    return gray[top:bottom, left:right]


def _regions(face_bgr: np.ndarray, landmarks: np.ndarray | None) -> tuple[dict[str, np.ndarray], float | None]:
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    height, width = gray.shape
    if landmarks is None:
        # Retained for diagnostics and old callers. The production decision
        # layer withholds a clear/reject result when real KPS is unavailable.
        return {
            "eye_band": _bounded_crop(gray, width * .05, height * .25, width * .95, height * .53),
            "face_core": _bounded_crop(gray, width * .10, height * .16, width * .90, height * .88),
        }, None

    eyes = landmarks[:2]
    eye_mid = eyes.mean(axis=0)
    eye_distance = float(np.linalg.norm(eyes[0] - eyes[1]))
    mouth_mid = landmarks[3:].mean(axis=0)
    vertical = mouth_mid - eye_mid
    vertical_norm = float(np.linalg.norm(vertical))
    down = vertical / vertical_norm if vertical_norm else np.array((0.0, 1.0), dtype=np.float32)
    across = np.array((down[1], -down[0]), dtype=np.float32)

    # Rotate only enough to align the eye line. The ROIs are then cropped from
    # one transformed native image, avoiding any upscaling before measurement.
    angle = float(np.degrees(np.arctan2(across[1], across[0])))
    matrix = cv2.getRotationMatrix2D(tuple(map(float, eye_mid)), angle, 1.0)
    aligned = cv2.warpAffine(
        gray,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )
    aligned_points = cv2.transform(landmarks[None, :, :], matrix)[0]
    aligned_eyes = aligned_points[:2]
    aligned_mid = aligned_eyes.mean(axis=0)
    left_eye_x, right_eye_x = sorted((float(aligned_eyes[0, 0]), float(aligned_eyes[1, 0])))
    y = float(aligned_mid[1])
    eye_band = _bounded_crop(
        aligned,
        left_eye_x - .42 * eye_distance,
        y - .38 * eye_distance,
        right_eye_x + .42 * eye_distance,
        y + .43 * eye_distance,
    )
    all_x = aligned_points[:, 0]
    all_y = aligned_points[:, 1]
    face_core = _bounded_crop(
        aligned,
        min(float(all_x.min()), left_eye_x) - .32 * eye_distance,
        y - .52 * eye_distance,
        max(float(all_x.max()), right_eye_x) + .32 * eye_distance,
        max(float(all_y.max()), y + .95 * eye_distance) + .35 * eye_distance,
    )
    return {"eye_band": eye_band, "face_core": face_core}, eye_distance


def _scale_metrics(gray: np.ndarray, sigma: float) -> dict[str, float]:
    smooth = cv2.GaussianBlur(gray, (0, 0), sigma)
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)
    gradient = np.hypot(gx, gy)
    laplacian = cv2.Laplacian(smooth, cv2.CV_32F, ksize=3)

    p05, p95 = np.percentile(smooth, (5, 95))
    contrast = max(float(p95 - p05), 0.05)
    gradient_p90 = float(np.percentile(gradient, 90))
    strong = gradient >= gradient_p90
    edge_curvature = float(
        np.mean(np.abs(laplacian)[strong])
        / (np.mean(gradient[strong]) + 1e-6)
    )
    energy_x = float(np.mean(gx[strong] ** 2))
    energy_y = float(np.mean(gy[strong] ** 2))
    cross = float(np.mean(2 * gx[strong] * gy[strong]))
    coherence = float(
        np.hypot(energy_x - energy_y, cross)
        / (energy_x + energy_y + 1e-8)
    )
    return {
        "contrast": contrast,
        "laplacian_normalized": float(np.var(laplacian) / (contrast * contrast)),
        "gradient_p90_normalized": gradient_p90 / contrast,
        "edge_curvature": edge_curvature,
        "orientation_coherence": coherence,
    }


def _region_metrics(gray: np.ndarray) -> dict[str, Any]:
    native_fine = _scale_metrics(gray, sigma=.55)
    native_coarse = _scale_metrics(gray, sigma=1.40)

    scale = TARGET_REGION_WIDTH / gray.shape[1]
    target_height = max(8, round(gray.shape[0] * scale))
    interpolation = cv2.INTER_AREA if scale <= 1.0 else cv2.INTER_CUBIC
    normalized = cv2.resize(gray, (TARGET_REGION_WIDTH, target_height), interpolation=interpolation)
    fine = _scale_metrics(normalized, sigma=.55)
    coarse = _scale_metrics(normalized, sigma=1.40)
    fine_to_coarse = fine["laplacian_normalized"] / max(coarse["laplacian_normalized"], 1e-8)
    gradient_retention = coarse["gradient_p90_normalized"] / max(fine["gradient_p90_normalized"], 1e-8)
    return {
        "native_size": [int(gray.shape[1]), int(gray.shape[0])],
        "resize_scale": scale,
        "fine_to_coarse_ratio": fine_to_coarse,
        "gradient_retention": gradient_retention,
        "fine": fine,
        "coarse": coarse,
        "native_fine": native_fine,
        "native_coarse": native_coarse,
    }


def focus_metrics(
    face_bgr: np.ndarray,
    *,
    landmarks: Iterable[Iterable[float]] | None = None,
) -> dict[str, Any]:
    """Return focus evidence for eye and face regions.

    ``landmarks`` are YuNet's five points in native face-crop coordinates.
    Directional evidence can withhold ``clear`` but cannot reject by itself.
    """
    if face_bgr is None or face_bgr.ndim != 3 or min(face_bgr.shape[:2]) < 8:
        raise ValueError("face_bgr must be a non-empty BGR image")
    height, width = face_bgr.shape[:2]
    points = _validated_landmarks(landmarks, width, height)
    regions, eye_distance = _regions(face_bgr, points)
    evidence = {name: _region_metrics(region) for name, region in regions.items()}
    eye = evidence["eye_band"]
    core = evidence["face_core"]

    eye_lap = eye["fine"]["laplacian_normalized"]
    eye_curvature = eye["fine"]["edge_curvature"]
    eye_grad = eye["fine"]["gradient_p90_normalized"]
    core_lap = core["fine"]["laplacian_normalized"]
    noisy = eye["fine_to_coarse_ratio"] > 12.0 or core["fine_to_coarse_ratio"] > 14.0
    directional = (
        eye["fine"]["orientation_coherence"] > .72
        and eye["gradient_retention"] > .68
        and eye_curvature < .19
    )

    reasons: list[str] = []
    if points is None:
        state = "uncertain"
        reasons.append("missing_reliable_landmarks")
    elif eye_distance is None or eye_distance < MIN_NATIVE_EYE_DISTANCE:
        state = "uncertain"
        reasons.append("insufficient_native_eye_pixels")
    elif eye["fine"]["contrast"] < .10 or core["fine"]["contrast"] < .08:
        state = "uncertain"
        reasons.append("insufficient_local_contrast")
    elif noisy:
        state = "uncertain"
        reasons.append("high_frequency_noise_or_artifacts")
    elif (
        eye_lap < .00145
        and core_lap < .00175
        and eye_curvature < .16
    ) or (
        eye_lap < .0032
        and core_lap < .0034
        and eye_curvature < .15
        and eye["coarse"]["laplacian_normalized"] < .0010
    ):
        state = "severe_blur"
        reasons.append("weak_eye_and_face_detail")
    elif directional:
        state = "uncertain"
        reasons.append("possible_directional_smear")
    elif (
        eye_lap >= .0045
        and core_lap >= .0060
        and eye_curvature >= .18
        and eye["native_fine"]["edge_curvature"] >= .215
        and eye_grad >= .25
        and eye["coarse"]["laplacian_normalized"] >= .0008
    ):
        state = "clear"
        reasons.append("strong_landmark_aligned_detail")
    else:
        state = "uncertain"
        reasons.append("borderline_focus_evidence")

    return {
        "state": state,
        "reasons": reasons,
        "landmarks_used": points is not None,
        "native_face_width": int(width),
        "native_eye_distance": eye_distance,
        # Compatibility keys retained for diagnostics written by v3.
        "resize_scale": eye["resize_scale"],
        "fine_to_coarse_ratio": eye["fine_to_coarse_ratio"],
        "fine": eye["fine"],
        "coarse": eye["coarse"],
        "directional_smear_candidate": directional,
        "motion_suspect": directional,
        "regions": evidence,
    }
