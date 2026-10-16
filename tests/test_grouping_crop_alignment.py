# Historical build15 engine regression; new portrait policy is tested in test_scene_burst_grouping.py.
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from ai_cull_assistant import grouping as g
from ai_cull_assistant.models import PhotoAsset


def scene(seed=123):
    # Reproducible structured texture, not a human-labeled scene.
    pixels = np.random.default_rng(seed).integers(0, 256, (96, 128, 3), dtype=np.uint8)
    return Image.fromarray(pixels).resize((512, 384), Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(.5))


def crop(image, fraction=.05):
    return image.crop((round(image.width*fraction), round(image.height*fraction),
                       round(image.width*(1-fraction)), round(image.height*(1-fraction))))


def asset(tmp_path, name, image):
    p=tmp_path/(name+'.png');image.save(p)
    return PhotoAsset(name,p,p,None,p,datetime(2000,1,1)+timedelta(days=len(name)*800),'.png',preview_path=p,analysis_mode='landscape')


@pytest.mark.parametrize('reverse_source',[False,True])
@pytest.mark.parametrize('preset',list(g.PRESETS))
def test_crop_fallback_is_symmetric_and_keeps_fixed_representative(tmp_path, reverse_source, preset):
    original=scene();trimmed=crop(original)
    a=asset(tmp_path,'A',trimmed if reverse_source else original)
    b=asset(tmp_path,'B',original if reverse_source else trimmed)
    left,right=g.visual_features(a),g.visual_features(b)
    assert not g._similar(a,b,left,right,g.PRESETS[preset],g.GroupingStats())
    stats=g.GroupingStats();g._assign_groups_build15_compat([b,a],preset,stats=stats)
    assert a.group_id==b.group_id
    assert a.grouping_anchor_id==b.grouping_anchor_id==g.source_photo_id(a)
    assert stats.crop_matches==1


def test_connected_policy_chains_successive_crop_edges(tmp_path):
    original=scene();trimmed=crop(original)
    a=asset(tmp_path,'A',original);b=asset(tmp_path,'B',trimmed);c=asset(tmp_path,'C',crop(trimmed))
    g._assign_groups_build15_compat([c,b,a])
    assert a.group_id==b.group_id==c.group_id
    assert b.grouping_anchor_id==g.source_photo_id(a)
    # The preserved legacy policy still rejects the transitive second crop.
    for item in (a,b,c):item.group_id=0;item.grouping_anchor_id=None
    g._assign_groups_fixed_legacy([c,b,a])
    assert a.group_id==b.group_id!=c.group_id


def test_incremental_crop_addition_respects_manual_protection(tmp_path):
    original=scene();a=asset(tmp_path,'A',original);a.group_id=17
    a.grouping_anchor_id=g.source_photo_id(a);b=asset(tmp_path,'B',crop(original))
    g._assign_groups_build15_compat([a,b],protected_group_ids={17})
    assert a.group_id==17 and b.group_id!=17
    b.group_id=0;g._assign_groups_build15_compat([a,b])
    assert b.group_id==17 and b.grouping_anchor_id==a.grouping_anchor_id


def test_crop_fallback_retains_original_subject_and_aspect_gates(tmp_path,monkeypatch):
    a=asset(tmp_path,'A',scene());b=asset(tmp_path,'B',crop(scene()));left,right=g.visual_features(a),g.visual_features(b)
    assert g._crop_aligned_similar(a,b,left,right,g.PRESETS['standard'],g.GroupingStats())
    assert not g._crop_aligned_similar(a,b,replace(left,aspect=left.aspect*2),right,g.PRESETS['standard'],g.GroupingStats())
    monkeypatch.setattr(g,'_subject_compatible',lambda *a:False)
    assert not g._crop_aligned_similar(a,b,left,right,g.PRESETS['standard'],g.GroupingStats())


def test_crop_fallback_never_compares_two_cropped_views():
    base=g._visual_from_image(Image.new('L',(64,64),127))
    left=replace(base,whole=(1<<64)-1,crop_variants=(base,))
    right=replace(base,whole=int('01'*32,2),crop_variants=(base,))
    a=PhotoAsset('A',Path('a'),Path('a'),None,None,datetime.now(),'.png',analysis_mode='landscape')
    assert not g._crop_aligned_similar(a,a,left,right,g.PRESETS['loose'],g.GroupingStats())


