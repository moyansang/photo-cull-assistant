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
from .subject import detail_features, detail_features_list
from .scan_timing import measure, timed
from .scan_diagnostics import operation
from .shared_decode import full_image

VERSION = "native-face-v4-kps"


@measure('decode')
def load_full_image(asset):
    path = asset.raw_path or asset.primary_path
    if path.suffix.lower() in RAW_EXTENSIONS:
        import rawpy
        try:
            with operation('raw_full_decode'), rawpy.imread(str(path)) as raw:
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
        entry = settings.photos.get(settings.key(asset), {})
        # Keep the exact legacy detector call for untouched photos. This is
        # both faster and preserves existing single-primary-face cache keys.
        if 'selected_faces' in entry:
            subjects = detail_features_list(asset, settings, require_landmarks=True)
        else:
            subject = detail_features(asset, settings, require_landmarks=True)
            subjects = [subject] if subject else []
    except (OSError, ValueError, cv2.error):
        return ScreeningResult(False, "preview_unreadable", False, analysis_version=VERSION)
    faces = [subject for subject in subjects if subject and getattr(subject, 'face', None)]
    if not faces:
        if any(subject and getattr(subject, 'head', None) for subject in subjects):
            return ScreeningResult(False, 'head_only_localized', False, analysis_version=VERSION,
                                   focus_evidence={'state':'uncertain','reasons':['head_without_visible_face']})
        return ScreeningResult(False, "no_reliable_face", False, analysis_version=VERSION)
    participant_inputs = [
        (tuple(subject.face), getattr(subject, "landmarks", None))
        for subject in faces
    ]
    source = asset.raw_path or asset.primary_path
    try:
        stat = source.stat()
        if len(participant_inputs) == 1:
            # Do not invalidate default single-subject results merely because
            # multi-participant support exists in this version.
            face, normalized_landmarks = participant_inputs[0]
            identity = dict(
                version=VERSION, path=str(source.resolve()), size=stat.st_size,
                mtime=stat.st_mtime_ns, face=face,
                landmarks=normalized_landmarks,
            )
        else:
            identity = dict(
                version=VERSION, path=str(source.resolve()), size=stat.st_size,
                mtime=stat.st_mtime_ns,
                participants=[
                    {"face": face, "landmarks": landmarks}
                    for face, landmarks in participant_inputs
                ],
            )
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cache = Path(cache_dir) / VERSION / (digest + '.json') if cache_dir else None
        if cache and cache.is_file():
            try:
                return ScreeningResult(**json.loads(cache.read_text('utf-8')))
            except (OSError, ValueError, TypeError):
                pass
        with full_image(asset) as image:
            width, height = image.size
            with Image.open(asset.preview_path) as preview:
                pw, ph = ImageOps.exif_transpose(preview).size
            if abs((width / height) / (pw / ph) - 1) > .05:
                return ScreeningResult(False, "source_preview_geometry_mismatch", True, analysis_version=VERSION)
            participant_results = []
            for participant_index, (face, normalized_landmarks) in enumerate(participant_inputs, 1):
                x, y, w, h = face
                box = (
                    max(0, round(x * width)), max(0, round(y * height)),
                    min(width, round((x + w) * width)), min(height, round((y + h) * height)),
                )
                crop = np.asarray(image.crop(box))
                if min(crop.shape[:2]) < 96:
                    participant_results.append({
                        "participant": participant_index,
                        "state": "uncertain",
                        "reasons": ["face_too_small_for_focus"],
                        "face_box": (box[0], box[1], box[2] - box[0], box[3] - box[1]),
                    })
                    continue
                gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
                lap, gradient, ratio = focus_metrics(gray)
                local_landmarks = None
                if normalized_landmarks and len(normalized_landmarks) == 5:
                    try:
                        local_landmarks = tuple(
                            (float(lx) * width - box[0], float(ly) * height - box[1])
                            for lx, ly in normalized_landmarks
                        )
                    except (TypeError, ValueError):
                        local_landmarks = None
                with timed('clarity'):
                    evidence = detail_metrics(
                        cv2.cvtColor(crop, cv2.COLOR_RGB2BGR),
                        landmarks=local_landmarks,
                    )
                participant_results.append({
                    **evidence,
                    "participant": participant_index,
                    "face_box": (box[0], box[1], box[2] - box[0], box[3] - box[1]),
                    "laplacian_variance": lap,
                    "tenengrad": gradient,
                    "detail_ratio": ratio,
                })
        states = [item["state"] for item in participant_results]
        aggregate_state = (
            "severe_blur" if "severe_blur" in states
            else "clear" if states and all(state == "clear" for state in states)
            else "uncertain"
        )
        rejected = aggregate_state == "severe_blur"
        reason = {
            "severe_blur": "obvious_subject_blur",
            "uncertain": "face_focus_uncertain",
            "clear": "subject_not_obviously_blurred",
        }[aggregate_state]
        representative = next(
            (item for item in participant_results if item["state"] == aggregate_state),
            participant_results[0],
        )
        if len(participant_results) == 1:
            evidence = {
                key: value for key, value in participant_results[0].items()
                if key not in {"participant", "face_box", "laplacian_variance", "tenengrad", "detail_ratio"}
            }
        else:
            evidence = {
                "state": aggregate_state,
                "participants": participant_results,
            }
        result = ScreeningResult(
            rejected=rejected, reason=reason, face_found=True,
            laplacian_variance=representative.get("laplacian_variance"),
            tenengrad=representative.get("tenengrad"),
            face_box=representative["face_box"],
            analysis_version=VERSION, source_size=(width, height),
            detail_ratio=representative.get("detail_ratio"), focus_evidence=evidence,
        )
        if cache:
            try:
                atomic_json(cache, asdict(result))
            except OSError:
                pass
        return result
    except (OSError, ValueError, cv2.error):
        return ScreeningResult(False, "source_unreadable_for_focus", True, analysis_version=VERSION)
