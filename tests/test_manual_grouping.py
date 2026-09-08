from datetime import datetime, timedelta
from pathlib import Path

from ai_cull_assistant.grouping import merge_adjacent_groups, split_group_at, renumber_groups
from ai_cull_assistant.models import PhotoAsset


def make_asset(stem: str, group_id: int, seconds: float):
    path = Path(f'/tmp/{stem}.jpg')
    return PhotoAsset(
        stem=stem,
        display_path=path,
        primary_path=path,
        raw_path=None,
        jpg_path=path,
        captured_at=datetime(2026, 1, 1, 12, 0, 0) + timedelta(seconds=seconds),
        ext='.jpg',
        group_id=group_id,
    )


def test_split_group_at_moves_selected_photo_and_following_photos_to_new_group():
    assets = [
        make_asset('A', 1, 0),
        make_asset('B', 1, 1),
        make_asset('C', 1, 2),
        make_asset('D', 2, 10),
    ]

    changed = split_group_at(assets, 'B')

    assert changed is True
    assert [(a.stem, a.group_id) for a in assets] == [
        ('A', 1),
        ('B', 2),
        ('C', 2),
        ('D', 3),
    ]


def test_split_group_at_first_photo_is_noop():
    assets = [make_asset('A', 1, 0), make_asset('B', 1, 1)]

    changed = split_group_at(assets, 'A')

    assert changed is False
    assert [a.group_id for a in assets] == [1, 1]


def test_merge_adjacent_groups_merges_and_renumbers_following_groups():
    assets = [
        make_asset('A', 1, 0),
        make_asset('B', 2, 1),
        make_asset('C', 2, 2),
        make_asset('D', 3, 3),
    ]

    changed = merge_adjacent_groups(assets, 1, 2)

    assert changed is True
    assert [(a.stem, a.group_id) for a in assets] == [
        ('A', 1),
        ('B', 1),
        ('C', 1),
        ('D', 2),
    ]


def test_merge_non_adjacent_groups_is_noop():
    assets = [make_asset('A', 1, 0), make_asset('B', 2, 1), make_asset('C', 3, 2)]

    changed = merge_adjacent_groups(assets, 1, 3)

    assert changed is False
    assert [a.group_id for a in assets] == [1, 2, 3]
