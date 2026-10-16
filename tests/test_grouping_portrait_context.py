"""Corroborated portrait matches must not weaken manual or scene boundaries."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from ai_cull_assistant import grouping, portrait_grouping
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.roi_contract import resolve_rois
from ai_cull_assistant.roi_features import features_from_resolved


def portrait(tmp_path, name, *, background=72, face=17, origin='automatic'):
    rng = np.random.default_rng(background)
    pixels = rng.integers(30, 210, (480, 320, 3), dtype=np.uint8)
    # Fixed costume/context with a separately controlled, informative face.
    pixels[160:450, 82:234] = np.random.default_rng(23).integers(50, 190, (290, 152, 3), dtype=np.uint8)
    pixels[96:154, 138:177] = np.random.default_rng(face).integers(30, 240, (58, 39, 3), dtype=np.uint8)
    path = tmp_path / (name+'.png')
    Image.fromarray(pixels).save(path)
    asset = PhotoAsset(name, path, path, None, path, datetime(2026, 1, 1), '.png', preview_path=path)
    face_box = (.43, .2, .12, .12)
    body_box = (.25, .32, .5, .62)
    override = ({'selected_faces':[list(face_box)], 'selected_bodies':[list(body_box)]}
                if origin=='manual' else {})
    resolved=resolve_rois([{'box':list(face_box)}], [{'box':list(body_box)}], override)
    with Image.open(path) as im:
        asset.subject_features=features_from_resolved(
            im, resolved, image_digest=sha256(path.read_bytes()).hexdigest())
    asset.subject_checked=True
    return asset


def refresh_pixels(asset):
    subject=asset.subject_features
    resolved=resolve_rois(list(subject.analysis_faces), list(subject.analysis_bodies))
    with Image.open(asset.preview_path) as im:
        asset.subject_features=features_from_resolved(
            im, resolved, image_digest=sha256(asset.preview_path.read_bytes()).hexdigest())


@pytest.mark.parametrize('distance', [14, 22, 31])
def test_explicit_body_difference_needs_corrob_scene_and_face(tmp_path, distance):
    a, b = portrait(tmp_path, 'A'), portrait(tmp_path, 'B')
    # Controlled prior descriptor disagreement; new evidence comes from pixels.
    b.subject_features = deepcopy(b.subject_features)
    from dataclasses import replace
    changed=f'{int(a.subject_features.body,16)^((1 << distance)-1):016x}'
    b.subject_features = replace(b.subject_features, body=changed, body_hashes=(changed,))
    assert not grouping._subject_compatible(a, b, grouping.PRESETS['standard'])
    before = [deepcopy(x.subject_features) for x in (a, b)]
    grouping.assign_groups([a, b], preserve_existing=False)
    assert a.group_id == b.group_id
    assert [x.subject_features for x in (a,b)] == before


@pytest.mark.parametrize('change', ['face', 'background'])
def test_matching_scene_or_subject_alone_is_insufficient(tmp_path, change):
    a = portrait(tmp_path, 'A')
    b = portrait(tmp_path, 'B', face=98 if change=='face' else 17,
                 background=98 if change=='background' else 72)
    from dataclasses import replace
    changed=f'{int(a.subject_features.body,16)^((1 << 64)-1):016x}'
    b.subject_features = replace(b.subject_features, body=changed, body_hashes=(changed,))
    contexts=[portrait_grouping.build_portrait_context(asset) for asset in (a,b)]
    assert all(context is not None for context in contexts)
    assert not portrait_grouping.pose_compatible(*contexts)
    grouping.assign_groups([a,b], preserve_existing=False)
    assert a.group_id != b.group_id


@pytest.mark.parametrize('kind', ['manual', 'ambiguous', 'missing', 'decode_error'])
def test_uncertain_or_manual_rois_never_get_relaxed_context(tmp_path, kind):
    a = portrait(tmp_path, 'A', origin='manual' if kind=='manual' else 'automatic')
    from dataclasses import replace
    if kind=='ambiguous':
        a.subject_features=replace(a.subject_features, roi_primary_selection_required=True)
    elif kind=='missing':
        a.subject_features=replace(a.subject_features, face=None)
    elif kind=='decode_error':
        a.notes.append('decode_error: test input')
    assert portrait_grouping.build_portrait_context(a) is None


def test_low_texture_background_is_not_positive_scene_evidence(tmp_path):
    a=portrait(tmp_path, 'A')
    with Image.open(a.preview_path) as source:
        pixels=np.asarray(source).copy()
    pixels[:90]=90;pixels[:, :80]=90;pixels[:, 235:]=90
    Image.fromarray(pixels).save(a.preview_path)
    refresh_pixels(a)
    context=portrait_grouping.build_portrait_context(a)
    assert context is not None
    assert not portrait_grouping.pose_compatible(context, context)


def test_large_camera_transform_is_not_same_camera_pose_fallback(tmp_path):
    a,b=portrait(tmp_path,'A'),portrait(tmp_path,'B')
    with Image.open(b.preview_path) as im:
        im.rotate(15, resample=Image.Resampling.BICUBIC).save(b.preview_path)
    refresh_pixels(b)
    x,y=portrait_grouping.build_portrait_context(a),portrait_grouping.build_portrait_context(b)
    assert x is not None and y is not None
    assert not portrait_grouping._scene_compatible(x,y,portrait_grouping._STANDARD)


def test_changed_preview_bytes_cannot_reuse_saved_context(tmp_path):
    a=portrait(tmp_path,'A')
    assert portrait_grouping.build_portrait_context(a) is not None
    with Image.open(a.preview_path) as im:
        im.rotate(1).save(a.preview_path)
    assert portrait_grouping.build_portrait_context(a) is None


@pytest.mark.parametrize('preset',['strict','standard','loose'])
def test_explicit_presets_keep_same_evidence_and_subject_fields(tmp_path,preset):
    a,b=portrait(tmp_path,'A'),portrait(tmp_path,'B')
    before=deepcopy(a.subject_features)
    assert portrait_grouping.pose_compatible(a,b,preset)
    assert a.subject_features==before


def _weak_graph(monkeypatch, tmp_path):
    assets=[portrait(tmp_path,name) for name in 'ABC']
    monkeypatch.setattr(grouping,'_similar',lambda *a,**kw:False)
    monkeypatch.setattr(grouping,'_crop_aligned_similar',lambda *a,**kw:False)
    monkeypatch.setattr(portrait_grouping,'build_portrait_context',
                        lambda asset:SimpleNamespace(context_hash=0, name=asset.stem))
    monkeypatch.setattr(portrait_grouping,'pose_compatible',
                        lambda a,b,preset='standard':{a.name,b.name}!={'A','C'})
    return assets


def test_relaxed_edges_cannot_bridge_incompatible_members(monkeypatch,tmp_path):
    assets=_weak_graph(monkeypatch,tmp_path)
    grouping.assign_groups(assets,preserve_existing=False)
    assert [a.group_id for a in assets]==[1,1,2]


def test_relaxed_component_membership_is_order_independent(monkeypatch,tmp_path):
    assets=_weak_graph(monkeypatch,tmp_path)
    grouping.assign_groups(assets,preserve_existing=False)
    forward={a.stem:a.group_id for a in assets}
    for a in assets:a.group_id=0;a.grouping_anchor_id=None
    grouping.assign_groups(list(reversed(assets)),preserve_existing=False)
    assert forward=={a.stem:a.group_id for a in assets}


def test_strict_component_cannot_be_joined_through_only_one_weak_member(monkeypatch,tmp_path):
    assets=_weak_graph(monkeypatch,tmp_path)
    monkeypatch.setattr(grouping,'_similar',lambda a,b,*rest,**kw:{a.stem,b.stem}=={'B','C'})
    grouping.assign_groups(assets,preserve_existing=False)
    assert assets[0].group_id!=assets[1].group_id==assets[2].group_id


def test_cancel_during_context_build_leaves_all_group_fields_intact(monkeypatch,tmp_path):
    assets=_weak_graph(monkeypatch,tmp_path)
    for asset in assets:
        asset.group_id=9
        asset.grouping_anchor_id='saved-anchor'
    built=[]
    def build(asset):
        built.append(asset.stem)
        return SimpleNamespace(context_hash=0,name=asset.stem)
    monkeypatch.setattr(portrait_grouping,'build_portrait_context',build)
    with pytest.raises(grouping.GroupingCancelled):
        grouping.assign_groups(assets,preserve_existing=False,cancel_check=lambda:bool(built))
    assert built==['A']
    assert [(a.group_id,a.grouping_anchor_id) for a in assets]==[(9,'saved-anchor')]*3


def test_protected_manual_component_is_never_context_candidate(monkeypatch,tmp_path):
    assets=_weak_graph(monkeypatch,tmp_path)
    assets[1].group_id=assets[2].group_id=7
    assets[1].grouping_anchor_id=assets[2].grouping_anchor_id='manual-anchor'
    observed=[]
    def build(asset):
        observed.append(asset.stem)
        return SimpleNamespace(context_hash=0,name=asset.stem)
    monkeypatch.setattr(portrait_grouping,'build_portrait_context',build)
    grouping.assign_groups(assets,preserve_existing=False,protected_group_ids={7})
    assert observed==['A']
    assert [(a.group_id,a.grouping_anchor_id) for a in assets[1:]]==[(7,'manual-anchor')]*2
