"""Per-photo AI focus review using bounded, native-source evidence images."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageOps

from .ai_api import ApiError, call_model
from .crop_settings import CropSettings


PROMPT_VERSION = "focus-review-v1"
MAX_IMAGE_EDGE = 1024
MAX_DETAIL_IMAGES = 18  # One additional slot is reserved for the overview.
_RESULT_KEYS = {"photo_identity", "status", "reason"}
_STATUSES = {"clear", "blur", "uncertain"}
_GENERATION_PROFILE_FIELDS = (
    "id", "base_url", "model", "preset_id", "temperature", "top_p",
    "max_tokens", "max_completion_tokens", "seed",
)


def load_full_image(asset: Any) -> Image.Image:
    """Defer the heavier local-focus dependency until native decoding is needed."""
    from .face_focus import load_full_image as decode

    return decode(asset)


def detail_features(asset: Any, crop_settings: CropSettings) -> Any:
    """Defer face-detector imports so cached-result reads stay lightweight."""
    from .subject import detail_features as detect_subject

    return detect_subject(asset, crop_settings)


def _subject_face(asset: Any, crop_settings: CropSettings) -> tuple[float, float, float, float] | None:
    subject = detail_features(asset, crop_settings)
    face = getattr(subject, "face", None) if subject else None
    if not face or len(face) != 4:
        return None
    try:
        values = tuple(float(value) for value in face)
    except (TypeError, ValueError):
        return None
    x, y, width, height = values
    if not all(math.isfinite(value) for value in values):
        return None
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
        return None
    return values


def _source_identity(asset: Any, face: tuple[float, float, float, float] | None) -> dict[str, Any]:
    source = Path(asset.raw_path or asset.primary_path)
    stat = source.stat()
    return {
        "path": str(source.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "face": list(face) if face else None,
    }


def _cache_digest(
    asset: Any,
    face: tuple[float, float, float, float] | None,
    profile: Mapping[str, object],
) -> str:
    identity = {
        "prompt_version": PROMPT_VERSION,
        "source": _source_identity(asset, face),
        # Include only service/generation identity.  Display metadata, timeout, and
        # credentials are deliberately excluded from both digest input and cache.
        "profile": {key: profile[key] for key in _GENERATION_PROFILE_FIELDS if key in profile},
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _balanced_ranges(length: int, maximum: int = MAX_IMAGE_EDGE) -> list[tuple[int, int]]:
    count = max(1, math.ceil(length / maximum))
    return [(round(index * length / count), round((index + 1) * length / count)) for index in range(count)]


def _tile_boxes(box: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    left, top, right, bottom = box
    boxes = [
        (left + x0, top + y0, left + x1, top + y1)
        for y0, y1 in _balanced_ranges(bottom - top)
        for x0, x1 in _balanced_ranges(right - left)
    ]
    if len(boxes) <= MAX_DETAIL_IMAGES:
        return boxes
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    boxes.sort(key=lambda item: ((item[0] + item[2]) / 2 - center_x) ** 2 + ((item[1] + item[3]) / 2 - center_y) ** 2)
    return sorted(boxes[:MAX_DETAIL_IMAGES], key=lambda item: (item[1], item[0]))


def _face_box(
    image_size: tuple[int, int],
    face: tuple[float, float, float, float],
) -> tuple[int, int, int, int]:
    image_width, image_height = image_size
    x, y, width, height = face
    # A small border keeps the face boundary readable while retaining native pixels.
    left = max(0, math.floor((x - width * .15) * image_width))
    top = max(0, math.floor((y - height * .15) * image_height))
    right = min(image_width, math.ceil((x + width * 1.15) * image_width))
    bottom = min(image_height, math.ceil((y + height * 1.15) * image_height))
    return left, top, max(left + 1, right), max(top + 1, bottom)


def _center_box(image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = image_size
    crop_width = min(width, MAX_IMAGE_EDGE)
    crop_height = min(height, MAX_IMAGE_EDGE)
    left = (width - crop_width) // 2
    top = (height - crop_height) // 2
    return left, top, left + crop_width, top + crop_height


def _preview_geometry_matches(asset: Any, image_size: tuple[int, int]) -> bool:
    preview_path = getattr(asset, "preview_path", None)
    if not preview_path:
        return True
    with Image.open(preview_path) as source:
        preview_width, preview_height = ImageOps.exif_transpose(source).size
    width, height = image_size
    if min(width, height, preview_width, preview_height) <= 0:
        return False
    return abs((width / height) / (preview_width / preview_height) - 1) <= .05


def _prepare_focus_images(
    asset: Any,
    crop_settings: CropSettings,
    out_dir: Path,
    face: tuple[float, float, float, float] | None = None,
) -> tuple[list[Path], bool]:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    proposed_face = face if face is not None else _subject_face(asset, crop_settings)
    with load_full_image(asset) as decoded:
        image = decoded.convert("RGB")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("源照片尺寸无效。")

        overview = image.copy()
        if max(overview.size) > MAX_IMAGE_EDGE:
            overview.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.BOX)
        overview_path = directory / "overview.png"
        overview.save(overview_path, format="PNG")
        overview.close()

        reliable_face = proposed_face if proposed_face and _preview_geometry_matches(asset, image.size) else None
        detail_box = _face_box(image.size, reliable_face) if reliable_face else _center_box(image.size)
        detail_paths: list[Path] = []
        label = "face_native" if reliable_face else "center_native"
        for index, box in enumerate(_tile_boxes(detail_box), start=1):
            path = directory / f"{label}_{index:02d}.png"
            with image.crop(box) as detail:
                detail.save(path, format="PNG")
            detail_paths.append(path)
    return [overview_path, *detail_paths], reliable_face is not None


def prepare_focus_images(asset: Any, crop_settings: CropSettings, out_dir: Path) -> list[Path]:
    """Write overview and native-pixel focus evidence, returning them in prompt order.

    Every output is at most 1024 pixels on either edge.  Detail images are cropped
    or tiled without resampling, upscaling, sharpening, or lossy recompression.
    """
    images, _ = _prepare_focus_images(asset, crop_settings, Path(out_dir))
    return images


def _prompt(photo_identity: str, face_found: bool) -> str:
    target = (
        "已可靠定位主要人脸。请以人脸，尤其是眼睛与睫毛的真实细节作为清晰度判断主体。"
        if face_found
        else "未能可靠定位主要人脸。不要猜测主体位置；无论画面其他区域看起来多清楚，status 必须为 uncertain。"
    )
    return f"""你正在审核单张照片的主体对焦清晰度。第一张图是总览，后续图片是从原始文件直接裁切的原生像素细节，未放大、未锐化。{target}

