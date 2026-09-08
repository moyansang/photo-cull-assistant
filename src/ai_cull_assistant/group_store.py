from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import PhotoAsset

STORE_VERSION = 1


def save_groups(assets: list[PhotoAsset], path: str | Path, source: str = "manual", collection_key: str | None = None) -> Path:
    store_path = Path(path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[str]] = {}
    for asset in assets:
        groups.setdefault(str(asset.group_id), []).append(asset.stem)
    payload = {
        "version": STORE_VERSION,
        "source": source,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "collection_key": collection_key,
        "photo_stems": [asset.stem for asset in assets],
        "groups": groups,
    }
    store_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return store_path


def load_groups(assets: list[PhotoAsset], path: str | Path, collection_key: str | None = None) -> bool:
    store_path = Path(path)
    if not store_path.exists():
        return False
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if payload.get("version") != STORE_VERSION:
        return False
    saved_collection_key = payload.get("collection_key")
    if collection_key is not None and saved_collection_key != collection_key:
        return False
    saved_stems = payload.get("photo_stems")
    if saved_stems != [asset.stem for asset in assets]:
        return False
    groups = payload.get("groups")
    if not isinstance(groups, dict):
        return False
    stem_to_group: dict[str, int] = {}
    try:
        for group_id, stems in groups.items():
            for stem in stems:
                stem_to_group[str(stem)] = int(group_id)
    except (TypeError, ValueError):
        return False
    if set(stem_to_group) != {asset.stem for asset in assets}:
        return False
    for asset in assets:
        asset.group_id = stem_to_group[asset.stem]
    return True
