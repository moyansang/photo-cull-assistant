from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.screening import ScreeningConfig, assess_subject_blur, screen_assets


def make_asset(tmp_path: Path, stem: str, image: np.ndarray) -> PhotoAsset:
    preview = tmp_path / f"{stem}_preview.jpg"
    cv2.imwrite(str(preview), image)
    original = tmp_path / f"{stem}.jpg"
    cv2.imwrite(str(original), image)
    return PhotoAsset(
        stem=stem,
        display_path=original,
        primary_path=original,
        raw_path=None,
        jpg_path=original,
        captured_at=datetime(2026, 1, 1),
        ext=".jpg",
        preview_path=preview,
    )


def checkerboard(size: int = 256, block: int = 8) -> np.ndarray:
    y, x = np.indices((size, size))
    board = ((x // block + y // block) % 2 * 255).astype(np.uint8)
    return cv2.cvtColor(board, cv2.COLOR_GRAY2BGR)


def test_no_face_means_do_not_auto_reject(tmp_path):
    image = checkerboard()
    path = tmp_path / "preview.jpg"
    cv2.imwrite(str(path), image)

    result = assess_subject_blur(path, face_boxes=[])

    assert result.rejected is False
    assert result.reason == "no_reliable_face"


def test_obviously_blurred_subject_is_rejected_when_face_region_is_known(tmp_path):
    sharp = checkerboard()
    blurred = cv2.GaussianBlur(sharp, (41, 41), 0)
    path = tmp_path / "blurred.jpg"
    cv2.imwrite(str(path), blurred)

    result = assess_subject_blur(path, face_boxes=[(32, 32, 192, 192)])

    assert result.rejected is True
    assert result.reason == "obvious_subject_blur"
    assert result.laplacian_variance is not None
    assert result.tenengrad is not None


def test_sharp_subject_is_not_rejected_when_face_region_is_known(tmp_path):
    sharp = checkerboard()
    path = tmp_path / "sharp.jpg"
    cv2.imwrite(str(path), sharp)

    result = assess_subject_blur(path, face_boxes=[(32, 32, 192, 192)])

    assert result.rejected is False
    assert result.reason == "subject_not_obviously_blurred"


def test_screen_assets_marks_only_rejected_assets(tmp_path):
    sharp = checkerboard()
    blurred = cv2.GaussianBlur(sharp, (41, 41), 0)
    sharp_asset = make_asset(tmp_path, "SHARP", sharp)
    blurred_asset = make_asset(tmp_path, "BLUR", blurred)

    def fake_faces(asset: PhotoAsset):
        return [(32, 32, 192, 192)]

    results = screen_assets([sharp_asset, blurred_asset], face_provider=fake_faces)

    assert results["SHARP"].rejected is False
    assert results["BLUR"].rejected is True
    assert sharp_asset.auto_rejected is False
    assert blurred_asset.auto_rejected is True
    assert blurred_asset.screening_reason == "obvious_subject_blur"


def test_downsampled_low_contrast_obvious_blur_is_rejected(tmp_path):
    # Reproduces the real pipeline: a high-resolution photo is blurred first,
    # then reduced to a 640 px preview. Downsampling can inflate Laplacian
    # enough that an overly strict threshold misses a visibly bad subject.
    rng = np.random.default_rng(2)
    high = np.full((2400, 1600, 3), 128, np.uint8)
    noise = rng.normal(0, 28, (1200, 800, 1)).clip(-80, 80).astype(np.int16)
    high[600:1800, 400:1200] = np.clip(
        high[600:1800, 400:1200].astype(np.int16) + noise,
        0,
        255,
    ).astype(np.uint8)
    blurred = cv2.GaussianBlur(high, (19, 19), 0)
    preview = cv2.resize(blurred, (427, 640), interpolation=cv2.INTER_AREA)
    path = tmp_path / "downsampled_blur.jpg"
    cv2.imwrite(str(path), preview)

    result = assess_subject_blur(path, face_boxes=[(107, 160, 213, 320)])

    assert result.rejected is True
