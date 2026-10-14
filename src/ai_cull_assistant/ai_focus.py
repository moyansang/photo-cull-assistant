"""Per-photo AI focus review using bounded, native-source evidence images."""
from __future__ import annotations
from .models import source_identity_path

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageOps

from .ai_api import ApiError, call_model
from .crop_settings import CropSettings
from .evidence_cleanup import guard_focus_review


PROMPT_VERSION = "focus-review-v3"
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



def _mode(asset):
    return getattr(asset, 'analysis_mode', 'portrait')


def _body_candidates(asset, settings):
    if _mode(asset) == 'landscape':return []
    from .subject import body_candidates
    return body_candidates(asset, settings)


def _bind_result(result, asset, settings):
    from .ai_project import technical_fingerprint
    return dict(result, technical_fingerprint=technical_fingerprint(asset, settings))


def _subject_tags(asset, settings, face, kind):
    """Durable geometry identity, independent of display order or face/body mix."""
    from .crop_settings import face_box_key
    tags={'evidence_kind':kind,'eye_evidence':kind=='face'}
    if kind=='body':
        box=settings.photos.get(settings.key(asset),{}).get('selected_bodies',[None])[0]
        if box is not None:
            tags.update(body_box=list(box),subject_id='body:'+face_box_key(box))
            evidence=getattr(asset,'clarity_evidence',None) or {}
            for body in evidence.get('body_candidates',[]):
                if face_box_key(body.get('box',[]))==face_box_key(box):
                    tags.update({key:body[key] for key in ('associated_face','association') if key in body})
                    break
    elif kind=='face' and face is not None:
        tags.update(face_box=list(face),subject_id='face:'+face_box_key(face))
    return tags


def _subject_face(asset: Any, crop_settings: CropSettings) -> tuple[float, float, float, float] | None:
    if _mode(asset) == 'landscape':return None
    entry=crop_settings.photos.get(crop_settings.key(asset),{})
    if ('selected_bodies' in entry and 'selected_faces' not in entry) or ('selected_faces' in entry and not entry['selected_faces']):return None
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


def _subject_faces(asset: Any, crop_settings: CropSettings) -> list[tuple[Any, tuple[float, float, float, float]]]:
    """Return selected participants while retaining the old single-face path."""
    if _mode(asset) == 'landscape':return []
    entry = crop_settings.photos.get(crop_settings.key(asset), {})
    if 'selected_bodies' in entry and 'selected_faces' not in entry:return []
    if 'selected_faces' not in entry:
        face = _subject_face(asset, crop_settings)
        subject = detail_features(asset, crop_settings) if face else None
        return [(subject, face)] if face else []
    from .subject import detail_features_list
    result = []
    for subject in detail_features_list(asset, crop_settings):
        face = getattr(subject, "face", None)
        if not face or len(face) != 4:
            continue
        try:
            values = tuple(float(value) for value in face)
        except (TypeError, ValueError):
            continue
        x, y, width, height = values
        if (all(math.isfinite(value) for value in values)
                and x >= 0 and y >= 0 and width > 0 and height > 0
                and x + width <= 1.000001 and y + height <= 1.000001):
            result.append((subject, values))
    return result


def _source_identity(asset: Any, face: tuple[float, float, float, float] | None) -> dict[str, Any]:
    # Both RAW and paired JPG can provide pixels. Changing either invalidates
    # cache evidence; the normal decode path decides which one is actually used.
    paths=[]
    for value in asset.rating_target_paths:
        source=Path(value)
        try:
            stat=source.stat();paths.append((source_identity_path(asset,source),stat.st_size,stat.st_mtime_ns))
        except OSError:
            paths.append((source_identity_path(asset,source),None,None))
    return {'paths':paths, 'face':list(face) if face else None, 'mode':_mode(asset)}



