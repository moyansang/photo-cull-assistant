"""Lightroom catalog interchange. Never writes image metadata or sidecars."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path


def write_lightroom_results(assets, destination, ratings=None):
    ratings = ratings or {}
    counts = Counter(a.stem for a in assets)
    photos = []
    seen = set()
    for asset in assets:
        rating = ratings.get(asset.stem)
        if rating is not None and counts[asset.stem] != 1:
            raise ValueError(f'文件名重复，无法安全应用评级：{asset.stem}')
        if rating is None and not asset.auto_rejected:
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
