"""Face-anchored appearance features; this is not skeletal pose estimation."""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .screening import ScreeningConfig, choose_primary_face, detect_faces


@dataclass(frozen=True)
class SubjectFeatures:
    whole: str
    center: str
    body: str | None
    face: tuple[float, float, float, float] | None


def image_hash(image: Image.Image) -> str:
    pixels = np.asarray(image.convert("L").resize((9, 8)))
    bits = pixels[:, :-1] > pixels[:, 1:]
    return f"{int(''.join('1' if v else '0' for v in bits.flat), 2):016x}"


def features(path: Path) -> SubjectFeatures | None:
    try:
        stat = path.stat()
        return _cached_features(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, cv2.error):
        return None


@lru_cache(maxsize=256)
def _cached_features(path: str, modified: int, size: int) -> SubjectFeatures:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    w, h = image.size
    boxes = detect_faces(cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR),
                         config=ScreeningConfig(min_face_size=24, min_face_confidence=2.0, max_detection_side=960))
    face = None
    body = None
    if boxes:
        x, y, fw, fh = choose_primary_face(boxes, w, h)
        # Avoid arbitrarily switching between similarly prominent people.
        others = sorted((bw * bh for _, _, bw, bh in boxes), reverse=True)
        if len(others) == 1 or fw * fh >= others[1] * 1.6:
            face = (x / w, y / h, fw / w, fh / h)
            crop = image.crop((max(0, x - 1.5 * fw), max(0, y - .3 * fh),
                               min(w, x + 2.5 * fw), min(h, y + 5 * fh)))
            body = image_hash(crop)
    return SubjectFeatures(image_hash(image), image_hash(image.crop((w * .2, h * .1, w * .8, h * .95))), body, face)


def face_crop(image: Image.Image, face: tuple[float, float, float, float]) -> Image.Image:
    w, h = image.size
    x, y, fw, fh = face
    return image.crop((max(0, int((x - fw * .25) * w)), max(0, int((y - fh * .3) * h)),
                       min(w, int((x + fw * 1.25) * w)), min(h, int((y + fh * 1.3) * h))))
