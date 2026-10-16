"""Pixel descriptors for complete resolved ROI selections, never identity features.

Call refresh_asset_roi_features for every asset in a regroup transaction. A
legacy face-derived body hash must not be compared to this explicit-body schema.
A failed extraction clears the affected asset instead of retaining stale data.
"""
from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
from threading import Lock

import cv2
import numpy as np
from PIL import Image, ImageOps

from .roi_contract import SCHEMA_VERSION, resolve_rois, roi_list_digest, _normalize
from .subject import SubjectFeatures, DETECTION_VERSION, image_hash

FEATURE_SCHEMA = 'subject-explicit-roi-dhash64-v1'
_cache = OrderedDict()
_cache_lock = Lock()


def _snapshot(asset):
    if not asset.preview_path:
        raise OSError('ROI extraction requires a prepared preview')
    path = Path(asset.preview_path)
    # One encoded snapshot supplies both digest and decoded pixels. Re-read to
    # reject in-place/atomic replacement while loading, including preserved mtime.
    encoded = path.read_bytes()
    digest = sha256(encoded).hexdigest()
    with Image.open(BytesIO(encoded)) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
    if path.read_bytes() != encoded:
        raise OSError('Preview changed during ROI extraction')
    return image, digest


def resolve_asset_rois(asset, settings, *, auto_faces=None, auto_bodies=None, _image=None):
    """Resolve all detector candidates plus persisted overrides, without saving.

    Callers with already collected full candidate lists can supply both lists to
    avoid inference. ``manual_face`` is the legacy single-face override alias.
    Display selection never gates the independent body candidate collection.
    """
    override = deepcopy(settings.photos.get(settings.key(asset), {}))
    if 'selected_faces' not in override and override.get('manual_face') is not None:
        override['selected_faces'] = [override['manual_face']]
    # An authoritative selection does not require re-running its detector.
    disabled = override.get('hidden') or override.get('subjects_disabled')
    if auto_faces is None and (disabled or 'selected_faces' in override):
        auto_faces = []
    if auto_bodies is None and (disabled or 'selected_bodies' in override or
            ('selected_faces' in override and not override['selected_faces'] and
             override.get('roi_selection_schema', 1) == 1)):
        auto_bodies = []
    if auto_faces is None or auto_bodies is None:
        from .subject import detect
        from .body_focus import detect_body_candidates
        image = _image if _image is not None else _snapshot(asset)[0]
        try:
            width, height = image.size
            if auto_faces is None:
                detections = detect(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR),
                                    settings.detection_confidence)
                auto_faces = []
                for item in detections:
                    x, y, w, h = item.box
                    # Detector rectangles may extend beyond the image edge.
                    left, top = max(0, x), max(0, y)
                    right, bottom = min(width, x+w), min(height, y+h)
                    if right > left and bottom > top:
                        auto_faces.append({'box': [left/width, top/height,
                                                   (right-left)/width, (bottom-top)/height]})
            if auto_bodies is None:
                auto_bodies = detect_body_candidates(image)
        finally:
            if _image is None:
                image.close()
    return resolve_rois(auto_faces, auto_bodies, override)


