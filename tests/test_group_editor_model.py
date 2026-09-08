from datetime import datetime
from pathlib import Path

from ai_cull_assistant.group_editor import grouped_assets
from ai_cull_assistant.models import PhotoAsset


def make_asset(stem: str, group_id: int):
    path = Path(f'/tmp/{stem}.jpg')
    return PhotoAsset(
        stem=stem,
        display_path=path,
        primary_path=path,
        raw_path=None,
        jpg_path=path,
        captured_at=datetime(2026, 1, 1),
        ext='.jpg',
        group_id=group_id,
    )


def test_grouped_assets_returns_groups_in_photo_order():
    assets = [make_asset('A', 1), make_asset('B', 1), make_asset('C', 2)]

    groups = grouped_assets(assets)

    assert [(gid, [a.stem for a in members]) for gid, members in groups] == [
        (1, ['A', 'B']),
        (2, ['C']),
    ]
