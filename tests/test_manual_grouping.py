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
        ('B', 3),
        ('C', 3),
        ('D', 2),
    ]


def test_split_group_at_first_photo_is_noop():
    assets = [make_asset('A', 1, 0), make_asset('B', 1, 1)]

    changed = split_group_at(assets, 'A')

    assert changed is False
    assert [a.group_id for a in assets] == [1, 1]


def test_merge_adjacent_groups_preserves_unrelated_group_ids():
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
        ('D', 3),
    ]


def test_merge_non_adjacent_groups_is_noop():
    assets = [make_asset('A', 1, 0), make_asset('B', 2, 1), make_asset('C', 3, 2)]

    changed = merge_adjacent_groups(assets, 1, 3)

    assert changed is False
    assert [a.group_id for a in assets] == [1, 2, 3]


def test_split_noncontiguous_group_preserves_unrelated_ids_and_memberships():
    assets = [make_asset('A', 7, 0), make_asset('B', 42, 1),
              make_asset('C', 7, 2), make_asset('D', 99, 3), make_asset('E', 7, 4)]
    assert split_group_at(assets, 'C')
    assert [a.group_id for a in assets] == [7, 42, 100, 99, 100]


def test_neighboring_display_groups_can_have_sparse_ids():
    assets = [make_asset('A', 7, 0), make_asset('B', 100, 1),
              make_asset('C', 42, 2), make_asset('D', 7, 3)]
    assert merge_adjacent_groups(assets, 100, 42)
    assert [a.group_id for a in assets] == [7, 100, 100, 7]
