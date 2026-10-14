from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import random

import numpy as np
from PIL import Image
import pytest

from ai_cull_assistant import grouping
from ai_cull_assistant.grouping import assign_groups
from ai_cull_assistant.models import PhotoAsset


def make_asset(stem: str, seconds: float = 0):
    path = Path(f'/tmp/{stem}.jpg')
    return PhotoAsset(stem, path, path, None, path,
                      datetime(2026, 1, 1) + timedelta(seconds=seconds), '.jpg')


def hash_visuals(monkeypatch, values):
    base = grouping._visual_from_image(Image.new('L', (64, 64), 127))
    monkeypatch.setattr(grouping, 'visual_features', lambda asset: replace(base, whole=values[asset.stem]))


def test_missing_visuals_never_group_on_capture_time():
    assets = [make_asset('missing_A', 0), make_asset('missing_B', .5), make_asset('missing_C', 5)]
    assign_groups(assets)
    assert [asset.group_id for asset in assets] == [1, 2, 3]


@pytest.mark.parametrize('preset', grouping.PRESETS)
def test_all_workspace_photos_compare_across_dates_folders_and_intervening_photos(monkeypatch, preset):
    assets = [make_asset('day1/A', 0), make_asset('day2/B', 1), make_asset('day3/C', 86400 * 365)]
    hash_visuals(monkeypatch, {'day1/A': 0, 'day2/B': 2 ** 64 - 1, 'day3/C': 0})
    original_order = list(assets)
    assign_groups(assets, preset)
    assert [asset.group_id for asset in assets] == [1, 2, 1]
    assert assets == original_order


def test_fixed_representative_prevents_transitive_chaining(monkeypatch):
    assets = [make_asset(name) for name in 'ABC']
    hash_visuals(monkeypatch, {'A': 0, 'B': 0xff, 'C': 0xffff})
    assign_groups(assets)
    assert [asset.group_id for asset in assets] == [1, 1, 2]


def test_shuffle_does_not_change_fresh_group_membership_or_ids(monkeypatch):
    values = {'A': 0, 'B': 0xff, 'C': 0xffff, 'D': 2 ** 64 - 1}
    hash_visuals(monkeypatch, values)
    forward = [make_asset(name) for name in values]
    reverse = [make_asset(name) for name in reversed(values)]
    assign_groups(forward)
    assign_groups(reverse)
    assert {a.stem: a.group_id for a in forward} == {a.stem: a.group_id for a in reverse}


def test_hash_alone_cannot_merge_different_grayscale_structure(tmp_path):
    assets = []
    for stem, color in [('A', 'black'), ('B', 'white')]:
        asset = make_asset(stem)
        asset.preview_path = tmp_path / f'{stem}.png'
        Image.new('RGB', (64, 64), color).save(asset.preview_path)
        assets.append(asset)
    assert grouping.dhash(assets[0].preview_path) == grouping.dhash(assets[1].preview_path)
    assign_groups(assets)
    assert [a.group_id for a in assets] == [1, 2]


def test_identical_images_merge_and_exif_orientation_is_respected(tmp_path):
    pixels = np.random.default_rng(71).integers(0, 256, (64, 96, 3), dtype=np.uint8)
    original = Image.fromarray(pixels)
    assets = [make_asset('A'), make_asset('B')]
    assets[0].preview_path = tmp_path / 'A.png'
    assets[1].preview_path = tmp_path / 'B.png'
    original.save(assets[0].preview_path)
    exif = Image.Exif(); exif[274] = 6
    original.transpose(Image.Transpose.ROTATE_90).save(assets[1].preview_path, exif=exif)
    assign_groups(assets)
    assert [a.group_id for a in assets] == [1, 1]


def test_decode_failure_placeholders_are_never_visual_evidence(tmp_path):
    path = tmp_path / 'placeholder.png'
    Image.new('RGB', (64, 64), 'gray').save(path)
    assets = [make_asset('A'), make_asset('B')]
    for asset in assets:
        asset.preview_path = path
        asset.notes.append('decode_error: unavailable source')
    assign_groups(assets)
    assert [a.group_id for a in assets] == [1, 2]


