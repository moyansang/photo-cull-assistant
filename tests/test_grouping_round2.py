"""Round-two workspace grouping boundaries; synthetic data is not an accuracy set."""
from dataclasses import replace
from datetime import datetime, timedelta
from itertools import permutations
import os
from pathlib import Path
import random

import numpy as np
from PIL import Image
import pytest

from ai_cull_assistant import grouping
from ai_cull_assistant.group_store import load_groups, manual_group_ids, save_groups
from ai_cull_assistant.models import PhotoAsset


def asset(name, group_id=0):
    path = Path('/round2-synthetic') / f'{name}.bmp'
    return PhotoAsset(name, path, path, None, path, datetime(2026, 1, 1), '.bmp',
                      group_id=group_id)


def hash_visuals(monkeypatch, values):
    base = grouping._visual_from_image(Image.new('L', (64, 64), 127))
    monkeypatch.setattr(grouping, 'visual_features',
                        lambda item: replace(base, whole=values[item.stem]))


def collision_visuals(count, seed=983):
    """Actual equal-dHash images, equal block moments, different row structure."""
    rng = np.random.default_rng(seed)
    result = {}
    for index in range(count):
        rows = np.concatenate([rng.permutation([0] * 4 + [255] * 4)
                               for _ in range(8)]).astype(np.uint8)
        pixels = np.tile(rows[:, None], (1, 64))
        result[f'P{index:04}'] = grouping._visual_from_image(Image.fromarray(pixels))
    return result


@pytest.mark.parametrize('preset', grouping.PRESETS)
def test_protected_absent_group_ids_cannot_be_reused_by_additions(monkeypatch, preset):
    hash_visuals(monkeypatch, {'A': 0, 'C': 0})
    items = [asset('A', 1), asset('C')]
    grouping.assign_groups(items, preset, protected_group_ids={1, 2, 42})
    assert [item.group_id for item in items] == [1, 43]
    assert items[1].group_id not in {1, 2, 42}


def test_partial_manual_restore_keeps_absent_ids_reserved_until_photo_returns(tmp_path, monkeypatch):
    hash_visuals(monkeypatch, {name: 0 for name in 'ABC'})
    path = tmp_path / 'groups.json'
    save_groups([asset('A', 1), asset('B', 2)], path, collection_key='workspace')
    original_store = path.read_bytes()
    survivors = [asset('A'), asset('C')]
    assert load_groups(survivors, path, 'workspace', allow_partial=True)
    protected = manual_group_ids(path, 'workspace')
    grouping.assign_groups(survivors, protected_group_ids=protected)
    assert [item.group_id for item in survivors] == [1, 3]
    save_groups(survivors, path, source='auto', collection_key='workspace')
    assert path.read_bytes() == original_store
    survivors.append(asset('B'))
    assert load_groups(survivors, path, 'workspace', allow_partial=True)
    grouping.assign_groups(survivors, protected_group_ids=protected)
    assert {item.stem: item.group_id for item in survivors} == {'A': 1, 'B': 2, 'C': 3}


@pytest.mark.parametrize('replacement', ['in_place', 'atomic_replace'])
def test_preview_cache_sees_same_size_same_mtime_rewrites(tmp_path, replacement):
    item = asset('preview')
    item.preview_path = tmp_path / 'preview.bmp'
    Image.new('L', (64, 64), 0).save(item.preview_path)
    original_stat = item.preview_path.stat()
    old = grouping.visual_features(item)
    assert old.means.mean() == 0
    target = item.preview_path if replacement == 'in_place' else tmp_path / 'new.bmp'
    Image.new('L', (64, 64), 255).save(target)
    os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    if replacement == 'atomic_replace':
        os.replace(target, item.preview_path)
    current_stat = item.preview_path.stat()
    assert current_stat.st_size == original_stat.st_size
    assert current_stat.st_mtime_ns == original_stat.st_mtime_ns
    new = grouping.visual_features(item)
    assert new.means.mean() == 255
    assert new is not old
    assert grouping.visual_features(item) is new
    assert not new.blocks.flags.writeable


