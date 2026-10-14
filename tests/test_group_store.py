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


def test_version_two_uses_source_identity_for_duplicate_stems(tmp_path):
    import json
    original = [make_asset('A', 7), make_asset('A', 42)]
    original[1].primary_path = Path('/tmp/other-folder/A.jpg')
    path = tmp_path / 'groups.json'
    save_groups(original, path)
    payload = json.loads(path.read_text())
    assert payload['version'] == 2
    assert len(set(payload['photo_ids'])) == 2
    loaded = [make_asset('A', 0), make_asset('A', 0)]
    loaded[1].primary_path = original[1].primary_path
    assert load_groups(list(reversed(loaded)), path)
    assert [a.group_id for a in loaded] == [7, 42]


def test_source_relocation_mapping_preserves_group_identity(tmp_path):
    original = make_asset('A', 7)
    path = tmp_path / 'groups.json'
    save_groups([original], path)
    loaded = make_asset('A-renamed-display', 0)
    loaded.primary_path = Path('/tmp/relocated/A.jpg')
    loaded.source_identity_paths[str(loaded.primary_path.resolve()).casefold()] = str(original.primary_path.resolve())
    assert load_groups([loaded], path)
    assert loaded.group_id == 7


def test_manual_store_is_not_overwritten_by_automatic_save(tmp_path):
    from ai_cull_assistant.group_store import manual_group_ids
    path = tmp_path / 'groups.json'
    save_groups([make_asset('A', 7), make_asset('B', 42)], path, collection_key='collection')
    before = path.read_bytes()
    assert save_groups([make_asset('A', 1), make_asset('B', 1)], path, source='auto') == path
    assert path.read_bytes() == before
    assert manual_group_ids(path, 'collection') == {7, 42}
    assert manual_group_ids(path, 'another-collection') == set()


def test_partial_manual_load_retains_survivors_without_assigning_additions(tmp_path):
    path = tmp_path / 'groups.json'
    save_groups([make_asset('A', 7), make_asset('B', 42)], path)
    loaded = [make_asset('A', 0), make_asset('C', 0)]
    assert load_groups(loaded, path, allow_partial=True)
    assert [a.group_id for a in loaded] == [7, 0]


def test_partial_automatic_store_is_not_treated_as_manual(tmp_path):
    path = tmp_path / 'groups.json'
    save_groups([make_asset('A', 7), make_asset('B', 42)], path, source='auto')
    loaded = [make_asset('A', 0), make_asset('C', 0)]
    assert not load_groups(loaded, path, allow_partial=True)
    assert [a.group_id for a in loaded] == [0, 0]


def test_legacy_v1_manual_store_loads_without_rewriting(tmp_path):
    import json
    path = tmp_path / 'groups.json'
    payload = {'version': 1, 'source': 'manual', 'photo_stems': ['A', 'B'],
               'groups': {'7': ['A'], '42': ['B']}}
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    loaded = [make_asset('B', 0), make_asset('A', 0)]
    assert load_groups(loaded, path)
    assert [a.group_id for a in loaded] == [42, 7]
    assert path.read_bytes() == before


def test_malformed_duplicate_memberships_do_not_partially_modify_assets(tmp_path):
    import json
    path = tmp_path / 'groups.json'
    path.write_text(json.dumps({'version': 1, 'source': 'manual', 'photo_stems': ['A', 'B'],
                                'groups': {'7': ['A', 'B'], '42': ['B']}}))
    loaded = [make_asset('A', 1), make_asset('B', 2)]
    assert not load_groups(loaded, path)
    assert [a.group_id for a in loaded] == [1, 2]


def test_automatic_store_records_grouping_algorithm_version(tmp_path):
    import json
    from ai_cull_assistant.grouping import GROUPING_VERSION
    path = tmp_path / 'groups.json'
    save_groups([make_asset('A', 7)], path, source='auto')
    assert json.loads(path.read_text())['grouping_version'] == GROUPING_VERSION


def test_automatic_representative_identity_round_trips(tmp_path):
    from ai_cull_assistant.grouping import source_photo_id
    path = tmp_path / 'groups.json'
    original = [make_asset('B', 7), make_asset('A', 7)]
    for asset in original:
        asset.grouping_anchor_id = source_photo_id(original[0])
    save_groups(original, path, source='auto')
    loaded = [make_asset('A', 0), make_asset('B', 0)]
    assert load_groups(loaded, path)
    assert {a.grouping_anchor_id for a in loaded} == {source_photo_id(original[0])}