def _cache_digest(
    asset: Any,
    face: tuple[float, float, float, float] | None,
    profile: Mapping[str, object],
    settings: CropSettings | None = None,
) -> str:
    identity = {
        "prompt_version": PROMPT_VERSION,
        "source": _source_identity(asset, face),
        "body": (getattr(asset, 'clarity_evidence', None) or {}).get('body'),
        "subjects": settings.photos.get(settings.key(asset), {}) if settings else {},
        "body_associations": (getattr(asset, 'clarity_evidence', None) or {}).get('body_candidates'),
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
    *,
    subject_override: Any = None,
    include_body: bool = True,
    decoded_image=None,
) -> tuple[list[Path], bool]:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    proposed_face = face if face is not None else _subject_face(asset, crop_settings)
    from contextlib import nullcontext
    with (nullcontext(decoded_image) if decoded_image is not None else load_full_image(asset)) as decoded:
        image = decoded if decoded.mode == "RGB" else decoded.convert("RGB")
        width, height = image.size
        if width <= 0 or height <= 0:
            raise ValueError("源照片尺寸无效。")

        overview = ImageOps.contain(image, (MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.BOX) if max(image.size) > MAX_IMAGE_EDGE else image.copy()
        overview_path = directory / "overview.png"
        overview.save(overview_path, format="PNG")
        overview.close()

        reliable_face = proposed_face if proposed_face and _preview_geometry_matches(asset, image.size) else None
        bodies = (_body_candidates(asset, crop_settings)
                  if not reliable_face and _preview_geometry_matches(asset,image.size) else [])
        body_boxes_native = [_face_box(image.size, tuple(item['box'])) for item in bodies]
        detail_box = _face_box(image.size, reliable_face) if reliable_face else _center_box(image.size)
        detail_paths: list[Path] = []
        # Dedicated native eye windows make important detail visible without
        # resampling a large face tile. Never invent landmarks for manual boxes.
        subject = subject_override if reliable_face and subject_override is not None else (
            detail_features(asset, crop_settings) if reliable_face else None
        )
        landmarks = getattr(subject, "landmarks", None)
        if landmarks and len(landmarks) >= 2:
            radius = max(48, min(256, round(reliable_face[2] * width * .20)))
            for index, (ex, ey) in enumerate(landmarks[:2], 1):
                if not (0 <= ex <= 1 and 0 <= ey <= 1):
                    continue
                box = (max(0, round(ex*width)-radius), max(0, round(ey*height)-radius),
                       min(width, round(ex*width)+radius), min(height, round(ey*height)+radius))
                path = directory / f"eye_native_{index:02d}.png"
                image.crop(box).save(path, format="PNG")
                detail_paths.append(path)
        label = "face_native" if reliable_face else "center_native"
        detail_boxes = _tile_boxes(detail_box)
        if _mode(asset) == 'landscape':
            label='landscape_native'
            # Distributed native windows and full overview: sky/water alone
            # cannot stand in for whole-scene evidence.
            cw,ch=min(width,MAX_IMAGE_EDGE),min(height,MAX_IMAGE_EDGE)
            detail_boxes=list(dict.fromkeys((round(x*(width-cw)),round(y*(height-ch)),
                round(x*(width-cw))+cw,round(y*(height-ch))+ch)
                for y in (0,.5,1) for x in (0,.5,1)))
        elif body_boxes_native:
            label='body_region_native'
            detail_boxes=[box for candidate in body_boxes_native for box in _tile_boxes(candidate)]
        body = ((getattr(asset, 'clarity_evidence', None) or {}).get('body') or {}) if include_body else {}
        body_boxes = []
        if body:
            from .body_focus import body_review_boxes
            body_boxes = body_review_boxes(body)
        for index, box in enumerate(detail_boxes[:max(0,MAX_DETAIL_IMAGES-len(detail_paths)-len(body_boxes))], start=1):
            path = directory / f"{label}_{index:02d}.png"
            with image.crop(box) as detail:
                detail.save(path, format="PNG")
            detail_paths.append(path)
        for index, box in enumerate(body_boxes[:max(0,MAX_DETAIL_IMAGES-len(detail_paths))],1):
            # Native windows; no downsampling that could hide motion blur.
            left,top,right,bottom=box
            cx,cy=(left+right)//2,(top+bottom)//2
            left=max(0,cx-min(512,(right-left)//2));top=max(0,cy-min(512,(bottom-top)//2))
            right=min(width,left+min(1024,box[2]-box[0]));bottom=min(height,top+min(1024,box[3]-box[1]))
            path=directory/f'body_native_{index:02d}.png'
            image.crop((left,top,right,bottom)).save(path,format='PNG')
            detail_paths.append(path)
    return [overview_path, *detail_paths], reliable_face is not None


def prepare_focus_images(asset: Any, crop_settings: CropSettings, out_dir: Path) -> list[Path]:
    """Write overview and native-pixel focus evidence, returning them in prompt order.

    Every output is at most 1024 pixels on either edge.  Detail images are cropped
    or tiled without resampling, upscaling, sharpening, or lossy recompression.
    """
    images, _ = _prepare_focus_images(asset, crop_settings, Path(out_dir))
    return images


def _prompt(photo_identity: str, face_found: bool, body_images: int = 0, *, mode='portrait', body_only=False) -> str:
    target = (
        "已可靠定位主要人脸。请以人脸，尤其是眼睛与睫毛的真实细节作为清晰度判断主体。"
        if face_found
        else "未能可靠定位主要人脸。不要猜测主体位置；无论画面其他区域看起来多清楚，status 必须为 uncertain。"
    )
    if mode == 'landscape':
        target = "本次为用户选择的风景/通用模式，检查完整画面的有效细节，不寻找人脸，不要求框选。天空、水面等低纹理及正常景深虚化不等于模糊；以有纹理的主要景物边缘和多区域证据判断，缺少可靠纹理用 uncertain。"
    elif body_only:
        target = "没有可靠人脸，但已可靠定位所选人体。仅判断人体躯干与主要肢体的边缘、失焦和运动拖影；不能用身体清晰替代眼区证据，理由中明确眼部未核验。背景和未选中人物不参与判断。"
    if body_images:
        target += f" 最后 {body_images} 张是与所选人脸对应的主体身体细节。检查躯干及手臂、腿等主要部位的明显运动拖影；忽略仅指尖、发梢、衣摆轻微运动及背景景深虚化。observations 还须加入 body_motion 字段（present|absent|uncertain）。任何主体部位明确模糊则 blur，身体不确定不能仅因脸清楚而 clear。"
    body_field = ',"body_motion":"present|absent|uncertain"' if body_images else ''
    return f"""你正在审核单张照片的主体对焦清晰度。第一张图是总览，后续图片是从原始文件直接裁切的原生像素细节，未放大、未锐化。{target}

本任务只做技术清晰度复核，不给星级，不评价表情、构图或整体选片优先级。
检查真实边缘而不是仅凭眼睛轮廓、深色眼线或假睫毛可见就说清晰。注意沿同一方向延伸的亮点、重影、发丝粘连和睫毛边缘软化。
噪声、柔和光线、皮肤平滑、遮挡或低纹理不等同于失焦；不能因看不见被遮挡的细节而认定严重模糊。
先分别观察失焦软化、运动拖影、噪声、遮挡，再判断主体：
- clear：主体关键细节明确清楚；
- blur：主体关键细节明确严重模糊；
- uncertain：证据不足、主体无法可靠定位、细节太少，或介于两者之间。

只输出一个 JSON 对象，不能使用 Markdown、代码围栏或额外文字，也不能增加字段。格式必须是：
{{"photo_identity":{json.dumps(photo_identity, ensure_ascii=False)},"status":"clear|blur|uncertain","reason":"指出实际看到的边缘证据，不使用泛泛的睫毛清晰结论","observations":{{"defocus":"present|absent|uncertain","motion_blur":"present|absent|uncertain","noise":"present|absent|uncertain","occlusion":"present|absent|uncertain"{body_field}}}}}
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
    if not isinstance(value, dict) or set(value) not in (_RESULT_KEYS, _RESULT_KEYS | {"observations"}):
        raise ApiError("AI 清晰度审核返回的 JSON 字段无效。")
    if value["photo_identity"] != photo_identity:
        raise ApiError("AI 清晰度审核返回了不匹配的照片标识。")
    if value["status"] not in _STATUSES:
        raise ApiError("AI 清晰度审核返回了无效状态。")
    reason = value["reason"]
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ApiError("AI 清晰度审核返回了无效理由。")
    parsed = {"status": value["status"], "reason": reason.strip()}
    if "observations" in value:
        observations = value["observations"]
        if (not isinstance(observations, dict)
                or set(observations) not in ({"defocus", "motion_blur", "noise", "occlusion"}, {"defocus", "motion_blur", "noise", "occlusion", "body_motion"})
                or any(not isinstance(v, str) or v not in {"present", "absent", "uncertain"} for v in observations.values())):
            raise ApiError("AI 清晰度审核观察字段无效。")
        parsed["observations"] = observations
        judged = ('defocus','motion_blur','body_motion') if 'body_motion' in observations else ('defocus','motion_blur')
        if parsed['status'] == 'clear' and any(observations[k] != 'absent' for k in judged):
            parsed.update(status='uncertain', reason='模型清晰结论与其模糊观察冲突：' + reason.strip())
        if parsed['status'] == 'blur' and all(observations[k] != 'present' for k in judged):
            parsed.update(status='uncertain', reason='模型未给出明确失焦或拖影证据：' + reason.strip())
    return parsed


def _cached_result(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("status") not in _STATUSES:
        return None
    if value.get("source") != "api" or not isinstance(value.get("reason"), str) or not value["reason"].strip():
        return None
    allowed = {"status", "reason", "source", "raw_response", "usage", "observations", "audit", "prompt_version"}
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
    from .participant_review import needs_person_review
    if _mode(asset) != 'landscape' and needs_person_review(asset, settings.photos.get(settings.key(asset), {})):
        raise ValueError('人物待确认：请先选择合影主体并重新扫描，再进行 AI 复核。')
    participants = _subject_faces(asset, settings)
    entry=settings.photos.get(settings.key(asset),{})
    bodies=(_body_candidates(asset,settings)
            if not participants or 'selected_bodies' in entry else [])
    targets=[(subject,face,settings,'face') for subject,face in participants]
    for body in bodies:
        values=dict(entry,selected_faces=[],selected_bodies=[body['box']])
        values.pop('manual_face',None)
        per_body=replace(settings,photos={**settings.photos,settings.key(asset):values})
        targets.append((None,None,per_body,'body'))
    if len(targets)>1:
        results=[]
        for index,(subject,face,subject_settings,kind) in enumerate(targets,1):
            result=_review_one(asset,subject_settings,profile,Path(cache_dir),face,
                              subject=subject,participant_index=index,include_body=False)
            results.append(dict(result,participant=index,**_subject_tags(asset,subject_settings,face,kind)))
        status='clear' if all(result['status']=='clear' for result in results) else 'uncertain'
        if status=='clear':reason=f'{len(results)} 项所选主体证据均清楚（人脸与身体可能属于同一人）。'
        else:
            issue=next(result for result in results if result['status']!='clear')
            reason=f"P{issue['participant']}：{issue['reason']}；逐人问题保留，整张待复核。"
        return _bind_result(dict(status=status,reason=reason,source='api',participants=results,
                                 prompt_version=PROMPT_VERSION),asset,settings)
    subject,face,subject_settings,kind=targets[0] if targets else (None,None,settings,None)
    result=_review_one(asset,subject_settings,profile,Path(cache_dir),face,subject=subject)
    if kind=='body':
        tags=_subject_tags(asset,subject_settings,face,kind)
        result=dict(result,**tags,participants=[dict(status=result['status'],reason=result['reason'],participant=1,**tags)])
    return _bind_result(result,asset,settings)



@guard_focus_review
def _review_one(
    asset: Any,
    settings: CropSettings,
    profile: Mapping[str, object],
    root: Path,
    face: tuple[float, float, float, float] | None,
    *,
    subject: Any = None,
    participant_index: int | None = None,
    include_body: bool = True,
) -> dict[str, Any]:
    """Review one selected face; callers aggregate participants conservatively."""
    digest = _cache_digest(asset, face, profile, settings)
    if participant_index is not None:
        digest = hashlib.sha256(f"{digest}:participant:{participant_index}".encode()).hexdigest()
    cache_path = root / PROMPT_VERSION / f"{digest}.json"
    if not getattr(asset, "ai_focus_dirty", False):
        if cached := _cached_result(cache_path):
            return cached

    from .focus_image_cache import cached_focus_images
    image_paths, face_found = cached_focus_images(
        asset, settings, root, face, subject=subject, include_body=include_body,
    )
    photo_identity = (
        f"{asset.stem} · P{participant_index}"
        if participant_index is not None else str(asset.stem)
    )
    body_images = sum(path.name.startswith('body_native_') for path in image_paths)
    body_only=not face_found and any(path.name.startswith('body_region_native') for path in image_paths)
    prompt = _prompt(photo_identity, face_found, body_images, mode=_mode(asset), body_only=body_only)
    if body_only:
        tags=_subject_tags(asset,settings,None,'body')
        if tags.get('associated_face') is not None:
            prompt+=f"\n此人体已通过位置关联到所选人脸 {tags['associated_face']}，属于同一人的另一项证据，不应重复计算人数。"
        elif tags.get('association') in {'ambiguous','unverified'}:
            prompt+='\n现有人脸与此身体的对应关系不确定，只判断框内身体，不推断身份或将其确定为另一位人物。'
    if participant_index is not None:
        prompt += (
            f"\n本次只检查合影成员 P{participant_index}，即后续原生细节图片对应的所选人脸或人体。这是证据编号，不表示不同的人。"
            "总览里的其他人和海报脸不参与本次判断；不要用其他人的清晰程度代替此人的结果。"
        )
    from .focus_audit import record_inputs, record_response
    audit = record_inputs(root, digest, image_paths, prompt, profile, PROMPT_VERSION,
                          source_paths=asset.rating_target_paths)
    try:
        response = call_model(profile, prompt, image_paths)
    except ApiError as exc:
        record_response(root, digest, {"error": str(exc)})
        raise
    if not isinstance(response, Mapping):
        raise ApiError("AI 清晰度审核返回了无法识别的响应。")
    raw_response = response.get("text")
    record_response(root, digest, {"raw_response": raw_response, "usage": response.get("usage", {})})
    parsed = _parse_response(raw_response, photo_identity)
    if body_images and parsed['status']=='clear' and parsed.get('observations',{}).get('body_motion')!='absent':
        parsed.update(status='uncertain',reason='身体复核未提供明确清晰证据：'+parsed['reason'])
    if not face_found and not body_only and _mode(asset) != 'landscape':
        parsed = {"status": "uncertain", "reason": "未检测到可靠人脸，无法可靠判断主体清晰度。"}
    result: dict[str, Any] = {**parsed, "source": "api", "raw_response": raw_response,
                            "audit": audit, "prompt_version": PROMPT_VERSION}
    if isinstance(response.get("usage"), dict):
        result["usage"] = response["usage"]
    _write_cache(cache_path, result)
    return result
