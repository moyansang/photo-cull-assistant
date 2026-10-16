"""Conservative, preview-only portrait evidence for pose-tolerant grouping.

This module does not detect people, identify a person, mutate ROI records, or
read an original photo.  It only derives transient descriptors from an already
prepared preview when the explicit ROI contract is complete and unambiguous.
Missing or inconsistent evidence returns ``None``/``False``.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps


FEATURE_SCHEMA = "subject-explicit-roi-dhash64-v1"
_FACE_SIZE = 64
_PHOTO_SIZE = 128
_ORB_EDGE = 640
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2


@dataclass(frozen=True, slots=True)
class _PoseConfig:
    face_hash: int
    face_ssim: float
    context_hash: int
    face_displacement: float
    face_area_ratio: float
    face_color: float
    context_color: float
    min_inliers: int
    min_inlier_ratio: float
    min_scale: float
    max_scale: float
    max_rotation: float
    max_translation: float
    min_span: float
    min_area: float
    max_background_mae: float
    min_background_coverage: float


_STANDARD = _PoseConfig(
    face_hash=8, face_ssim=.65, context_hash=10,
    face_displacement=.06, face_area_ratio=1.25,
    face_color=.55, context_color=.55,
    min_inliers=12, min_inlier_ratio=.55,
    min_scale=.94, max_scale=1.06, max_rotation=3.0,
    max_translation=.06, min_span=.12, min_area=.03,
    max_background_mae=.04, min_background_coverage=.12,
)
_CONFIGS = {
    "strict": _PoseConfig(
        face_hash=6, face_ssim=.72, context_hash=8,
        face_displacement=.05, face_area_ratio=1.20,
        face_color=.62, context_color=.62,
        min_inliers=18, min_inlier_ratio=.62,
        min_scale=.96, max_scale=1.04, max_rotation=2.0,
        max_translation=.04, min_span=.15, min_area=.04,
        max_background_mae=.035, min_background_coverage=.15,
    ),
    "standard": _STANDARD,
    # A loose whole-image preset must not weaken portrait-identity safeguards.
    "loose": _STANDARD,
}


@dataclass(frozen=True, slots=True)
class PortraitContext:
    """Immutable in-memory evidence derived from one prepared preview."""

    context_hash: int
    face_hash: int
    face_box: tuple[float, float, float, float]
    face_gray: np.ndarray
    face_color_hist: np.ndarray
    context_color_hist: np.ndarray
    orb_points: np.ndarray
    orb_descriptors: np.ndarray
    orb_size: tuple[int, int]
    photo_rgb: np.ndarray
    background_mask: np.ndarray
    preview_digest: str
    explicit_body_bound: bool
    body_box: tuple[float, float, float, float]
    body_hash: int
    whole_hash: int
    aspect: float
    context_gray: np.ndarray


def _notes(asset: Any) -> tuple[str, ...]:
    value = getattr(asset, "notes", ())
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(item) for item in value)
    except TypeError:
        return ()


def _valid_box(value: Any) -> tuple[float, float, float, float] | None:
    try:
        x, y, width, height = (float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(part) for part in (x, y, width, height)):
        return None
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        return None
    if x + width > 1.000001 or y + height > 1.000001:
        return None
    return x, y, width, height


def _crop(image: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    x, y, width, height = box
    iw, ih = image.size
    left = max(0, math.floor(x * iw))
    top = max(0, math.floor(y * ih))
    right = min(iw, math.ceil((x + width) * iw))
    bottom = min(ih, math.ceil((y + height) * ih))
    if right <= left or bottom <= top:
        raise ValueError("empty ROI")
    return image.crop((left, top, right, bottom))


def _face_context_box(face: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, width, height = face
    left, top = max(0.0, x - 1.5 * width), max(0.0, y - .3 * height)
    right, bottom = min(1.0, x + 2.5 * width), min(1.0, y + 5.0 * height)
    return left, top, right - left, bottom - top


def _dhash(image: Image.Image) -> int:
    pixels = np.asarray(image.convert("L").resize((9, 8)))
    value = 0
    for bit in (pixels[:, :-1] > pixels[:, 1:]).flat:
        value = (value << 1) | int(bit)
    return value


def _whole_dhash(image: Image.Image) -> int:
    """Exactly the legacy whole-frame LANCZOS hash, distinct from ROI hashes."""
    pixels=np.asarray(image.convert('L').resize((9,8),Image.Resampling.LANCZOS))
    value=0
    for bit in (pixels[:,:-1]>pixels[:,1:]).flat:
        value=(value<<1)|int(bit)
    return value


def _color_hist(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 6, 4], [0, 180, 0, 256, 0, 256])
    hist = cv2.normalize(hist, None, alpha=1.0, norm_type=cv2.NORM_L1).reshape(-1).astype(np.float32)
    hist.flags.writeable = False
    return hist


def _face_gray(image: Image.Image) -> np.ndarray:
    result = np.asarray(image.convert("L").resize((_FACE_SIZE, _FACE_SIZE), Image.Resampling.LANCZOS)).copy()
    result.flags.writeable = False
    return result


def _resize_for_orb(image: Image.Image) -> np.ndarray:
    width, height = image.size
    scale = min(1.0, _ORB_EDGE / max(width, height))
    size = max(1, round(width * scale)), max(1, round(height * scale))
    return cv2.resize(np.asarray(image), size, interpolation=cv2.INTER_AREA)


def _paint_box(mask: np.ndarray, box: tuple[float, float, float, float]) -> None:
    x, y, width, height = box
    ih, iw = mask.shape
    left, top = max(0, math.floor(x * iw)), max(0, math.floor(y * ih))
    right, bottom = min(iw, math.ceil((x + width) * iw)), min(ih, math.ceil((y + height) * ih))
    if right > left and bottom > top:
        mask[top:bottom, left:right] = 0


def _body_bound(face: tuple[float, float, float, float],
                body: tuple[float, float, float, float]) -> bool:
    fx, fy, fw, fh = face
    bx, by, bw, bh = body
    center_x, center_y = fx + fw / 2, fy + fh / 2
    return (bx <= center_x <= bx + bw and by <= center_y <= by + min(bh, .55 * bh + fh)
            and fw <= bw * 1.15 and fh <= bh * .75)


def _record_box(record: Any) -> tuple[float, float, float, float] | None:
    return _valid_box(record.get("box")) if isinstance(record, dict) else None


def _stable_preview(asset: Any, expected_digest: str) -> tuple[Image.Image, str] | None:
    path_value = getattr(asset, "preview_path", None)
    if not path_value:
        return None
    path = Path(path_value)
    try:
        encoded = path.read_bytes()
        digest = sha256(encoded).hexdigest()
        if digest != expected_digest or path.read_bytes() != encoded:
            return None
        with Image.open(BytesIO(encoded)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.load()
        return image, digest
    except (OSError, ValueError, Image.UnidentifiedImageError, Image.DecompressionBombError):
        return None


def build_portrait_context(asset: Any) -> PortraitContext | None:
    """Build transient grouping evidence without changing the asset or its ROI data."""
    if getattr(asset, "analysis_mode", "portrait") != "portrait":
        return None
    if any(note.startswith("decode_error:") for note in _notes(asset)):
        return None
    if not getattr(asset, "subject_checked", False):
        return None
    subject = getattr(asset, "subject_features", None)
    if subject is None or getattr(subject, "feature_schema", None) != FEATURE_SCHEMA:
        return None
    if getattr(subject, "roi_primary_selection_required", True):
        return None

    faces = tuple(getattr(subject, "analysis_faces", ()))
    bodies = tuple(getattr(subject, "analysis_bodies", ()))
    face_hashes = tuple(getattr(subject, "face_hashes", ()))
    body_hashes = tuple(getattr(subject, "body_hashes", ()))
    if (len(faces) != 1 or len(bodies) != 1 or len(face_hashes) != 1 or len(body_hashes) != 1
            or getattr(subject, "primary_face_index", None) != 0
            or getattr(subject, "primary_body_index", None) != 0):
        return None
    if any(not isinstance(record, dict) or record.get("origin") != "automatic"
           for record in faces + bodies):
        return None
    face, body = _record_box(faces[0]), _record_box(bodies[0])
    if face is None or body is None:
        return None
    if (_valid_box(getattr(subject, 'face', None)) != face
            or _valid_box(getattr(subject, 'body_box', None)) != body
            or getattr(subject, 'body', None) != body_hashes[0]):
        return None
    expected_digest = getattr(subject, "image_digest", None)
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        return None
    snapshot = _stable_preview(asset, expected_digest)
    if snapshot is None:
        return None
    image, digest = snapshot

    try:
        face_crop = _crop(image, face)
        context_box = _face_context_box(face)
        context_crop = _crop(image, context_box)
    except ValueError:
        return None
    if min(face_crop.size)<16 or float(np.asarray(face_crop.convert('L')).std())<5:
        return None
    face_hash = _dhash(face_crop)
    try:
        if face_hash != int(face_hashes[0], 16):
            return None
        int(body_hashes[0], 16)  # Validate completeness; this hash is not identity evidence.
    except (TypeError, ValueError):
        return None

    orb_rgb = _resize_for_orb(image)
    orb_height, orb_width = orb_rgb.shape[:2]
    orb_mask = np.full((orb_height, orb_width), 255, dtype=np.uint8)
    _paint_box(orb_mask, context_box)
    # Exclude hair/scalp above the face crop as well as the face-anchored torso.
    fx, fy, fw, fh = face
    head_top = max(0.0, fy-fh)
    _paint_box(orb_mask, (context_box[0], head_top, context_box[2], fy+fh-head_top))
    for record in bodies:
        box = _record_box(record)
        if box is None:
            return None
        _paint_box(orb_mask, box)
        bx, by, bw, bh = box
        body_top=max(0.0,by-.25*bh)
        _paint_box(orb_mask,(bx,body_top,bw,by+bh-body_top))
    if np.count_nonzero(orb_mask) / orb_mask.size < _STANDARD.min_background_coverage:
        return None

    gray = cv2.cvtColor(orb_rgb, cv2.COLOR_RGB2GRAY)
    orb = cv2.ORB_create(nfeatures=1000, scaleFactor=1.2, nlevels=8,
                         edgeThreshold=15, patchSize=31, fastThreshold=10)
    keypoints, descriptors = orb.detectAndCompute(gray, orb_mask)
    if descriptors is not None:
        # OpenCV's mask constrains only each keypoint centre. Its rotated,
        # scale-dependent descriptor patch must also stay outside every subject
        # mask; otherwise clothing corners could masquerade as background proof.
        clearance = cv2.distanceTransform(orb_mask, cv2.DIST_L2, 5)
        keep = [i for i, point in enumerate(keypoints)
                if clearance[min(orb_height-1, round(point.pt[1])),
                             min(orb_width-1, round(point.pt[0]))] >= .75 * point.size + 2]
        keypoints = [keypoints[i] for i in keep]
        descriptors = descriptors[keep]
    if descriptors is None:
        points = np.empty((0, 2), dtype=np.float32)
        descriptors = np.empty((0, 32), dtype=np.uint8)
    else:
        points = np.asarray([point.pt for point in keypoints], dtype=np.float32)
        descriptors = np.asarray(descriptors, dtype=np.uint8).copy()

    photo_rgb = cv2.resize(np.asarray(image), (_PHOTO_SIZE, _PHOTO_SIZE), interpolation=cv2.INTER_AREA)
    background_mask = cv2.resize(orb_mask, (_PHOTO_SIZE, _PHOTO_SIZE), interpolation=cv2.INTER_AREA)
    background_mask = np.where(background_mask==255,255,0).astype(np.uint8)
    for array in (points, descriptors, photo_rgb, background_mask):
        array.flags.writeable = False
    return PortraitContext(
        context_hash=_dhash(context_crop), face_hash=face_hash, face_box=face,
        face_gray=_face_gray(face_crop), face_color_hist=_color_hist(face_crop),
        context_color_hist=_color_hist(context_crop), orb_points=points,
        orb_descriptors=descriptors, orb_size=(orb_width, orb_height),
        photo_rgb=photo_rgb, background_mask=background_mask,
        preview_digest=digest, explicit_body_bound=_body_bound(face, body),
        body_box=body, body_hash=int(body_hashes[0],16), whole_hash=_whole_dhash(image),
        aspect=image.width/image.height, context_gray=_face_gray(context_crop),
    )


def _region_structure(left, right, mask):
    """SSIM and colour on all union-region pixels, never resized ROI crops."""
    lgray=cv2.cvtColor(left,cv2.COLOR_RGB2GRAY).astype(np.float32)
    rgray=cv2.cvtColor(right,cv2.COLOR_RGB2GRAY).astype(np.float32)
    values=[]
    weights=[]
    for y in range(0,_PHOTO_SIZE,16):
        for x in range(0,_PHOTO_SIZE,16):
            sl=np.s_[y:y+16,x:x+16]
            selected=mask[sl]
            if np.count_nonzero(selected)<32:
                continue
            a,b=lgray[sl][selected],rgray[sl][selected]
            am,bm=a.mean(),b.mean()
            av,bv=a.var(),b.var()
            covariance=np.mean(a*b)-am*bm
            score=((2*am*bm+_C1)/(am*am+bm*bm+_C1)
                   *(2*covariance+_C2)/(av+bv+_C2))
            values.append(float(score))
            weights.append(len(a))
    if not values:
        return -1.0
    return float(np.average(values,weights=weights))


def _body_mask(box):
    mask=np.full((_PHOTO_SIZE,_PHOTO_SIZE),255,np.uint8)
    _paint_box(mask,box)
    return mask==0


def _translation_background_match(left,right,warped,matrix,preset):
    """Independent, fully masked and spatially distributed translation check.

    Thin background strips contribute only their actual pixel count. A block
    centre outside a person does not make that block background. Robust SSIM
    tolerates a minority of moving bystanders, but matching textured support
    must still span both axes and a substantial hull, independent of ORB points.
    """
    mask=cv2.warpAffine(left.background_mask,matrix,(_PHOTO_SIZE,_PHOTO_SIZE))==255
    mask&=right.background_mask==255
    mask=cv2.erode(mask.astype(np.uint8),np.ones((3,3),np.uint8))>0
    if np.mean(mask)<.12:
        return False
    difference=np.abs(warped.astype(np.float32)-right.photo_rgb.astype(np.float32))
    if float(difference[mask].mean()/255)>.04:
        return False
    gray=[cv2.cvtColor(im,cv2.COLOR_RGB2GRAY).astype(np.float32)
          for im in (warped,right.photo_rgb)]
    scores=[];weights=[];positions=[]
    for y in range(0,_PHOTO_SIZE,16):
        for x in range(0,_PHOTO_SIZE,16):
            sl=np.s_[y:y+16,x:x+16];valid=mask[sl]
            if np.count_nonzero(valid)<32:
                continue
            a,b=[im[sl][valid] for im in gray]
            if min(float(a.std()),float(b.std()))<8:
                continue
            am,bm=a.mean(),b.mean();av,bv=a.var(),b.var()
            covariance=np.mean(a*b)-am*bm
            score=((2*am*bm+_C1)/(am*am+bm*bm+_C1)
                   *(2*covariance+_C2)/(av+bv+_C2))
            yy,xx=np.nonzero(valid)
            scores.append(float(score));weights.append(len(a))
            positions.append(((x+float(xx.mean()))/_PHOTO_SIZE,(y+float(yy.mean()))/_PHOTO_SIZE))
    if len(scores)<8 or sum(weights)<512:
        return False
    order=np.argsort(scores)
    middle=np.searchsorted(np.cumsum(np.asarray(weights)[order]),sum(weights)/2)
    median=np.asarray(scores)[order[middle]]
    cutoff=.86 if preset=='strict' else .78
    if median<cutoff:
        return False
    selected=np.asarray(scores)>=(.78 if preset=='strict' else .70)
    if sum(w for w,ok in zip(weights,selected) if ok)/sum(weights)<.60:
        return False
    points=np.float32(positions)[selected]
    if len(points)<4 or np.any(np.ptp(points,axis=0)<.50):
        return False
    return abs(float(cv2.contourArea(cv2.convexHull(points))))>=.10


def _small_translation_body_match(left, right, preset):
    """Repair a drifting automatic body ROI using direct near-duplicate proof.

    This does not turn a flat background into scene evidence. It is entered
    only when the old explicit body hashes conflict AND their boxes differ.
    A fixed small translation must make the whole frame and the entire union
    of both body rectangles pass the existing strict visual cutoff. The face
    and face-anchored context corroborate independently; face/body boxes need
    not belong to the same person. No ROI or persisted descriptor is changed.
    """
    strict=preset=='strict'
    whole_hash_limit=8 if strict else 10
    if (left.whole_hash^right.whole_hash).bit_count()>whole_hash_limit:
        return False
    if max(left.aspect,right.aspect)/min(left.aspect,right.aspect)>1.08:
        return False
    if (left.body_hash^right.body_hash).bit_count()<=whole_hash_limit:
        return False
    if max(abs(a-b) for a,b in zip(left.body_box,right.body_box))<.01:
        return False
    if _block_ssim(left.face_gray,right.face_gray)<(.86 if strict else .80):
        return False
    if _block_ssim(left.context_gray,right.context_gray)<(.72 if strict else .65):
        return False
    lm,rm=_body_mask(left.body_box),_body_mask(right.body_box)
    intersection=np.count_nonzero(lm&rm)
    union=np.count_nonzero(lm|rm)
    if not union or intersection/union<.60:
        return False
    gray=[cv2.cvtColor(c.photo_rgb,cv2.COLOR_RGB2GRAY).astype(np.float32)
          for c in (left,right)]
    # OpenCV may window its inputs in place; all contexts remain immutable.
    shift,response=cv2.phaseCorrelate(gray[0].copy(),gray[1].copy(),
        cv2.createHanningWindow((_PHOTO_SIZE,_PHOTO_SIZE),cv2.CV_32F))
    if (not all(math.isfinite(v) for v in (*shift,response)) or response<.80
            or max(abs(v) for v in shift)>_PHOTO_SIZE*(.02 if strict else .03)):
        return False
    matrix=np.float32([[1,0,shift[0]],[0,1,shift[1]]])
    warped=cv2.warpAffine(left.photo_rgb,matrix,(_PHOTO_SIZE,_PHOTO_SIZE))
    valid=cv2.warpAffine(np.full(lm.shape,255,np.uint8),matrix,
                         (_PHOTO_SIZE,_PHOTO_SIZE))==255
    # Require the complete interpolation support to remain inside the frame.
    valid=cv2.erode(valid.astype(np.uint8),np.ones((3,3),np.uint8))>0
    moved=cv2.warpAffine(lm.astype(np.uint8)*255,matrix,(_PHOTO_SIZE,_PHOTO_SIZE))>0
    body=(moved|rm)&valid
    if np.mean(body)<.12 or np.mean(valid)<.90:
        return False
    cutoff=.90 if strict else .86
    if (_region_structure(warped,right.photo_rgb,valid)<cutoff
            or _region_structure(warped,right.photo_rgb,body)<cutoff):
        return False
    # Colour uses every body-union pixel, including disagreement near either
    # rectangle's boundary. A stable companion face cannot replace this check.
    hist=[]
    for image in (warped,right.photo_rgb):
        pixels=image[body].reshape(-1,1,3)
        hist.append(_color_hist(Image.fromarray(pixels)))
    return (_hist_similarity(*hist)>=.80
            and _translation_background_match(left,right,warped,matrix,preset))


def _block_ssim(left: np.ndarray, right: np.ndarray) -> float:
    lblocks = left.reshape(8, 8, 8, 8).transpose(0, 2, 1, 3).reshape(64, 64).astype(np.float32)
    rblocks = right.reshape(8, 8, 8, 8).transpose(0, 2, 1, 3).reshape(64, 64).astype(np.float32)
    lmean, rmean = lblocks.mean(axis=1), rblocks.mean(axis=1)
    lvar, rvar = lblocks.var(axis=1), rblocks.var(axis=1)
    covariance = np.einsum("ij,ij->i", lblocks, rblocks, dtype=np.float32) / 64 - lmean * rmean
    luminance = (2 * lmean * rmean + _C1) / (lmean ** 2 + rmean ** 2 + _C1)
    structure = (2 * covariance + _C2) / (lvar + rvar + _C2)
    return float(np.clip(np.mean(luminance * structure), -1, 1))


def _hist_similarity(left: np.ndarray, right: np.ndarray) -> float:
    return float(cv2.compareHist(left, right, cv2.HISTCMP_INTERSECT))


def _good_matches(left: PortraitContext, right: PortraitContext) -> list[cv2.DMatch]:
    if len(left.orb_descriptors) < 2 or len(right.orb_descriptors) < 2:
        return []
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(left.orb_descriptors, right.orb_descriptors, k=2)
    candidates = [near for near, far in pairs if near.distance < .78 * far.distance]
    # A repeated texture may map many source points to one target point. Keep
    # only its best match before estimating geometry.
    unique: dict[int, cv2.DMatch] = {}
    for match in candidates:
        current = unique.get(match.trainIdx)
        if current is None or match.distance < current.distance:
            unique[match.trainIdx] = match
    return sorted(unique.values(), key=lambda match: (match.distance, match.queryIdx, match.trainIdx))


def _distributed(points: np.ndarray, size: tuple[int, int], cfg: _PoseConfig) -> bool:
    if len(points) < 3:
        return False
    width, height = size
    span_x = float(np.ptp(points[:, 0])) / max(width, 1)
    span_y = float(np.ptp(points[:, 1])) / max(height, 1)
    hull = cv2.convexHull(points.astype(np.float32))
    area = abs(float(cv2.contourArea(hull))) / max(width * height, 1)
    return span_x >= cfg.min_span and span_y >= cfg.min_span and area >= cfg.min_area


def _normalised_affine(matrix: np.ndarray, left_size: tuple[int, int],
                       right_size: tuple[int, int]) -> np.ndarray:
    lw, lh = left_size
    rw, rh = right_size
    affine = np.vstack((matrix, (0.0, 0.0, 1.0)))
    source = np.diag((float(lw), float(lh), 1.0))
    target = np.diag((1.0 / rw, 1.0 / rh, 1.0))
    return target @ affine @ source


def _background_photometry(left: PortraitContext, right: PortraitContext,
                            matrix: np.ndarray, cfg: _PoseConfig) -> bool:
    lw, lh = left.orb_size
    rw, rh = right.orb_size
    affine = np.vstack((matrix, (0.0, 0.0, 1.0)))
    to_orb = np.diag((lw / _PHOTO_SIZE, lh / _PHOTO_SIZE, 1.0))
    from_orb = np.diag((_PHOTO_SIZE / rw, _PHOTO_SIZE / rh, 1.0))
    small = (from_orb @ affine @ to_orb)[:2].astype(np.float32)
    size = (_PHOTO_SIZE, _PHOTO_SIZE)
    warped = cv2.warpAffine(left.photo_rgb, small, size, flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    warped_mask = cv2.warpAffine(left.background_mask, small, size, flags=cv2.INTER_NEAREST,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    valid = cv2.warpAffine(np.full(size, 255, dtype=np.uint8), small, size,
                           flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    common = (warped_mask > 0) & (right.background_mask > 0) & (valid > 0)
    if np.count_nonzero(common) / common.size < cfg.min_background_coverage:
        return False
    difference = np.abs(warped.astype(np.float32) - right.photo_rgb.astype(np.float32))
    return float(difference[common].mean() / 255.0) <= cfg.max_background_mae


def _scene_compatible(left: PortraitContext, right: PortraitContext,
                      cfg: _PoseConfig) -> bool:
    matches = _good_matches(left, right)
    if len(matches) < cfg.min_inliers:
        return False
    source = np.asarray([left.orb_points[match.queryIdx] for match in matches], dtype=np.float32)
    target = np.asarray([right.orb_points[match.trainIdx] for match in matches], dtype=np.float32)
    matrix, inlier_mask = cv2.estimateAffinePartial2D(
        source, target, method=cv2.RANSAC, ransacReprojThreshold=3.0,
        maxIters=2000, confidence=.99, refineIters=10,
    )
    if matrix is None or inlier_mask is None:
        return False
    selected = inlier_mask.reshape(-1).astype(bool)
    inliers = int(np.count_nonzero(selected))
    if inliers < cfg.min_inliers or inliers / len(matches) < cfg.min_inlier_ratio:
        return False
    if not (_distributed(source[selected], left.orb_size, cfg)
            and _distributed(target[selected], right.orb_size, cfg)):
        return False

    normalised = _normalised_affine(matrix, left.orb_size, right.orb_size)
    linear = normalised[:2, :2]
    scale = math.sqrt(abs(float(np.linalg.det(linear))))
    rotation = abs(math.degrees(math.atan2(float(linear[1, 0]), float(linear[0, 0]))))
    mapped_center = normalised @ np.asarray((.5, .5, 1.0))
    translation = float(np.max(np.abs(mapped_center[:2] - .5)))
    if (not cfg.min_scale <= scale <= cfg.max_scale
            or rotation > cfg.max_rotation or translation > cfg.max_translation):
        return False
    return _background_photometry(left, right, matrix, cfg)


def pose_compatible(left: PortraitContext | Any, right: PortraitContext | Any,
                    preset: str = "standard") -> bool:
    """Return True only for complete scene, face, and body-context evidence.

    ``left`` and ``right`` may be prebuilt contexts or assets. This pair result
    is a weak grouping edge, not a person-identity claim; callers must apply
    component-wide coherence checks before merging groups.
    """
    cfg = _CONFIGS[preset]
    if not isinstance(left, PortraitContext):
        left = build_portrait_context(left)
    if not isinstance(right, PortraitContext):
        right = build_portrait_context(right)
    if left is None or right is None:
        return False

    lx, ly, lw, lh = left.face_box
    rx, ry, rw, rh = right.face_box
    displacement = math.hypot(lx + lw / 2 - rx - rw / 2, ly + lh / 2 - ry - rh / 2)
    area_ratio = max(lw * lh, rw * rh) / max(1e-9, min(lw * lh, rw * rh))
    if displacement > cfg.face_displacement or area_ratio > cfg.face_area_ratio:
        return False
    if (left.face_hash ^ right.face_hash).bit_count() > cfg.face_hash:
        return False
    if _block_ssim(left.face_gray, right.face_gray) + 1e-6 < cfg.face_ssim:
        return False
    if _hist_similarity(left.face_color_hist, right.face_color_hist) < cfg.face_color:
        return False
    if (left.context_hash ^ right.context_hash).bit_count() > cfg.context_hash:
        return False
    if _hist_similarity(left.context_color_hist, right.context_color_hist) < cfg.context_color:
        return False
    return (_scene_compatible(left, right, cfg)
            or _small_translation_body_match(left,right,preset))
