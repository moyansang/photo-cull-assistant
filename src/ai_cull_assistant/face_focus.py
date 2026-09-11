"""Native-resolution face focus analysis, with source/face/version keyed caching."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .ai_project import atomic_json
from .crop_settings import CropSettings
from .focus_metrics import focus_metrics as detail_metrics
from .models import RAW_EXTENSIONS
from .screening import ScreeningResult
from .subject import detail_features

VERSION = "native-face-v3"


def load_full_image(asset):
    path = asset.raw_path or asset.primary_path
    if path.suffix.lower() in RAW_EXTENSIONS:
        import rawpy
        try:
            with rawpy.imread(str(path)) as raw:
                rgb = raw.postprocess(
                    use_camera_wb=True, half_size=False,
                    no_auto_bright=True, output_bps=8,
                )
        except rawpy.LibRawError as exc:
            raise ValueError("RAW decode failed") from exc
        return Image.fromarray(rgb)
    with Image.open(path) as source:
        return ImageOps.exif_transpose(source).convert("RGB")


def focus_metrics(gray):
    """Native pixel diagnostics retained alongside the normalized decision evidence."""
    gray = gray.astype(np.float32)
    lap = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    gradient = float(np.sqrt(np.mean(gx * gx + gy * gy)))
    return lap, gradient, lap / max(gradient * gradient, 1e-6)


def assess_asset_focus(asset, *, crop_settings=None, cache_dir=None):
    settings = crop_settings or CropSettings()
    try:
        subject = detail_features(asset, settings)
    except (OSError, ValueError, cv2.error):
        return ScreeningResult(False, "preview_unreadable", False, analysis_version=VERSION)
    if not subject or not subject.face:
        return ScreeningResult(False, "no_reliable_face", False, analysis_version=VERSION)
    face = tuple(subject.face)
    source = asset.raw_path or asset.primary_path
    try:
        stat = source.stat()
        identity = dict(
            version=VERSION, path=str(source.resolve()), size=stat.st_size,
            mtime=stat.st_mtime_ns, face=face,
        )
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cache = Path(cache_dir) / VERSION / (digest + '.json') if cache_dir else None
        if cache and cache.is_file():
            try:
                return ScreeningResult(**json.loads(cache.read_text('utf-8')))
            except (OSError, ValueError, TypeError):
                pass
        with load_full_image(asset) as image:
            width, height = image.size
            with Image.open(asset.preview_path) as preview:
                pw, ph = ImageOps.exif_transpose(preview).size
            if abs((width / height) / (pw / ph) - 1) > .05:
                return ScreeningResult(False, "source_preview_geometry_mismatch", True, analysis_version=VERSION)
            x, y, w, h = face
            box = (
                max(0, round(x * width)), max(0, round(y * height)),
                min(width, round((x + w) * width)), min(height, round((y + h) * height)),
            )
            crop = np.asarray(image.crop(box))
        if min(crop.shape[:2]) < 96:
            return ScreeningResult(
                False, "face_too_small_for_focus", True,
                analysis_version=VERSION, source_size=(width, height),
            )
        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        lap, gradient, ratio = focus_metrics(gray)
        evidence = detail_metrics(cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
        rejected = evidence["state"] == "severe_blur"
        reason = {
            "severe_blur": "obvious_subject_blur",
            "uncertain": "face_focus_uncertain",
            "clear": "subject_not_obviously_blurred",
        }[evidence["state"]]
        result = ScreeningResult(
            rejected=rejected, reason=reason, face_found=True,
            laplacian_variance=lap, tenengrad=gradient,
            face_box=(box[0], box[1], box[2] - box[0], box[3] - box[1]),
            analysis_version=VERSION, source_size=(width, height),
            detail_ratio=ratio, focus_evidence=evidence,
        )
        if cache:
            try:
                atomic_json(cache, asdict(result))
            except OSError:
                pass
        return result
    except (OSError, ValueError, cv2.error):
        return ScreeningResult(False, "source_unreadable_for_focus", True, analysis_version=VERSION)
