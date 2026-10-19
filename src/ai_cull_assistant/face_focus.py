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

VERSION = "native-face-v6-effective-region"
# Derived local cache format only; do not invalidate persisted paid AI identities.
LOCAL_CACHE_VERSION = "source-stat-v4-candidate-provenance"


@measure('decode')
def load_full_image(asset):
    """Use the shared JPG-first/native RAW fallback decoder."""
    from .image_decode import load_analysis_image
    return load_analysis_image(asset)


def focus_metrics(gray):
    """Native pixel diagnostics retained alongside the normalized decision evidence."""
    gray = gray.astype(np.float32)
    lap = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    gradient = float(np.sqrt(np.mean(gx * gx + gy * gy)))
    return lap, gradient, lap / max(gradient * gradient, 1e-6)


def _measure_participants(image, participant_inputs):
    width, height = image.size
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
            "normalized_box": tuple(face),
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
            "normalized_box": tuple(face),
            "face_box": (box[0], box[1], box[2] - box[0], box[3] - box[1]),
            "laplacian_variance": lap,
            "tenengrad": gradient,
            "detail_ratio": ratio,
        })
    return participant_results


def assess_asset_focus(asset, *, crop_settings=None, cache_dir=None):
    settings = crop_settings or CropSettings()
    try:
        entry = settings.photos.get(settings.key(asset), {})
        from .participant_review import needs_person_review
        asset.person_review_pending = needs_person_review(asset, entry)
        if asset.person_review_pending:
            return ScreeningResult(False, 'person_selection_pending', False, analysis_version=VERSION,
                                   focus_evidence={'state': 'uncertain', 'reasons': ['person_selection_pending']})
        # Keep the lightweight legacy detector call for untouched photos;
        # local cache identity is versioned independently below.
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
    # Record the detector view actually used for this measurement, after refresh.
    # candidate_confidence is the configured threshold, not a face confidence.
    automatic_subject = None
    if (len(faces) == 1 and not any(key in entry for key in
            ('selected_faces', 'manual_face', 'selected_bodies'))):
        subject = faces[0]
        automatic_subject = dict(
            version='single-detected-face-v1',
            candidate_count=getattr(subject, 'candidate_count', None),
            detection_threshold=settings.detection_confidence,
            box=list(subject.face),
            candidate_provenance=getattr(subject, 'candidate_provenance', None),
            landmarks_available=bool(getattr(subject, 'landmarks', None)
                                     and len(subject.landmarks) == 5),
        )
    source = asset.jpg_path or asset.raw_path or asset.primary_path
    try:
        from .image_decode import source_signature, asset_source_signature
        primary_signature = source_signature(source)
        source_signatures = asset_source_signature(asset)
        if len(participant_inputs) == 1:
            # Subject geometry remains part of the derived local key.
            face, normalized_landmarks = participant_inputs[0]
            identity = dict(
                version=VERSION, cache_identity=LOCAL_CACHE_VERSION,
                source=primary_signature, face=face,
                landmarks=normalized_landmarks,
            )
        else:
            identity = dict(
                version=VERSION, cache_identity=LOCAL_CACHE_VERSION,
                source=primary_signature,
                participants=[
                    {"face": face, "landmarks": landmarks}
                    for face, landmarks in participant_inputs
                ],
            )
        # The paired JPG is normally measured, so changes to either member must
        # invalidate evidence even when the RAW filename remains the identity.
        identity['sources'] = source_signatures
        identity['automatic_subject'] = automatic_subject
        if 'selected_faces' in entry or entry.get('manual_face') is not None:
            identity['manual_scope_sources'] = [getattr(subject, 'head_source', None) for subject in faces]
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        cache = Path(cache_dir) / VERSION / (digest + '.json') if cache_dir else None
        if cache and cache.is_file():
            try:
                cached_result = ScreeningResult(**json.loads(cache.read_text('utf-8')))
                if asset_source_signature(asset) == source_signatures:
                    return cached_result
            except (OSError, ValueError, TypeError):
                pass
        decode_options = {}
        if asset.raw_path:
            regions = []
            for face_box, points in participant_inputs:
                if points and len(points) == 5:
                    ex0, ey0 = points[0]
                    ex1, ey1 = points[1]
                    distance = max(abs(ex1-ex0), abs(ey1-ey0))
                    # An eye-distance-sized square supports the native-pixel
                    # gate; this is a decoder requirement, not fabricated KPS.
                    regions.append((min(ex0,ex1), min(ey0,ey1), distance, distance))
                else:
                    regions.append(face_box)
            decode_options = dict(evidence_regions=regions, min_region_pixels=56)
        with full_image(asset, **decode_options) as image:
            decode_evidence = dict(image.info.get('decode_evidence') or {})
            width, height = image.size
            with Image.open(asset.preview_path) as preview:
                pw, ph = ImageOps.exif_transpose(preview).size
            if abs((width / height) / (pw / ph) - 1) > .05:
                return ScreeningResult(False, "source_preview_geometry_mismatch", True, analysis_version=VERSION)
            participant_results = _measure_participants(image, participant_inputs)
        insufficient_reasons = {'face_too_small_for_focus', 'insufficient_native_eye_pixels',
                                'insufficient_local_contrast', 'high_frequency_noise_or_artifacts'}
        needs_more_detail = any(insufficient_reasons.intersection(item.get('reasons', []))
                               for item in participant_results)
        # Recheck actual missing detail, never borderline quality or close group
        # scores. A decoder failure remains review-only and is recorded below.
        if (asset.raw_path and needs_more_detail and
                decode_evidence.get('source_kind') != 'raw_full_decode' and
                not decode_evidence.get('raw_upgrade_attempted')):
            with full_image(asset, **decode_options, insufficient_evidence=True) as image:
                width, height = image.size
                decode_evidence = dict(image.info.get('decode_evidence') or {})
                if abs((width / height) / (pw / ph) - 1) > .05:
                    return ScreeningResult(False, 'source_preview_geometry_mismatch', True, analysis_version=VERSION)
                participant_results = _measure_participants(image, participant_inputs)
        for subject, item in zip(faces, participant_results):
            if getattr(subject, 'head_source', None) == 'manual_ambiguous':
                item['state'] = 'uncertain'
                item['reasons'] = list(item.get('reasons', [])) + ['manual_face_scope_ambiguous']
        if decode_evidence.get('evidence_sufficient') is False:
            for item in participant_results:
                item['state'] = 'uncertain'
                item['reasons'] = list(item.get('reasons', [])) + ['source_detail_evidence_insufficient']
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
        if automatic_subject is not None:
            evidence['automatic_subject'] = automatic_subject
        if decode_evidence:
            evidence['decode'] = decode_evidence
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
                # Do not retain evidence under an input version changed while
                # decoding/measuring. The next explicit evaluation gets a new key.
                if asset_source_signature(asset) == source_signatures:
                    atomic_json(cache, asdict(result))
            except OSError:
                pass
        return result
    except (OSError, ValueError, cv2.error):
        return ScreeningResult(False, "source_unreadable_for_focus", True, analysis_version=VERSION)