只判断主体是否存在明显失焦或运动模糊：
- clear：主体关键细节明确清楚；
- blur：主体关键细节明确严重模糊；
- uncertain：证据不足、主体无法可靠定位、细节太少，或介于两者之间。

只输出一个 JSON 对象，不能使用 Markdown、代码围栏或额外文字，也不能增加字段。格式必须是：
{{"photo_identity":{json.dumps(photo_identity, ensure_ascii=False)},"status":"clear|blur|uncertain","reason":"一句简洁的中文理由"}}
photo_identity 必须逐字照抄。"""


def _parse_response(text: object, photo_identity: str) -> dict[str, str]:
    if not isinstance(text, str) or not text.strip():
        raise ApiError("AI 清晰度审核没有返回 JSON 文本。")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite value")),
        )
    except (TypeError, ValueError):
        raise ApiError("AI 清晰度审核返回的不是严格 JSON。") from None
    if not isinstance(value, dict) or set(value) != _RESULT_KEYS:
        raise ApiError("AI 清晰度审核返回的 JSON 字段无效。")
    if value["photo_identity"] != photo_identity:
        raise ApiError("AI 清晰度审核返回了不匹配的照片标识。")
    if value["status"] not in _STATUSES:
        raise ApiError("AI 清晰度审核返回了无效状态。")
    reason = value["reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ApiError("AI 清晰度审核返回了无效理由。")
    return {"status": value["status"], "reason": reason.strip()}


def _cached_result(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("status") not in _STATUSES:
        return None
    if value.get("source") != "api" or not isinstance(value.get("reason"), str) or not value["reason"].strip():
        return None
    allowed = {"status", "reason", "source", "raw_response", "usage"}
    if not set(value) <= allowed:
        return None
    return value


def _write_cache(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def review_focus(
    asset: Any,
    crop_settings: CropSettings,
    profile: Mapping[str, object],
    cache_dir: Path,
) -> dict[str, Any]:
    """Ask the configured model for one photo's focus state, with safe caching."""
    settings = crop_settings or CropSettings()
    face = _subject_face(asset, settings)
    digest = _cache_digest(asset, face, profile)
    root = Path(cache_dir)
    cache_path = root / PROMPT_VERSION / f"{digest}.json"
    if cached := _cached_result(cache_path):
        return cached

    image_paths, face_found = _prepare_focus_images(
        asset, settings, root / PROMPT_VERSION / "images" / digest, face
    )
    photo_identity = str(asset.stem)
    response = call_model(profile, _prompt(photo_identity, face_found), image_paths)
    if not isinstance(response, Mapping):
        raise ApiError("AI 清晰度审核返回了无法识别的响应。")
    raw_response = response.get("text")
    parsed = _parse_response(raw_response, photo_identity)
    if not face_found:
        parsed = {"status": "uncertain", "reason": "未检测到可靠人脸，无法可靠判断主体清晰度。"}
    result: dict[str, Any] = {**parsed, "source": "api", "raw_response": raw_response}
    if isinstance(response.get("usage"), dict):
        result["usage"] = response["usage"]
    _write_cache(cache_path, result)
    return result
