"""Face-anchored appearance features; this is not skeletal pose estimation."""
from dataclasses import dataclass, replace
from functools import lru_cache
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .yunet import detect, head_box
from .crop_settings import CropSettings, crop_bounds


_manual_details = OrderedDict()
DETECTION_VERSION = 'subject-v2-profile-head'


def _remember_manual(key, head, landmarks):
    _manual_details[key] = (head, landmarks)
    _manual_details.move_to_end(key)
    while len(_manual_details) > 128:
        _manual_details.popitem(last=False)


def asset_features(asset, score_threshold=.8, *, require_landmarks=False):
    legacy_face_without_points = bool(
        require_landmarks
        and (getattr(asset.subject_features, 'detection_version', None) != DETECTION_VERSION
             or (getattr(asset.subject_features, "face", None)
                 and not getattr(asset.subject_features, "landmarks", None)))
    )
    if not asset.subject_checked or asset.subject_confidence != score_threshold or legacy_face_without_points:
        if asset.preview_path and not Path(asset.preview_path).is_file():
            from .preview import ensure_preview
            ensure_preview(asset)
        asset.subject_features = features(Path(asset.preview_path), score_threshold) if asset.preview_path else None
        asset.subject_checked = True
        asset.subject_confidence = score_threshold
    return asset.subject_features


@dataclass(frozen=True)
class SubjectFeatures:
    whole: str
    center: str
    body: str | None
    face: tuple[float, float, float, float] | None
    head: tuple[float, float, float, float] | None = None
    # YuNet's five points in normalized, EXIF-oriented image coordinates.
    # Kept optional so sessions written before landmark persistence still load.
    landmarks: tuple[tuple[float, float], ...] | None = None
    # A head-only fallback does not imply a visible face or eye evidence.
    head_source: str | None = None
    detection_version: str | None = None


def image_hash(image: Image.Image) -> str:
    pixels = np.asarray(image.convert("L").resize((9, 8)))
    bits = pixels[:, :-1] > pixels[:, 1:]
    return f"{int(''.join('1' if v else '0' for v in bits.flat), 2):016x}"


def features(path: Path, score_threshold: float = .9) -> SubjectFeatures | None:
    try:
        stat = path.stat()
        return _cached_features(str(path.resolve()), stat.st_mtime_ns, stat.st_size, score_threshold)
    except (OSError, ValueError, cv2.error):
        return None


def _head_candidates(image):
    from .head_detection import detect_heads
    return detect_heads(image)


def _inside_head(candidate, head):
    x, y, w, h = candidate.box
    hx, hy, hw, hh = head.box
    return hx - .1*hw <= x+w/2 <= hx+1.1*hw and hy-.1*hh <= y+h/2 <= hy+1.1*hh


def _refine_head_face(image, head, score_threshold):
    """Try real YuNet landmarks inside a located head; never fabricate eyes."""
    from .head_detection import refine_face_in_head
    return refine_face_in_head(image, head, score_threshold)


def _choose_subject(image, candidates, score_threshold):
    height, width = image.shape[:2]
    ranked = sorted(candidates, key=lambda f: selection_score(f,width,height), reverse=True)
    chosen = ranked[0] if ranked and (len(ranked)==1 or selection_score(ranked[0],width,height)-selection_score(ranked[1],width,height)>=.025) else None
    if chosen and chosen.box[2]*chosen.box[3] >= width*height*.002:
        return chosen, None
    heads = sorted(_head_candidates(image), key=lambda item: item.score, reverse=True)
    if not heads:
        return chosen, None
    # Similar competing people need a manual choice, just like competing faces.
    if len(heads)>1 and heads[0].score-heads[1].score < .04:
        return chosen, None
    located = heads[0]
    matching = [face for face in ranked if _inside_head(face,located)]
    if matching:
        return max(matching,key=lambda item:item.score), None
    if chosen and not (chosen.score < .9 and located.score >= .78
                       and located.box[2]*located.box[3] >= 4*chosen.box[2]*chosen.box[3]):
        return chosen, None
    refined = _refine_head_face(image, located, score_threshold)
    return (refined, None) if refined else (None, located)


@lru_cache(maxsize=256)
def _cached_features(path: str, modified: int, size: int, score_threshold: float) -> SubjectFeatures:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    w, h = image.size
    pixels = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    candidates = detect(pixels, score_threshold)
    chosen, located_head = _choose_subject(pixels, candidates, score_threshold)
    face = None
    body = None
    head = None
    landmarks = None
    if chosen:
        x, y, fw, fh = chosen.box
        face = (x / w, y / h, fw / w, fh / h)
        head = head_box(chosen, w, h)
        landmarks = tuple((px / w, py / h) for px, py in chosen.landmarks)
        crop = image.crop((max(0, x - 1.5 * fw), max(0, y - .3 * fh),
                           min(w, x + 2.5 * fw), min(h, y + 5 * fh)))
        body = image_hash(crop)
    elif located_head:
        head = located_head.normalized_box(w, h)
    return SubjectFeatures(
        image_hash(image),
        image_hash(image.crop((w * .2, h * .1, w * .8, h * .95))),
        body,
        face,
        head,
        landmarks,
        'yunet' if face else (located_head.source if located_head else None),
        DETECTION_VERSION,
    )


