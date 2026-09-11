"""Conservative multi-scale detail evidence from native-resolution face crops."""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np


TARGET_FACE_WIDTH = 384
MIN_NATIVE_FACE_WIDTH = 384


def _normalized_eye_band(face_bgr: np.ndarray) -> tuple[np.ndarray, float]:
    if face_bgr is None or face_bgr.ndim != 3 or face_bgr.shape[0] < 8 or face_bgr.shape[1] < 8:
        raise ValueError("face_bgr must be a non-empty BGR image")

    native_width = int(face_bgr.shape[1])
    scale = TARGET_FACE_WIDTH / native_width
    target_height = max(8, round(face_bgr.shape[0] * scale))
    interpolation = cv2.INTER_AREA if scale <= 1.0 else cv2.INTER_CUBIC
    normalized = cv2.resize(
        face_bgr,
        (TARGET_FACE_WIDTH, target_height),
        interpolation=interpolation,
    )
    gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

    height, width = gray.shape
    # Tight detector crops place both eyes in roughly this band.  Production
    # code should replace this with a landmark-derived eye ROI when KPS exists.
    y0, y1 = round(height * 0.25), round(height * 0.53)
    x0, x1 = round(width * 0.05), round(width * 0.95)
    return gray[y0:y1, x0:x1], scale


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
    return {
        "contrast": contrast,
        "laplacian_normalized": float(np.var(laplacian) / (contrast * contrast)),
        "gradient_p90_normalized": gradient_p90 / contrast,
        "edge_curvature": edge_curvature,
    }


def focus_metrics(face_bgr: np.ndarray) -> dict[str, Any]:
    """Return scale/contrast-aware focus evidence and a conservative state.

    ``uncertain`` deliberately covers borderline,
    noisy, low-contrast, or under-resolved faces.
    """

    native_width = int(face_bgr.shape[1])
    eye_band, resize_scale = _normalized_eye_band(face_bgr)
    fine = _scale_metrics(eye_band, sigma=0.55)
    coarse = _scale_metrics(eye_band, sigma=1.40)

    lap = fine["laplacian_normalized"]
    grad = fine["gradient_p90_normalized"]
    curvature = fine["edge_curvature"]
    coarse_lap = coarse["laplacian_normalized"]
    fine_to_coarse = lap / max(coarse_lap, 1e-8)

    reasons: list[str] = []
    if native_width < MIN_NATIVE_FACE_WIDTH:
        state = "uncertain"
        reasons.append("insufficient_native_face_pixels")
    elif fine["contrast"] < 0.10:
        state = "uncertain"
        reasons.append("insufficient_local_contrast")
    elif fine_to_coarse > 12.0:
        # Fine derivatives can be inflated by sensor/JPEG noise.  High ratios
        # are withheld rather than incorrectly promoted to clear.
        state = "uncertain"
        reasons.append("high_frequency_noise_or_artifacts")
    elif lap < 0.00150:
        state = "severe_blur"
        reasons.append("very_low_normalized_detail")
    # Broad but soft transitions can have a high gradient (eyeliner/hair).
    # Require low fine/coarse detail and weak edge sharpness together;
    # gradient magnitude alone must not veto this blur evidence.
    elif lap < 0.00360 and curvature < 0.165 and coarse_lap < 0.00100:
        state = "severe_blur"
        reasons.append("weak_edges_at_both_scales")
    elif lap >= 0.00400 and curvature >= 0.180 and grad >= 0.250 and coarse_lap >= 0.00080:
        state = "clear"
        reasons.append("strong_consistent_eye_detail")
    else:
        state = "uncertain"
        reasons.append("borderline_focus_evidence")

    return {
        "state": state,
        "reasons": reasons,
        "native_face_width": native_width,
        "resize_scale": resize_scale,
        "fine_to_coarse_ratio": fine_to_coarse,
        "fine": fine,
        "coarse": coarse,
    }

