"""Public preset semantics and exact, quality-independent region statistics."""
from datetime import datetime,timedelta
from pathlib import Path
import json
import numpy as np
from PIL import Image
import pytest
from ai_cull_assistant import grouping,scene_burst_grouping as scene
from ai_cull_assistant.models import PhotoAsset

def photos(tmp_path,seconds):
    pixels=np.random.default_rng(1017).integers(0,256,(120,180,3),dtype=np.uint8)
    assets=[]
    for i,second in enumerate(seconds):
        path=tmp_path/f'p{i:03}.png';Image.fromarray(pixels).save(path)
        assets.append(PhotoAsset(stem=path.stem,primary_path=path,display_path=path,raw_path=None,jpg_path=path,
            captured_at=datetime(2026,1,1)+timedelta(seconds=second),ext='.png',preview_path=path))
    return assets

@pytest.mark.parametrize('preset,expected',[('strict',2),('standard',1),('loose',1)])
def test_nine_second_pause_is_distinct_only_in_short_burst_preset(tmp_path,preset,expected):
    assets=photos(tmp_path,[0,9])
    grouping.assign_groups(assets,preset,preserve_existing=False)
    assert len({a.group_id for a in assets})==expected

@pytest.mark.parametrize('preset,expected',[('strict',2),('standard',2),('loose',1)])
def test_fifteen_second_pause_needs_loose_burst_preset(tmp_path,preset,expected):
    assets=photos(tmp_path,[0,15])
    grouping.assign_groups(assets,preset,preserve_existing=False)
    assert len({a.group_id for a in assets})==expected

@pytest.mark.parametrize('preset,expected',[('strict',2),('standard',1),('loose',1)])
def test_long_continuous_burst_obeys_explained_span(tmp_path,preset,expected):
    assets=photos(tmp_path,range(0,71,7))
    grouping.assign_groups(assets,preset,preserve_existing=False)
    assert len({a.group_id for a in assets})==expected

def test_all_presets_keep_standard_identity_scene_and_bridge_guards():
    from dataclasses import replace
    for cfg in scene.PRESET_CONFIGS.values():
        assert replace(cfg,gap_seconds=12.,span_seconds=90.)==scene.CONFIG
    assert scene.PRESET_CONFIGS['standard'] is scene.CONFIG
    assert [(c.gap_seconds,c.span_seconds) for c in scene.PRESET_CONFIGS.values()]==[(8.,60.),(12.,90.),(18.,120.)]

@pytest.mark.parametrize('preset',['strict','standard','loose'])
def test_presets_do_not_read_rejection_or_quality_state(tmp_path,preset):
    assets=photos(tmp_path,[0,.2])
    grouping.assign_groups(assets,preset,preserve_existing=False)
    original=[a.group_id for a in assets]
    for a in assets:
        a.auto_rejected=True;a.focus_score=0.;a.notes=['body_blur','face_blur']
        a.ai_focus_result={'status':'blur'};a.ai_focus_stale=True;a.ai_focus_dirty=True
        a.clarity_evidence={'recommendation':'reject'};a.local_recommendation='reject'
    grouping.assign_groups(assets,preset,preserve_existing=False)
    assert [a.group_id for a in assets]==original and len(set(original))==1

@pytest.mark.parametrize('preset',['strict','standard','loose'])
def test_manual_memberships_and_absent_ids_remain_reserved(tmp_path,preset):
    assets=photos(tmp_path,[0,.2,.4]);assets[0].group_id=75;assets[0].grouping_anchor_id='manual'
    grouping.assign_groups(assets,preset,preserve_existing=False,protected_group_ids={75,100})
    assert assets[0].group_id==75 and assets[0].grouping_anchor_id=='manual'
    assert assets[1].group_id==assets[2].group_id>100