def test_explicit_regroup_uses_rewritten_preview_evidence(tmp_path):
    items = [asset('A'), asset('B')]
    for item in items:
        item.preview_path = tmp_path / f'{item.stem}.bmp'
        Image.new('L', (64, 64), 0).save(item.preview_path)
    grouping.assign_groups(items)
    assert [item.group_id for item in items] == [1, 1]
    old_stat = items[1].preview_path.stat()
    Image.new('L', (64, 64), 255).save(items[1].preview_path)
    os.utime(items[1].preview_path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    grouping.assign_groups(items, preserve_existing=False)
    assert [item.group_id for item in items] == [1, 2]


@pytest.mark.parametrize('preset', grouping.PRESETS)
def test_collision_fixture_keeps_different_structure_separate(monkeypatch, preset):
    visuals = collision_visuals(128)
    assert {value.whole for value in visuals.values()} == {0}
    reference = next(iter(visuals.values()))
    assert all(np.array_equal(value.means, reference.means) and
               np.array_equal(value.variances, reference.variances)
               for value in visuals.values())
    monkeypatch.setattr(grouping, 'visual_features', lambda item: visuals[item.stem])
    items = [asset(name) for name in visuals]
    stats = grouping.GroupingStats()
    grouping.assign_groups(items, preset, stats=stats)
    assert len({item.group_id for item in items}) == len(items)
    assert stats.hash_candidates == stats.structure_comparisons == 128 * 127 // 2
    assert stats.representatives == 128


def test_collision_stress_is_order_invariant_without_time_cutoffs(monkeypatch):
    visuals = collision_visuals(256)
    monkeypatch.setattr(grouping, 'visual_features', lambda item: visuals[item.stem])
    expected = {}
    for seed in (41, 92):
        items = [asset(name) for name in visuals]
        random.Random(seed).shuffle(items)
        for index, item in enumerate(items):
            item.captured_at += timedelta(days=index * 113)
        original_order = list(items)
        stats = grouping.GroupingStats()
        grouping.assign_groups(items, stats=stats)
        memberships = {item.stem: (item.group_id, item.grouping_anchor_id) for item in items}
        if expected:
            assert memberships == expected
        expected = memberships
        assert items == original_order
        assert stats.hash_candidates == stats.structure_comparisons == 32640
        assert stats.representatives == 256


def test_saved_anchor_survives_every_input_order_and_earlier_addition(tmp_path, monkeypatch):
    hash_visuals(monkeypatch, {'A': 0xff, 'B': 0, 'C': 0xffff, 'D': (1 << 64) - 1})
    saved = [asset('B')]
    grouping.assign_groups(saved)
    anchor = saved[0].grouping_anchor_id
    path = tmp_path / 'groups.json'
    save_groups(saved, path, source='auto')
    for names in permutations('ABC'):
        items = [asset('B')]
        assert load_groups(items, path)
        items.extend(asset(name) for name in names if name != 'B')
        items.sort(key=lambda item: names.index(item.stem))
        grouping.assign_groups(items)
        assert {item.stem: item.group_id for item in items} == {'A': 1, 'B': 1, 'C': 2}
        assert {item.grouping_anchor_id for item in items if item.group_id == 1} == {anchor}


def test_removed_anchor_stays_absent_across_save_load(tmp_path, monkeypatch):
    hash_visuals(monkeypatch, {'A': 0, 'B': 0xff, 'C': 0xffff})
    items = [asset('A'), asset('B')]
    grouping.assign_groups(items)
    original_anchor = items[0].grouping_anchor_id
    path = tmp_path / 'groups.json'
    save_groups([items[1]], path, source='auto')
    restored = [asset('B')]
    assert load_groups(restored, path)
    assert restored[0].grouping_anchor_id == original_anchor
    restored.append(asset('C'))
    grouping.assign_groups(restored)
    assert [item.group_id for item in restored] == [1, 2]
    assert restored[0].grouping_anchor_id == original_anchor


def test_decoding_failure_does_not_reuse_previously_cached_valid_preview(tmp_path):
    item = asset('A')
    item.preview_path = tmp_path / 'valid.bmp'
    Image.new('L', (64, 64), 127).save(item.preview_path)
    assert grouping.visual_features(item) is not None
    item.notes.append('decode_error: replacement failed')
    assert grouping.visual_features(item) is None


@pytest.mark.parametrize('preset', grouping.PRESETS)
def test_batch_matches_scalar_for_random_images_and_candidate_priority(preset):
    rng = np.random.default_rng(5121)
    base = rng.integers(0, 256, (64, 64), dtype=np.uint8)
    visuals = []
    for index in range(96):
        other = rng.integers(0, 256, (64, 64), dtype=np.uint8)
        fraction = (index % 12) / 12
        pixels = np.rint(base * fraction + other * (1 - fraction)).astype(np.uint8)
        value = grouping._visual_from_image(Image.fromarray(pixels))
        visuals.append(replace(value, whole=index % 32, aspect=1 + .02 * (index % 8)))
    cfg = grouping.PRESETS[preset]
    representatives = [(asset(f'R{index:04}'), value, index + 1)
                       for index, value in enumerate(visuals)]
    for value in visuals[::7]:
        candidates = sorted(((left.whole ^ value.whole).bit_count(), index)
                            for index, left in enumerate(visuals)
                            if (left.whole ^ value.whole).bit_count() <= cfg.hash_distance_threshold)
        query = asset('query')
        expected = next((index for _, index in candidates
                         if grouping._similar(representatives[index][0], query,
                                              visuals[index], value, cfg, grouping.GroupingStats())), None)
        actual = grouping._first_matching_representative(
            query, value, candidates, representatives, cfg, grouping.GroupingStats())
        assert actual == expected


def test_batch_preserves_subject_gates_across_chunk_boundaries():
    from ai_cull_assistant.subject import SubjectFeatures

    value = grouping._visual_from_image(Image.new('L', (64, 64), 127))
    query = asset('query')
    query.subject_features = SubjectFeatures('0', '0', '0', (.4, .1, .1, .1))
    representatives = []
    for index in range(150):
        anchor = asset(f'R{index:04}')
        anchor.subject_features = SubjectFeatures('0', '0', 'ffffffffffffffff', (.4, .1, .1, .1))
        if index >= 79:
            anchor.subject_features = query.subject_features
        representatives.append((anchor, value, index + 1))
    candidates = [(0, index) for index in range(len(representatives))]
    assert grouping._first_matching_representative(
        query, value, candidates, representatives, grouping.PRESETS['standard'],
        grouping.GroupingStats()) == 79


def test_batch_decisions_equal_scalar_within_float_rounding_distance_of_threshold():
    rng = np.random.default_rng(319)
    pixels = rng.integers(0, 256, (64, 64), dtype=np.uint8)
    right = grouping._visual_from_image(Image.fromarray(pixels))
    left = []
    for ratio in np.linspace(.35, .9, 17):
        other = rng.integers(0, 256, pixels.shape, dtype=np.uint8)
        combined = np.rint(ratio * pixels + (1 - ratio) * other).astype(np.uint8)
        left.append(grouping._visual_from_image(Image.fromarray(combined)))
    scores = [grouping.grayscale_structure_similarity(value, right) for value in left]
    upper = [grouping._structure_upper_bound(value, right) for value in left]
    for boundary in scores + upper:
        for delta in (-2e-5, -1e-7, -1e-9, 0, 1e-9, 1e-7, 2e-5):
            threshold = boundary + 1e-6 + delta
            expected = [(bound + 1e-6 >= threshold and score + 1e-6 >= threshold)
                        for bound, score in zip(upper, scores)]
            actual = grouping._batch_structure_matches(left, right, threshold, grouping.GroupingStats())
            assert actual.tolist() == expected


def test_scalar_and_batched_assignment_have_same_membership_and_anchors(monkeypatch):
    visuals = collision_visuals(160)
    # Exact copies join representatives well beyond the first batch.
    visuals.update({f'Z{index:04}': value for index, value in enumerate(list(visuals.values())[::7])})
    monkeypatch.setattr(grouping, 'visual_features', lambda item: visuals[item.stem])
    scalar_items = [asset(name) for name in visuals]
    batch_items = [asset(name) for name in reversed(visuals)]
    grouping.assign_groups(batch_items)

    def scalar_first(item, visual, candidates, representatives, cfg, stats):
        for _, position in candidates:
            anchor, anchor_visual, _ = representatives[position]
            if grouping._similar(anchor, item, anchor_visual, visual, cfg, stats):
                return position
        return None

    monkeypatch.setattr(grouping, '_first_matching_representative', scalar_first)
    grouping.assign_groups(scalar_items)
    assert {item.stem: (item.group_id, item.grouping_anchor_id) for item in scalar_items} == {
        item.stem: (item.group_id, item.grouping_anchor_id) for item in batch_items}


@pytest.mark.parametrize('change_stage', ['hash', 'decode'])
def test_preview_changed_during_read_is_not_cached(tmp_path, monkeypatch, change_stage):
    item = asset('changing')
    item.preview_path = tmp_path / 'changing.bmp'
    Image.new('L', (64, 64), 18).save(item.preview_path)
    if change_stage == 'hash':
        original = grouping.hashlib.file_digest

        def change_during_hash(stream, *args, **kwargs):
            digest = original(stream, *args, **kwargs)
            Image.new('L', (128, 64), 239).save(item.preview_path)
            return digest

        monkeypatch.setattr(grouping.hashlib, 'file_digest', change_during_hash)
    else:
        original = grouping._visual_from_image

        def change_during_decode(image):
            result = original(image)
            Image.new('L', (128, 64), 239).save(item.preview_path)
            return result

        monkeypatch.setattr(grouping, '_visual_from_image', change_during_decode)
    assert grouping.visual_features(item) is None
    monkeypatch.undo()
    current = grouping.visual_features(item)
    assert current.means.mean() == 239
    assert current.aspect == 2


@pytest.mark.parametrize("deny_open_replace", [False, True])
def test_atomic_replacement_during_hash_uses_consistent_open_file_snapshot(tmp_path, monkeypatch, deny_open_replace):
    item = asset('atomic')
    item.preview_path = tmp_path / 'preview.bmp'
    Image.new('L', (64, 64), 19).save(item.preview_path)
    replacement = tmp_path / 'replacement.bmp'
    Image.new('L', (64, 64), 237).save(replacement)
    original = grouping.hashlib.file_digest
    old_bytes = item.preview_path.read_bytes()
    new_bytes = replacement.read_bytes()
    replaced = []

    def replace_during_hash(stream, *args, **kwargs):
        digest = original(stream, *args, **kwargs)
        try:
            if deny_open_replace:
                raise PermissionError('Simulated Windows open-handle replacement denial')
            os.replace(replacement, item.preview_path)
        except PermissionError:
            replaced.append(False)
            raise
        replaced.append(True)
        return digest

    monkeypatch.setattr(grouping.hashlib, 'file_digest', replace_during_hash)
    initial = grouping.visual_features(item)
    # POSIX replacement may also change the old inode ctime: conservative
    # no-evidence is valid; using new pixels under the old digest is not.
    assert initial is None or initial.means.mean() == 19
    monkeypatch.undo()
    assert len(replaced) == 1
    if not replaced[0]:
        # Windows can deny replacing a file with an open read handle. The
        # original bytes must still produce the original features, not 237.
        assert item.preview_path.read_bytes() == old_bytes
        assert replacement.exists()
        assert grouping.visual_features(item).means.mean() == 19
        os.replace(replacement, item.preview_path)  # The reader is now closed.
    assert item.preview_path.read_bytes() == new_bytes
    assert grouping.visual_features(item).means.mean() == 237


def test_batch_scratch_work_stays_bounded_for_large_collision_candidate_set(monkeypatch):
    black = grouping._visual_from_image(Image.new('L', (64, 64), 0))
    white = grouping._visual_from_image(Image.new('L', (64, 64), 255))
    representatives = [(asset(f'R{index:04}'), black, index + 1) for index in range(1024)]
    sizes = []
    original = grouping._batch_structure_matches

    def record_size(left, *args, **kwargs):
        sizes.append(len(left))
        return original(left, *args, **kwargs)

    monkeypatch.setattr(grouping, '_batch_structure_matches', record_size)
    result = grouping._first_matching_representative(
        asset('query'), white, [(0, index) for index in range(1024)], representatives,
        grouping.PRESETS['standard'], grouping.GroupingStats())
    assert result is None
    assert sizes == [64] * 16


def test_content_feature_cache_is_bounded_and_reuses_recent_entries(tmp_path, monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(grouping, '_visual_cache', OrderedDict())
    monkeypatch.setattr(grouping, '_VISUAL_CACHE_SIZE', 4)
    items = [asset(f'C{index}') for index in range(5)]
    cached = []
    for index, item in enumerate(items):
        item.preview_path = tmp_path / f'{index}.bmp'
        Image.new('L', (64, 64), index * 40).save(item.preview_path)
        cached.append(grouping.visual_features(item))
    assert len(grouping._visual_cache) == 4
    assert all(isinstance(key, bytes) and len(key) == 32 for key in grouping._visual_cache)
    assert all(isinstance(value, grouping.VisualFeatures) for value in grouping._visual_cache.values())
    assert grouping.visual_features(items[-1]) is cached[-1]
    assert grouping.visual_features(items[0]) is not cached[0]
    assert len(grouping._visual_cache) == 4


def test_unprepared_raw_source_is_never_opened_for_grouping(tmp_path, monkeypatch):
    item = asset('raw')
    raw = tmp_path / 'source.DNG'
    # A TIFF container is readable by Pillow even under a RAW suffix. Grouping
    # must not use that as permission to decode the original implicitly.
    Image.new('L', (64, 64), 201).save(raw, format='TIFF')
    item.primary_path = item.display_path = item.raw_path = raw
    item.jpg_path = None
    item.ext = '.dng'
    opened = []
    original = grouping._cached_visual

    def record_path(path):
        opened.append(path)
        return original(path)

    monkeypatch.setattr(grouping, '_cached_visual', record_path)
    assert grouping.visual_features(item) is None
    assert opened == []


def test_raw_asset_uses_its_prepared_preview_without_opening_original(tmp_path, monkeypatch):
    item = asset('raw')
    raw = tmp_path / 'source.dng'
    item.primary_path = item.display_path = item.raw_path = raw
    item.jpg_path = None
    item.ext = '.dng'
    item.preview_path = tmp_path / 'prepared.png'
    Image.new('L', (96, 64), 172).save(item.preview_path)
    opened = []
    original = grouping._cached_visual

    def record_path(path):
        opened.append(path)
        return original(path)

    monkeypatch.setattr(grouping, '_cached_visual', record_path)
    visual = grouping.visual_features(item)
    assert visual.means.mean() == 172
    assert visual.aspect == 1.5
    assert opened == [item.preview_path]


@pytest.mark.parametrize('change_stage', ['hash', 'decode', 'cache_hit'])
def test_same_stat_overwrite_cannot_return_or_cache_stale_snapshot(tmp_path, monkeypatch, change_stage):
    """Windows can preserve every fstat field during an equal-size overwrite."""
    from collections import OrderedDict

    monkeypatch.setattr(grouping, '_visual_cache', OrderedDict())
    item = asset('frozen_stat')
    item.preview_path = tmp_path / 'frozen_stat.bmp'
    Image.new('L', (64, 64), 19).save(item.preview_path)
    original_stat = item.preview_path.stat()
    with item.preview_path.open('rb') as stream:
        version = grouping._file_version(stream)
    monkeypatch.setattr(grouping, '_file_version', lambda stream: version)
    if change_stage == 'cache_hit':
        assert grouping.visual_features(item).means.mean() == 19

    def overwrite():
        Image.new('L', (64, 64), 237).save(item.preview_path)
        os.utime(item.preview_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        assert item.preview_path.stat().st_size == original_stat.st_size
        assert item.preview_path.stat().st_mtime_ns == original_stat.st_mtime_ns

    if change_stage in ('hash', 'cache_hit'):
        original = grouping.hashlib.file_digest

        def change_during_hash(stream, *args, **kwargs):
            result = original(stream, *args, **kwargs)
            overwrite()
            return result

        monkeypatch.setattr(grouping.hashlib, 'file_digest', change_during_hash)
    else:
        original = grouping._visual_from_image

        def change_during_decode(image):
            result = original(image)
            overwrite()
            return result

        monkeypatch.setattr(grouping, '_visual_from_image', change_during_decode)

    # Detect the race even when metadata supplies no change signal. A cached
    # old visual is not valid evidence for the overwritten file on this call.
    assert grouping.visual_features(item) is None
    if change_stage != 'cache_hit':
        assert not grouping._visual_cache
    if change_stage in ('hash', 'cache_hit'):
        monkeypatch.setattr(grouping.hashlib, 'file_digest', original)
    else:
        monkeypatch.setattr(grouping, '_visual_from_image', original)
    current = grouping.visual_features(item)
    assert current.means.mean() == 237
    assert grouping.visual_features(item) is current
