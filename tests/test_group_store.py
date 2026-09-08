from datetime import datetime
from pathlib import Path

from ai_cull_assistant.group_store import load_groups, save_groups
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


def test_save_and_load_groups_round_trip(tmp_path):
    path = tmp_path / 'groups.json'
    original = [make_asset('A', 1), make_asset('B', 1), make_asset('C', 2)]
    save_groups(original, path, source='manual')

    loaded = [make_asset('A', 0), make_asset('B', 0), make_asset('C', 0)]
    result = load_groups(loaded, path)

    assert result is True
    assert [a.group_id for a in loaded] == [1, 1, 2]


def test_load_groups_rejects_stale_file_when_photo_set_changed(tmp_path):
    path = tmp_path / 'groups.json'
    save_groups([make_asset('A', 1), make_asset('B', 1)], path, source='manual')

    loaded = [make_asset('A', 0), make_asset('C', 0)]

    assert load_groups(loaded, path) is False
    assert [a.group_id for a in loaded] == [0, 0]


def test_load_groups_rejects_same_filenames_from_different_photo_folder(tmp_path):
    path = tmp_path / 'groups.json'
    original = [make_asset('A', 1), make_asset('B', 1)]
    save_groups(original, path, source='manual', collection_key='C:/shoots/day1')

    loaded = [make_asset('A', 0), make_asset('B', 0)]

    assert load_groups(loaded, path, collection_key='C:/shoots/day2') is False
    assert [a.group_id for a in loaded] == [0, 0]


def test_load_groups_with_collection_key_rejects_legacy_store_without_key(tmp_path):
    path = tmp_path / 'groups.json'
    original = [make_asset('A', 1), make_asset('B', 1)]
    save_groups(original, path, source='manual')

    loaded = [make_asset('A', 0), make_asset('B', 0)]

    assert load_groups(loaded, path, collection_key='C:/shoots/day1') is False