def _validate_resolved(resolved):
    result = deepcopy(resolved)
    if result.get('schema') != SCHEMA_VERSION:
        raise ValueError('Unsupported ROI contract schema')
    for kind in ('face', 'body'):
        key = 'analysis_faces' if kind == 'face' else 'analysis_bodies'
        regions = result[key]
        if not isinstance(regions, list):
            raise ValueError('Resolved ROI collections must be lists')
        for region in regions:
            if not isinstance(region, dict) or region.get('kind') != kind or region.get('origin') not in ('manual', 'automatic'):
                raise ValueError('Invalid resolved ROI record')
            if _normalize([region], kind, region['origin'])[0] != region:
                raise ValueError('Noncanonical resolved ROI record')
        index = result['primary_'+kind+'_index']
        if index is not None and (isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(regions)):
            raise ValueError('Invalid resolved primary index')
        if result.get(kind+'_rois_digest') != roi_list_digest(regions):
            raise ValueError('ROI list digest mismatch')
    signature = dict(schema=result['schema'], faces=result['analysis_faces'],
                     bodies=result['analysis_bodies'], disabled=result['disabled'],
                     empty_face_policy=result['empty_face_policy'],
                     primary_face_index=result['primary_face_index'],
                     primary_body_index=result['primary_body_index'])
    digest = sha256(json.dumps(signature, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if digest != result.get('revision_digest'):
        raise ValueError('ROI revision digest mismatch')
    return result


def _crop(image, box):
    x, y, width, height = box
    iw, ih = image.size
    # Cover even tiny valid ROIs with at least one real pixel, without padding.
    return image.crop((max(0, math.floor(x*iw)), max(0, math.floor(y*ih)),
                       min(iw, math.ceil((x+width)*iw)), min(ih, math.ceil((y+height)*ih))))


def features_from_resolved(image, resolved, *, image_digest, score_threshold=.8):
    """Extract every resolved ROI from an already EXIF-oriented pixel image."""
    roi = _validate_resolved(resolved)
    faces, bodies = roi['analysis_faces'], roi['analysis_bodies']
    face_hashes = tuple(image_hash(_crop(image, item['box'])) for item in faces)
    body_hashes = tuple(image_hash(_crop(image, item['box'])) for item in bodies)
    fi, bi = roi['primary_face_index'], roi['primary_body_index']
    width, height = image.size
    return SubjectFeatures(
        whole=image_hash(image),
        center=image_hash(image.crop((width*.2, height*.1, width*.8, height*.95))),
        body=body_hashes[bi] if bi is not None else None,
        face=tuple(faces[fi]['box']) if fi is not None else None,
        detection_version=DETECTION_VERSION, candidate_count=len(faces),
        candidate_confidence=score_threshold, feature_schema=FEATURE_SCHEMA,
        roi_revision_digest=roi['revision_digest'], image_digest=image_digest,
        analysis_faces=tuple(deepcopy(faces)), analysis_bodies=tuple(deepcopy(bodies)),
        face_hashes=face_hashes, body_hashes=body_hashes,
        primary_face_index=fi, primary_body_index=bi,
        body_box=tuple(bodies[bi]['box']) if bi is not None else None,
        roi_primary_selection_required=(len(faces) > 1 and fi is None) or (len(bodies) > 1 and bi is None),
    )


def refresh_asset_roi_features(asset, settings, resolved=None):
    """Recompute/version-check and persist one asset's complete ROI features.

    Raises on invalid contracts or missing/changed previews; never silently uses
    an old feature. Caller owns batch rollback and one regroup after all succeed.
    Pure resolution may be passed explicitly from the save transaction.
    """
    try:
        # Resolution and hashes use the same oriented immutable pixel snapshot.
        image, digest = _snapshot(asset)
        try:
            roi = _validate_resolved(resolved if resolved is not None else
                                     resolve_asset_rois(asset, settings, _image=image))
            key = (FEATURE_SCHEMA, roi['revision_digest'], digest, settings.detection_confidence)
            with _cache_lock:
                found = _cache.get(key)
            if found is None:
                found = features_from_resolved(image, roi, image_digest=digest,
                                               score_threshold=settings.detection_confidence)
            with _cache_lock:
                _cache[key] = deepcopy(found)
                _cache.move_to_end(key)
                while len(_cache) > 128:
                    _cache.popitem(last=False)
        finally:
            image.close()
        asset.subject_features = deepcopy(found)
        asset.subject_checked = True
        asset.subject_confidence = settings.detection_confidence
        return asset.subject_features
    except (OSError, ValueError, TypeError, KeyError, cv2.error):
        asset.subject_features = None
        asset.subject_checked = False
        raise