def test_crop_fallback_uses_stricter_hash_and_structure_even_for_loose():
    base=g._visual_from_image(Image.new('L',(64,64),127))
    a=PhotoAsset('A',Path('a'),Path('a'),None,None,datetime.now(),'.png',analysis_mode='landscape')
    left=replace(base,whole=(1<<64)-1,crop_variants=(base,))
    right=replace(base,whole=(1<<9)-1)
    assert g._similar(a,a,base,right,g.PRESETS['loose'],g.GroupingStats())
    assert not g._crop_aligned_similar(a,a,left,right,g.PRESETS['loose'],g.GroupingStats())
    white=g._visual_from_image(Image.new('L',(64,64),255))
    assert g._similar(a,a,base,white,g.PRESETS['loose'],g.GroupingStats())
    assert not g._crop_aligned_similar(a,a,left,white,g.PRESETS['loose'],g.GroupingStats())


def test_crop_bank_is_bounded_immutable_and_absent_on_tiny_previews():
    v=g._visual_from_image(scene());assert len(v.crop_variants)==2
    for c in v.crop_variants:
        assert not c.crop_variants and c.blocks.shape==(64,64)
        assert not c.blocks.flags.writeable and not c.means.flags.writeable
    assert not g._visual_from_image(Image.new('RGB',(64,64))).crop_variants


def test_unrelated_structured_sources_do_not_join_via_crop_fallback(tmp_path):
    assets=[asset(tmp_path,f'{i:02}',scene(i)) for i in range(12)]
    g._assign_groups_build15_compat(assets,'loose')
    assert len({a.group_id for a in assets})==len(assets)


def _stub_asset(name, mode='landscape'):
    path = Path('/crop-batch-synthetic') / (name + '.png')
    return PhotoAsset(name, path, path, None, path, datetime(2026, 1, 1),
                      '.png', analysis_mode=mode)


def _flat_visual(level):
    return g._base_visual_from_image(Image.new('L', (64, 64), level))


def _scalar_crop_first(query, visual, candidates, representatives, cfg, stats):
    for _distance, position in candidates:
        anchor, left, _group_id = representatives[position]
        if g._crop_aligned_similar(anchor, query, left, visual, cfg, stats):
            return position
    return None


@pytest.mark.parametrize('preset', list(g.PRESETS))
def test_crop_batch_preserves_stricter_subject_hash_gate(preset):
    from ai_cull_assistant.subject import SubjectFeatures

    query = _stub_asset('query', 'portrait')
    query.subject_features = SubjectFeatures('0', '0', '0', (.4, .1, .1, .1))
    visual = _flat_visual(127)
    left = replace(_flat_visual(64), crop_variants=(visual,))
    representatives = []
    for index in range(80):
        anchor = _stub_asset(f'R{index:03}', 'portrait')
        # Nine body bits pass the standard/loose whole-frame gate, but must
        # fail the aligned limit of eight in scalar AND batched paths.
        anchor.subject_features = SubjectFeatures('0', '0', '1ff', (.4, .1, .1, .1))
        if index == 79:
            anchor.subject_features = query.subject_features
        representatives.append((anchor, left, index + 1))
    candidates = [(0, index) for index in range(len(representatives))]
    cfg = g.PRESETS[preset]
    assert _scalar_crop_first(query, visual, candidates, representatives,
                              cfg, g.GroupingStats()) == 79
    assert g._first_crop_matching_representative(
        query, visual, candidates, representatives, cfg, g.GroupingStats()) == 79


