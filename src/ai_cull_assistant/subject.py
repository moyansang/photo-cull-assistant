"""Face-anchored appearance features; this is not skeletal pose estimation."""
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .yunet import detect, head_box
from .crop_settings import CropSettings, crop_bounds


def asset_features(asset, score_threshold=.8, *, require_landmarks=False):
    legacy_face_without_points = bool(
        require_landmarks
        and asset.subject_features
        and getattr(asset.subject_features, "face", None)
        and not getattr(asset.subject_features, "landmarks", None)
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


@lru_cache(maxsize=256)
def _cached_features(path: str, modified: int, size: int, score_threshold: float) -> SubjectFeatures:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    w, h = image.size
    candidates = detect(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR), score_threshold)
    face = None
    body = None
    head = None
    landmarks = None
    if candidates:
        candidates.sort(key=lambda f: selection_score(f, w, h), reverse=True)
        chosen = candidates[0]
        x, y, fw, fh = chosen.box
        # Avoid arbitrarily switching between similarly prominent people.
        if len(candidates) == 1 or selection_score(chosen, w, h) - selection_score(candidates[1], w, h) >= .025:
            face = (x / w, y / h, fw / w, fh / h)
            head = head_box(chosen, w, h)
            landmarks = tuple((px / w, py / h) for px, py in chosen.landmarks)
            crop = image.crop((max(0, x - 1.5 * fw), max(0, y - .3 * fh),
                               min(w, x + 2.5 * fw), min(h, y + 5 * fh)))
            body = image_hash(crop)
    return SubjectFeatures(
        image_hash(image),
        image_hash(image.crop((w * .2, h * .1, w * .8, h * .95))),
        body,
        face,
        head,
        landmarks,
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
                return replace(
                    base,
                    face=tuple(box),
                    head=head_box(absolute, iw, ih),
                    landmarks=landmarks,
                )
            return replace(base, face=tuple(box), head=tuple(box), landmarks=None)
    return subject
