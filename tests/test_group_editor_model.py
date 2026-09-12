from datetime import datetime
from pathlib import Path

from ai_cull_assistant.group_editor import grouped_assets, visible_group_range
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


def test_large_group_editor_only_renders_rows_near_viewport():
    indexes = visible_group_range(450, viewport_top=145 * 220, viewport_height=820, row_height=145)

    assert 218 <= indexes.start <= 220
    assert indexes.stop - indexes.start < 10
    assert 220 in indexes