@pytest.mark.parametrize('query_bank_size', [0, 1, 2])
@pytest.mark.parametrize('first_rank', [0, 15, 16, 63, 64, 79, 127, 128, None])
def test_crop_batch_matches_scalar_order_with_varied_crop_banks(query_bank_size, first_rank):
    whole, bright, dark, rejected = (_flat_visual(level) for level in (127, 255, 8, 64))
    visual = replace(whole, crop_variants=(bright, dark)[:query_bank_size])
    query = _stub_asset('query')
    representatives = [
        (_stub_asset(f'R{index:03}'),
         replace(rejected, crop_variants=(rejected,) * (index % 3)), index + 1)
        for index in range(145)
    ]
    # Nontrivial positions prove the return value follows candidate ordering,
    # not representative position or the order of alignment direction/scale.
    order = np.random.default_rng(701).permutation(len(representatives)).tolist()
    if first_rank is not None:
        for offset, rank in enumerate(range(first_rank, min(first_rank + 4, len(order)))):
            position = order[rank]
            if offset % 2 and query_bank_size:
                left = (bright, dark)[(query_bank_size - 1)]
            else:
                left = replace(rejected, crop_variants=(rejected, whole))
            representatives[position] = (_stub_asset(f'M{position:03}'), left, position + 1)
    candidates = [(rank // 18, position) for rank, position in enumerate(order)]
    expected = None if first_rank is None else order[first_rank]
    cfg = g.PRESETS['standard']
    assert _scalar_crop_first(query, visual, candidates, representatives,
                              cfg, g.GroupingStats()) == expected
    assert g._first_crop_matching_representative(
        query, visual, candidates, representatives, cfg, g.GroupingStats()) == expected


@pytest.mark.parametrize('direction', ['anchor', 'query'])
def test_crop_batch_matches_scalar_near_structure_threshold(direction):
    rng = np.random.default_rng(319)
    pixels = rng.integers(0, 256, (64, 64), dtype=np.uint8)
    base = replace(g._base_visual_from_image(Image.fromarray(pixels)), whole=0)
    other = rng.integers(0, 256, pixels.shape, dtype=np.uint8)
    query = _stub_asset('query')
    for ratio in (.72, .8, .9, .97):
        mixed = np.rint(ratio * pixels + (1 - ratio) * other).astype(np.uint8)
        value = replace(g._base_visual_from_image(Image.fromarray(mixed)), whole=0)
        score = g.grayscale_structure_similarity(base, value)
        assert score > .86
        # All 65 identical candidates pass or fail together. Exercise both
        # sides of the decision tolerance in the batch boundary recheck.
        if direction == 'anchor':
            visual = value
            left = replace(value, crop_variants=(base,))
        else:
            visual = replace(base, crop_variants=(value,))
            left = base
        representatives = [(_stub_asset(f'R{i:03}'), left, i + 1) for i in range(65)]
        candidates = [(0, i) for i in range(65)]
        for delta in (-2e-5, -1e-7, -1e-9, 0, 1e-9, 1e-7, 2e-5):
            cfg = g.GroupingConfig(8, score + 1e-6 + delta, 1.08)
            expected = _scalar_crop_first(query, visual, candidates, representatives,
                                          cfg, g.GroupingStats())
            actual = g._first_crop_matching_representative(
                query, visual, candidates, representatives, cfg, g.GroupingStats())
            assert actual == expected


def test_legacy_crop_collision_assignment_batches_bounded_scratch(monkeypatch):
    # Unlike the legacy 64px fixture, these descriptors carry both crop views.
    rng = np.random.default_rng(983)
    visuals = {}
    for index in range(96):
        rows = np.concatenate([rng.permutation([0] * 4 + [255] * 4)
                               for _ in range(8)]).astype(np.uint8)
        image = Image.fromarray(np.tile(rows[:, None], (1, 64)))
        image = image.resize((512, 512), Image.Resampling.NEAREST)
        visuals[f'P{index:03}'] = g._visual_from_image(image)
    assert all(len(value.crop_variants) == 2 for value in visuals.values())
    assert {view.whole for value in visuals.values()
            for view in (value, *value.crop_variants)} == {0}
    monkeypatch.setattr(g, 'visual_features', lambda item: visuals[item.stem])
    sizes = []
    original = g._batch_structure_matches

    def record_size(left, *args, **kwargs):
        sizes.append(len(left))
        return original(left, *args, **kwargs)

    monkeypatch.setattr(g, '_batch_structure_matches', record_size)
    assets = [_stub_asset(name) for name in visuals]
    stats = g.GroupingStats()
    g._assign_groups_fixed_legacy(assets, stats=stats)
    assert len({item.group_id for item in assets}) == len(assets)
    assert stats.crop_candidates == 96 * 95 // 2
    assert stats.structure_comparisons == 5 * 96 * 95 // 2
    assert max(sizes) == 64
    # Verify the optimized path preserves full production membership/anchors.
    monkeypatch.setattr(g, '_first_crop_matching_representative', _scalar_crop_first)
    scalar_assets = [_stub_asset(name) for name in reversed(visuals)]
    g._assign_groups_fixed_legacy(scalar_assets)
    assert {item.stem: (item.group_id, item.grouping_anchor_id) for item in assets} == {
        item.stem: (item.group_id, item.grouping_anchor_id) for item in scalar_assets}