def face_crop(image: Image.Image, face: tuple[float, float, float, float], head: tuple[float, float, float, float] | None = None, settings: CropSettings = CropSettings()) -> Image.Image:
    w, h = image.size
    if head:
        return image.crop(crop_bounds(image.size, head, settings))
    x, y, fw, fh = face
    return image.crop((max(0, int((x - fw * .25) * w)), max(0, int((y - fh * .3) * h)),
                       min(w, int((x + fw * 1.25) * w)), min(h, int((y + fh * 1.3) * h))))


def selection_score(face, width, height):
    """Confidence dominates; a mild center preference breaks close ties."""
    x, y, w, h = face.box
    distance = ((x + w / 2) / width - .5) ** 2 + ((y + h / 2) / height - .4) ** 2
    return face.score - .12 * distance


def detail_features(asset, settings, *, require_landmarks=False):
    subject = asset_features(
        asset,
        settings.detection_confidence,
        require_landmarks=require_landmarks,
    )
    override = settings.photos.get(settings.key(asset), {})
    if override.get('hidden'):
        return replace(subject, face=None, head=None) if subject else None
    box = override.get('manual_face')
    if box and len(box) == 4 and all(np.isfinite(v) for v in box):
        x, y, w, h = box
        if 0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x+w <= 1.000001 and y+h <= 1.000001:
            from .preview import ensure_preview
            ensure_preview(asset)
            preview = Path(asset.preview_path)
            stat = preview.stat()
            cache_key = (str(preview.resolve()), stat.st_mtime_ns, stat.st_size,
                         tuple(box), settings.detection_confidence)
            base = subject or SubjectFeatures('', '', None, None)
            cached = _manual_details.get(cache_key)
            if cached is not None:
                return replace(base, face=tuple(box), head=cached[0], landmarks=cached[1], head_source='manual')
            with Image.open(asset.preview_path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
            iw, ih = image.size
            base = subject or SubjectFeatures('', '', None, None)
            # A manual rectangle identifies the intended face, but it is not
            # landmark evidence. Run YuNet inside it and persist only points
            # that were actually detected.
            face_left, face_top = round(x * iw), round(y * ih)
            face_right, face_bottom = round((x + w) * iw), round((y + h) * ih)
            margin_x, margin_y = round((face_right-face_left) * .3), round((face_bottom-face_top) * .3)
            px0, py0 = max(0, face_left-margin_x), max(0, face_top-margin_y)
            px1, py1 = min(iw, face_right+margin_x), min(ih, face_bottom+margin_y)
            region = np.asarray(image)[py0:py1, px0:px1]
            detections = detect(
                cv2.cvtColor(region, cv2.COLOR_RGB2BGR),
                settings.detection_confidence,
            ) if region.size else []
            contained = []
            for candidate in detections:
                dx, dy, dw, dh = candidate.box
                center_x, center_y = px0 + dx + dw/2, py0 + dy + dh/2
                if face_left <= center_x <= face_right and face_top <= center_y <= face_bottom:
                    contained.append(candidate)
            if contained:
                target_x = (face_left + face_right) / 2
                target_y = (face_top + face_bottom) / 2
                chosen = min(
                    contained,
                    key=lambda item: (
                        ((px0 + item.box[0] + item.box[2]/2 - target_x) / max(1, face_right-face_left)) ** 2
                        + ((py0 + item.box[1] + item.box[3]/2 - target_y) / max(1, face_bottom-face_top)) ** 2
                        - .05 * item.score
                    ),
                )
                from .yunet import FaceDetection
                absolute = FaceDetection(
                    (chosen.box[0] + px0, chosen.box[1] + py0, chosen.box[2], chosen.box[3]),
                    tuple((lx + px0, ly + py0) for lx, ly in chosen.landmarks),
                    chosen.score,
                )
                landmarks = tuple((lx / iw, ly / ih) for lx, ly in absolute.landmarks)
                head = head_box(absolute, iw, ih)
                _remember_manual(cache_key, head, landmarks)
                return replace(
                    base,
                    face=tuple(box),
                    head=head,
                    landmarks=landmarks,
                    head_source='manual',
                )
            _remember_manual(cache_key, tuple(box), None)
            return replace(base, face=tuple(box), head=tuple(box), landmarks=None, head_source='manual')
    return subject