def histogram_inputs():
    rng=np.random.default_rng(20261017)
    arrays=[rng.integers(0,256,(*shape,3),dtype=np.uint8) for shape in [(13,17),(640,427),(1,1),(4,4)]]
    arrays += [np.full((3,7,3),v,np.uint8) for v in [0,63,64,127,128,191,192,255]]
    for a in arrays:
        for mode in ('all','random','empty','rectangle'):
            mask=None if mode=='all' else np.full(a.shape[:2],255 if mode=='rectangle' else 0,np.uint8)
            if mode=='random':mask=rng.integers(0,2,a.shape[:2],dtype=np.uint8)*255
            yield a,mask

def test_histograms_are_bit_exact_to_frozen_v9_outputs():
    fixture=json.loads(Path(__file__).with_name('data').joinpath('grouping-histogram-v9.json').read_text(encoding='utf-8'))
    for (pixels,mask),expected in zip(histogram_inputs(),fixture['expected'],strict=True):
        result=scene._hist(pixels,mask)
        shared=scene._hist(pixels,mask,scene._hist_bins(pixels))
        assert np.array_equal(result,np.asarray(expected,np.float32))
        assert np.array_equal(shared,result)

def test_prepared_preview_rewrites_with_same_stat_are_not_cached(tmp_path):
    asset=photos(tmp_path,[0])[0]
    # BMP has a stable byte length even when its pixels change.
    path=tmp_path/'snapshot.bmp';Image.new('RGB',(120,80),'red').save(path);asset.preview_path=path
    before=path.stat();first=scene.build_context(asset)
    Image.new('RGB',(120,80),'blue').save(path)
    import os
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert path.stat().st_size==before.st_size and path.stat().st_mtime_ns==before.st_mtime_ns
    second=scene.build_context(asset)
    assert not np.array_equal(first.cohort,second.cohort)

def test_identity_model_corruption_is_checked_before_a_new_run(tmp_path):
    from ai_cull_assistant.face_identity import IdentityEngine,IdentityUnavailable
    path=tmp_path/'model.onnx';path.write_bytes(b'not an offline recognition model')
    with pytest.raises(IdentityUnavailable,match='校验失败'):
        IdentityEngine(model_path=path)


@pytest.mark.parametrize('preset,expected',[('strict',3),('standard',2),('loose',1)])
def test_hundred_second_burst_distinguishes_standard_and_loose_span(tmp_path,preset,expected):
    # Strict keeps the standard 0..90 / 95..110 boundary, then splits 0..90
    # into 0..60 / 65..90; it cannot join the two later fragments together.
    assets=photos(tmp_path,range(0,111,5))
    grouping.assign_groups(assets,preset,preserve_existing=False)
    assert len({a.group_id for a in assets})==expected


@pytest.mark.parametrize('preset',['strict','standard','loose'])
def test_cancel_keeps_public_memberships_atomic_then_retry_completes(tmp_path,preset):
    assets=photos(tmp_path,[0,.2,.4]);calls=0
    def cancel():
        nonlocal calls
        calls+=1
        return calls>=4
    with pytest.raises(grouping.GroupingCancelled):
        grouping.assign_groups(assets,preset,preserve_existing=False,cancel_check=cancel)
    assert all(a.group_id==0 and a.grouping_anchor_id is None for a in assets)
    grouping.assign_groups(assets,preset,preserve_existing=False)
    assert len({a.group_id for a in assets})==1 and assets[0].group_id>0


@pytest.mark.parametrize('preset',['strict','standard','loose'])
def test_reopened_members_keep_anchor_and_accept_incremental_photo(tmp_path,preset):
    from ai_cull_assistant.group_store import save_groups,load_groups
    original=photos(tmp_path,[0,.2])
    grouping.assign_groups(original,preset,preserve_existing=False)
    anchor=original[0].grouping_anchor_id;gid=original[0].group_id
    store=tmp_path/'groups.json';save_groups(original,store,source='auto',collection_key='preset-test')
    reopened=photos(tmp_path,[0,.2,.4])
    assert load_groups(reopened[:2],store,collection_key='preset-test')
    grouping.assign_groups(reopened,preset,preserve_existing=True)
    assert all(a.group_id==gid and a.grouping_anchor_id==anchor for a in reopened)


