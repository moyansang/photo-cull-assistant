from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from .grouping import GROUPING_VERSION, source_photo_id
from .models import PhotoAsset

STORE_VERSION = 2


def _photo_id(asset: PhotoAsset) -> str:
    return source_photo_id(asset)


def _read_payload(path: str | Path) -> dict | None:
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload.get('version') in (1, STORE_VERSION) else None


def _memberships(payload: dict) -> dict[str, int] | None:
    field = 'groups_by_id' if payload.get('version') == STORE_VERSION else 'groups'
    groups = payload.get(field)
    if not isinstance(groups, dict):
        return None
    membership = {}
    try:
        for raw_group_id, members in groups.items():
            group_id = int(raw_group_id)
            if group_id < 1 or str(group_id) != str(raw_group_id) or not isinstance(members, list) or not members:
                return None
            for member in members:
                if not isinstance(member, str) or not member or member in membership:
                    return None
                membership[member] = group_id
    except (TypeError, ValueError):
        return None
    keys = payload.get('photo_ids' if payload.get('version') == STORE_VERSION else 'photo_stems')
    if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
        return None
    if len(keys) != len(set(keys)) or set(keys) != set(membership):
        return None
    return membership


def manual_group_ids(path: str | Path, collection_key: str | None = None) -> set[int]:
    """Read saved manual group IDs without changing assets or loading images."""
    payload = _read_payload(path)
    if payload is None or payload.get('source') != 'manual':
        return set()
    if collection_key is not None and payload.get('collection_key') != collection_key:
        return set()
    membership = _memberships(payload)
    return set(membership.values()) if membership is not None else set()


def save_groups(assets: list[PhotoAsset], path: str | Path, source: str = 'manual',
                collection_key: str | None = None, *, overwrite_manual: bool = False) -> Path:
    """Persist stable memberships atomically, retaining readable legacy fields.

    An automatic save over a saved manual store is a no-op (returns its path),
    unless an explicit user action authorized overwrite_manual=True. It does not
    raise during normal session publication or silently replace manual work.
    """
    store_path = Path(path)
    previous = _read_payload(store_path)
    if source != 'manual' and not overwrite_manual and previous and previous.get('source') == 'manual':
        return store_path
    groups: dict[str, list[str]] = {}
    by_id: dict[str, list[str]] = {}
    anchor_candidates: dict[str, set[str]] = {}
    ids = [_photo_id(asset) for asset in assets]
    if len(ids) != len(set(ids)):
        raise ValueError('Cannot save grouping for duplicate primary-source identities')
    for asset, photo_id in zip(assets, ids):
        if type(asset.group_id) is not int or asset.group_id < 1:
            raise ValueError('Groups must have positive integer IDs before saving')
        groups.setdefault(str(asset.group_id), []).append(asset.stem)
        by_id.setdefault(str(asset.group_id), []).append(photo_id)
        if asset.grouping_anchor_id:
            anchor_candidates.setdefault(str(asset.group_id), set()).add(asset.grouping_anchor_id)
    anchors = {group_id: next(iter(candidates))
               for group_id, candidates in anchor_candidates.items()
               if source == 'auto' and len(candidates) == 1}
    payload = {
        'version': STORE_VERSION,
        'source': source,
        'saved_at': datetime.now(timezone.utc).isoformat(),
        'collection_key': collection_key,
        'grouping_version': GROUPING_VERSION if source == 'auto' else None,
        'photo_stems': [asset.stem for asset in assets],
        'photo_ids': ids,
        'groups': groups,
        'groups_by_id': by_id,
        'group_anchors': anchors,
    }
    store_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=store_path.parent,
                                prefix=store_path.name + '.', suffix='.tmp', delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, store_path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return store_path


def load_groups(assets: list[PhotoAsset], path: str | Path, collection_key: str | None = None,
                *, allow_partial: bool = False) -> bool:
    """Read v1/v2 stores without regrouping, renumbering or migrating on disk.

    allow_partial only applies to manual stores: surviving photos retain saved
    memberships; additions remain unassigned for a subsequent protected scan.
    The default still rejects changed photo sets as older callers expect.
    """
    payload = _read_payload(path)
    if payload is None:
        return False
    if collection_key is not None and payload.get('collection_key') != collection_key:
        return False
    membership = _memberships(payload)
    if membership is None:
        return False
    keys = [_photo_id(asset) if payload['version'] == STORE_VERSION else asset.stem for asset in assets]
    if len(keys) != len(set(keys)):
        return False
    partial = allow_partial and payload.get('source') == 'manual'
    if partial:
        if keys and not set(keys).intersection(membership):
            return False
    elif set(keys) != set(membership):
        return False
    anchors = payload.get('group_anchors', {})
    if not isinstance(anchors, dict) or any(not isinstance(value, str) for value in anchors.values()):
        return False
    for group_id, anchor_id in anchors.items():
        try:
            saved_group_id = int(group_id)
        except (TypeError, ValueError):
            return False
        if anchor_id in membership and membership[anchor_id] != saved_group_id:
            return False
    # Validate the whole payload first so failures never apply half a grouping.
    for asset, key in zip(assets, keys):
        if key in membership:
            asset.group_id = membership[key]
            asset.grouping_anchor_id = anchors.get(str(asset.group_id))
    return True
