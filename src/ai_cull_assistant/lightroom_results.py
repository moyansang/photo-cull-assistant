"""Lightroom catalog interchange. Never writes image metadata or sidecars."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path


# None means no current technical assessment: leave Lightroom's keyword alone.
def focus_review_status(asset):
    if asset.auto_rejected or asset.screening_reason in {'subject_not_obviously_blurred', 'ai_focus_clear', 'ai_focus_blur'}:
        return False
    if asset.screening_reason in {
        'ai_focus_uncertain', 'face_focus_uncertain', 'face_too_small_for_focus', 'no_reliable_face',
        'source_preview_geometry_mismatch', 'source_unreadable_for_focus',
        'preview_unreadable', 'preview_unavailable',
    }:
        return True
    return None


def write_lightroom_results(assets, destination, ratings=None):
    ratings = ratings or {}
    counts = Counter(a.stem for a in assets)
    photos = []
    seen = set()
    for asset in assets:
        rating = ratings.get(asset.stem)
        if rating is not None and counts[asset.stem] != 1:
            raise ValueError(f'文件名重复，无法安全应用评级：{asset.stem}')
        focus_review = focus_review_status(asset)
        if rating is None and not asset.auto_rejected and focus_review is None:
            continue
        fields = {}
        if rating is not None:
            if type(rating) is not int or not 1 <= rating <= 5:
                raise ValueError('星级必须为 1～5')
            fields['rating'] = rating
            if asset.auto_rejected:
                fields['pick_status'] = 0  # Explicit review overrides automatic rejection.
        elif asset.auto_rejected:
            fields.update(pick_status=-1, reason=asset.screening_reason or 'technical_screening')
        if focus_review is not None:
            fields["focus_review"] = focus_review
        for path in asset.rating_target_paths:
            absolute = str(path.resolve())
            key = absolute.casefold()
            if key in seen:
                raise ValueError(f'照片路径重复：{absolute}')
            seen.add(key)
            photos.append(dict(path=absolute, filename=path.name, **fields))
    result = dict(format='photo-cull-assistant', version=1,
                  created_at=datetime.now(timezone.utc).isoformat(), photos=photos)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)
    return target