def graph_observations(tmp_path,monkeypatch,seconds,accepted):
    from dataclasses import replace
    assets=photos(tmp_path,seconds);base=scene.build_context(assets[0])
    contexts=[replace(base,time=base.time+s,frame_hash=i) for i,s in enumerate(seconds)]
    monkeypatch.setattr(scene,'build_context',lambda a,identity=None:contexts[int(a.stem[1:])])
    def evidence(left,right,cfg=scene.CONFIG):
        ok=accepted(left.frame_hash,right.frame_hash)
        return ok,dict(camera='same',geometry={'ratio':1.},cohort_overlap=1.,people_coverage={'strong_identity':False})
    monkeypatch.setattr(scene,'pair_evidence',evidence)
    return assets


def test_strict_window_cannot_steal_a_standard_member_into_another_group(tmp_path,monkeypatch):
    assets=graph_observations(tmp_path,monkeypatch,[0,5,10],lambda a,b:{a,b}!={0,1})
    grouping.assign_groups(assets,'standard',preserve_existing=False)
    assert assets[0].group_id==assets[2].group_id!=assets[1].group_id
    grouping.assign_groups(assets,'strict',preserve_existing=False)
    assert len({a.group_id for a in assets})==3


def test_loose_window_cannot_split_a_standard_group_via_an_older_reference(tmp_path,monkeypatch):
    assets=graph_observations(tmp_path,monkeypatch,[0,13,13.2],lambda a,b:{a,b}!={0,2})
    grouping.assign_groups(assets,'standard',preserve_existing=False)
    assert assets[0].group_id!=assets[1].group_id==assets[2].group_id
    grouping.assign_groups(assets,'loose',preserve_existing=False)
    assert assets[0].group_id!=assets[1].group_id==assets[2].group_id


def test_loose_merge_needs_both_directions_of_partial_person_support(tmp_path,monkeypatch):
    assets=graph_observations(tmp_path,monkeypatch,[0,15],lambda a,b:a<b)
    grouping.assign_groups(assets,'loose',preserve_existing=False)
    assert assets[0].group_id!=assets[1].group_id


def test_loose_merge_cannot_chain_through_a_shared_middle_group(tmp_path,monkeypatch):
    assets=graph_observations(tmp_path,monkeypatch,[0,15,30],lambda a,b:abs(a-b)<=1)
    grouping.assign_groups(assets,'loose',preserve_existing=False)
    assert assets[0].group_id==assets[1].group_id!=assets[2].group_id


def test_loose_incremental_merge_checks_all_old_members_with_saved_anchor(tmp_path):
    assets=photos(tmp_path,[0,.2,15])
    grouping.assign_groups(assets[:2],'standard',preserve_existing=False)
    gid=assets[0].group_id;anchor=assets[0].grouping_anchor_id
    grouping.assign_groups(assets,'loose',preserve_existing=True)
    assert all(a.group_id==gid and a.grouping_anchor_id==anchor for a in assets)


@pytest.mark.parametrize('anchors',[('missing','missing'),('one','two')])
def test_loose_does_not_repair_missing_or_conflicting_saved_anchor_by_merging(tmp_path,anchors):
    assets=photos(tmp_path,[0,.2,15])
    for a,anchor in zip(assets,anchors):a.group_id=9;a.grouping_anchor_id=anchor
    grouping.assign_groups(assets,'loose',preserve_existing=True)
    assert [a.grouping_anchor_id for a in assets[:2]]==list(anchors)
    assert assets[0].group_id==assets[1].group_id==9 and assets[2].group_id!=9


def test_loose_does_not_merge_two_saved_groups_even_with_compatible_pixels(tmp_path):
    assets=photos(tmp_path,[0,15])
    for a,gid in zip(assets,(9,12)):a.group_id=gid;a.grouping_anchor_id=grouping.source_photo_id(a)
    before=[(a.group_id,a.grouping_anchor_id) for a in assets]
    grouping.assign_groups(assets,'loose',preserve_existing=True)
    assert [(a.group_id,a.grouping_anchor_id) for a in assets]==before