def test_existing_ids_are_stable_and_additions_join_automatic_group(monkeypatch):
    assets = [make_asset('A'), make_asset('B'), make_asset('C')]
    assets[0].group_id, assets[1].group_id = 7, 42
    hash_visuals(monkeypatch, {'A': 0, 'B': 2 ** 64 - 1, 'C': 0})
    assign_groups(assets)
    assert [a.group_id for a in assets] == [7, 42, 7]


def test_protected_manual_membership_survives_explicit_auto_regroup(monkeypatch):
    assets = [make_asset(name) for name in 'ABCD']
    assets[0].group_id = assets[1].group_id = 7
    assets[2].group_id = 42
    hash_visuals(monkeypatch, {name: 0 for name in 'ABCD'})
    assign_groups(assets, preserve_existing=False, protected_group_ids={7})
    assert [a.group_id for a in assets] == [7, 7, 8, 8]


def test_structural_metric_matches_independent_block_ssim_formula():
    rng = np.random.default_rng(71)
    left = grouping._visual_from_image(Image.fromarray(rng.integers(0, 256, (64, 64), dtype=np.uint8)))
    right = grouping._visual_from_image(Image.fromarray(rng.integers(0, 256, (64, 64), dtype=np.uint8)))
    values = []
    for a, b in zip(left.blocks.astype(float), right.blocks.astype(float)):
        covariance = np.mean((a - a.mean()) * (b - b.mean()))
        values.append(((2*a.mean()*b.mean() + 6.5025) * (2*covariance + 58.5225)) /
                      ((a.mean()**2 + b.mean()**2 + 6.5025) * (a.var() + b.var() + 58.5225)))
    score = grouping.grayscale_structure_similarity(left, right)
    assert score == pytest.approx(np.mean(values), abs=1e-5)
    assert grouping._structure_upper_bound(left, right) >= score
    assert grouping.grayscale_structure_similarity(left, left) == pytest.approx(1, abs=1e-5)


def test_hash_index_matches_brute_force_radius_including_hash_collisions():
    rng = random.Random(57)
    values = [rng.getrandbits(64) for _ in range(300)] + [0, 0, 0xffff]
    index = grouping._HashIndex()
    for position, value in enumerate(values):
        index.add(value, position)
    for query in values[::13] + [0xff]:
        actual = index.find(query, 10)
        expected = sorted(((query ^ value).bit_count(), position) for position, value in enumerate(values)
                          if (query ^ value).bit_count() <= 10)
        assert actual == expected


def test_thousands_use_candidate_filter_not_all_pairs_grayscale(monkeypatch):
    rng = random.Random(17)
    values = {f'P{index:05}': rng.getrandbits(64) for index in range(2500)}
    assets = [make_asset(name) for name in values]
    hash_visuals(monkeypatch, values)
    stats = grouping.GroupingStats()
    assign_groups(assets, stats=stats)
    assert stats.photos == 2500
    assert stats.structure_comparisons < 100
    assert stats.hash_candidates < 100
    assert len({asset.group_id for asset in assets}) > 2400


def test_long_identical_burst_compares_only_to_one_representative(monkeypatch):
    assets = [make_asset(f'P{index:05}') for index in range(2500)]
    hash_visuals(monkeypatch, {asset.stem: 0 for asset in assets})
    stats = grouping.GroupingStats()
    assign_groups(assets, stats=stats)
    assert stats.representatives == 1
    assert stats.structure_comparisons == 2499
    assert {asset.group_id for asset in assets} == {1}


def test_representative_cannot_drift_between_incremental_imports(monkeypatch):
    hash_visuals(monkeypatch, {'A': 0xff, 'B': 0, 'C': 0xffff})
    assets = [make_asset('B')]
    assign_groups(assets)
    original_anchor = assets[0].grouping_anchor_id
    assets.append(make_asset('A'))  # sorts before the original B representative
    assign_groups(assets)
    assert [a.group_id for a in assets] == [1, 1]
    assert {a.grouping_anchor_id for a in assets} == {original_anchor}
    assets.append(make_asset('C'))
    assign_groups(assets)
    assert [a.group_id for a in assets] == [1, 1, 2]


def test_removed_representative_does_not_silently_move(monkeypatch):
    hash_visuals(monkeypatch, {'A': 0, 'B': 0xff, 'C': 0xffff})
    assets = [make_asset('A'), make_asset('B')]
    assign_groups(assets)
    remaining = [assets[1], make_asset('C')]
    assign_groups(remaining)
    assert [a.group_id for a in remaining] == [1, 2]
