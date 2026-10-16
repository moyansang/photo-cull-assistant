from dataclasses import asdict
from datetime import datetime
import os

import numpy as np
import pytest
from PIL import Image

from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.roi_contract import resolve_rois
from ai_cull_assistant.roi_features import (FEATURE_SCHEMA, features_from_resolved,
    refresh_asset_roi_features, resolve_asset_rois)
from ai_cull_assistant.subject import SubjectFeatures, image_hash


def asset_for(path):
    return PhotoAsset(path.stem, path, path, None, path, datetime.now(), path.suffix,
                      preview_path=path)


def pixels():
    return Image.fromarray(np.random.default_rng(91).integers(0, 256, (100, 120, 3), dtype=np.uint8))


def test_explicit_body_hash_reads_body_pixels_and_preserves_all_rois():
    image = pixels()
    roi = resolve_rois([[.1,.1,.2,.2]], [[0,0,.5,1], [.5,0,.5,1]],
                       {'primary_body_index':1, 'primary_body_basis':
                        resolve_rois([], [[0,0,.5,1],[.5,0,.5,1]])['body_rois_digest']})
    found = features_from_resolved(image, roi, image_digest='test')
    assert found.feature_schema == FEATURE_SCHEMA
    assert found.body == image_hash(image.crop((60,0,120,100)))
    assert len(found.analysis_bodies) == len(found.body_hashes) == 2
    assert found.face_hashes == (image_hash(image.crop((12,10,37,31))),)
    assert found.whole == image_hash(image)
    assert found.landmarks is None
    assert SubjectFeatures(**asdict(found)) == found


def test_no_primary_does_not_arbitrarily_choose_first_person():
    roi = resolve_rois([[0,0,.2,.2],[.7,0,.2,.2]], [[0,0,.4,1],[.6,0,.4,1]])
    found = features_from_resolved(pixels(), roi, image_digest='x')
    assert found.face is None and found.body is None
    assert found.roi_primary_selection_required
    assert len(found.face_hashes) == len(found.body_hashes) == 2


def test_hidden_does_not_change_whole_descriptor():
    image = pixels()
    roi = resolve_rois([[0,0,.2,.2]], [[0,0,.5,1]], {'subjects_disabled':True})
    found = features_from_resolved(image, roi, image_digest='x')
    assert found.whole == image_hash(image)
    assert not found.analysis_faces and not found.analysis_bodies
    assert found.face is found.body is None


def test_full_content_invalidation_even_with_equal_stats(tmp_path):
    path = tmp_path/'source.bmp'
    pixels().save(path)
    asset = asset_for(path)
    settings = CropSettings()
    roi = resolve_rois([], [[0,0,1,1]])
    before = refresh_asset_roi_features(asset, settings, roi)
    stat = path.stat()
    Image.fromarray(np.asarray(pixels())[:, ::-1]).save(path)
    os.utime(path, ns=(stat.st_atime_ns,stat.st_mtime_ns))
    after = refresh_asset_roi_features(asset, settings, roi)
    assert path.stat().st_size == stat.st_size
    assert before.image_digest != after.image_digest
    assert before.body != after.body


def test_selection_revision_and_mutation_cannot_poison_cache(tmp_path):
    path = tmp_path/'source.png'; pixels().save(path)
    asset = asset_for(path); settings = CropSettings()
    a = resolve_rois([], [[0,0,.5,1]])
    b = resolve_rois([], [[.5,0,.5,1]])
    first = refresh_asset_roi_features(asset, settings, a)
    first.analysis_bodies[0]['box'][0] = .9
    again = refresh_asset_roi_features(asset, settings, a)
    assert again.analysis_bodies[0]['box'][0] == 0
    second = refresh_asset_roi_features(asset, settings, b)
    assert second.roi_revision_digest != again.roi_revision_digest
    assert second.body != again.body


def test_failed_refresh_clears_old_descriptor(tmp_path):
    path = tmp_path/'source.png'; pixels().save(path)
    asset = asset_for(path)
    asset.subject_features = SubjectFeatures('a','b','c',(.1,.1,.1,.1))
    asset.subject_checked = True
    bad = resolve_rois([], [[0,0,1,1]])
    bad['analysis_bodies'][0]['box'] = [0,0,.5,1]
    with pytest.raises(ValueError):
        refresh_asset_roi_features(asset,CropSettings(),bad)
    assert asset.subject_features is None and not asset.subject_checked


def test_legacy_and_new_empty_face_semantics_with_complete_resolver(tmp_path):
    path = tmp_path/'source.png'; pixels().save(path)
    asset = asset_for(path)
    settings = CropSettings(photos={str(path.resolve()).casefold(): {'selected_faces':[]}})
    args = dict(auto_faces=[[.1,.1,.2,.2]], auto_bodies=[[0,0,1,1]])
    old = resolve_asset_rois(asset,settings,**args)
    assert old['analysis_bodies'] == []
    settings.photos[settings.key(asset)]['roi_selection_schema'] = 2
    new = resolve_asset_rois(asset,settings,**args)
    assert new['analysis_faces'] == [] and len(new['analysis_bodies']) == 1


def test_manual_face_alias_and_body_kept_together(tmp_path):
    path = tmp_path/'source.png'; pixels().save(path)
    asset = asset_for(path)
    settings = CropSettings(photos={str(path.resolve()).casefold(): {'manual_face':[.2,.2,.1,.1]}})
    resolved = resolve_asset_rois(asset,settings,auto_faces=[],auto_bodies=[[0,0,1,1]])
    assert resolved['analysis_faces'][0]['origin'] == 'manual'
    assert len(resolved['analysis_bodies']) == 1


def test_manual_full_selection_never_runs_detectors(tmp_path, monkeypatch):
    from ai_cull_assistant import subject, body_focus
    path = tmp_path/'source.png'; pixels().save(path)
    asset = asset_for(path)
    settings = CropSettings(photos={str(path.resolve()).casefold():
        {'selected_faces':[[.1,.1,.2,.2]],'selected_bodies':[[0,0,.5,1]]}})
    def fail(*a, **kw):
        raise AssertionError('Manual selections must not trigger automatic inference')
    monkeypatch.setattr(subject, 'detect', fail)
    monkeypatch.setattr(body_focus, 'detect_body_candidates', fail)
    found = refresh_asset_roi_features(asset,settings)
    assert found.face == (.1,.1,.2,.2)
    assert found.body_box == (0.,0.,.5,1.)


def test_manual_face_move_cannot_keep_previous_body_hash(tmp_path, monkeypatch):
    from ai_cull_assistant import subject
    path = tmp_path/'source.png'; pixels().save(path)
    asset = asset_for(path)
    base = refresh_asset_roi_features(asset,CropSettings(),
                                     resolve_rois([[.1,.1,.2,.2]],[[0,0,.5,1]]))
    monkeypatch.setattr(subject,'detect',lambda *a,**kw:[])
    moved = subject._manual_detail(asset,CropSettings(),base,[.6,.2,.2,.2])
    assert moved.body is None and moved.feature_schema is None
    assert moved.analysis_bodies == ()


def test_quality_view_is_independent_of_ambiguous_grouping_rois(tmp_path, monkeypatch):
    from ai_cull_assistant import subject
    path = tmp_path/'source.png'; pixels().save(path)
    asset = asset_for(path)
    grouped = refresh_asset_roi_features(asset,CropSettings(),resolve_rois(
        [[.1,.1,.2,.2],[.6,.1,.2,.2]],[[0,0,.5,1],[.5,0,.5,1]]))
    assert grouped.face is None and grouped.body is None
    quality = SubjectFeatures('a','b','c',(.1,.1,.2,.2),(.05,.05,.3,.3),
                              landmarks=((.15,.15),)*5)
    monkeypatch.setattr(subject,'features',lambda *a,**kw:quality)
    selected = subject.detail_features_list(asset,CropSettings(),require_landmarks=True)
    assert selected == [quality]
    assert asset.subject_features is grouped
    assert asset.subject_features.feature_schema == FEATURE_SCHEMA


def test_exif_orientation_precedes_roi_coordinates(tmp_path):
    path = tmp_path/'oriented.jpg'
    image = pixels()
    exif = Image.Exif(); exif[274] = 6
    image.save(path,exif=exif)
    asset = asset_for(path)
    found = refresh_asset_roi_features(asset,CropSettings(),resolve_rois([],[[0,0,.5,1]]))
    from PIL import ImageOps
    with Image.open(path) as source:
        oriented = ImageOps.exif_transpose(source).convert('RGB')
    assert oriented.size == (100,120)
    assert found.body == image_hash(oriented.crop((0,0,50,120)))


def test_json_roundtrip_normalizes_every_sequence():
    import json
    found = features_from_resolved(pixels(),resolve_rois([[.1,.1,.2,.2]],[[0,0,.5,1]]),image_digest='x')
    restored = SubjectFeatures(**json.loads(json.dumps(asdict(found))))
    assert restored == found
    assert asdict(restored) == asdict(found)


def test_roi_refresh_never_rebuilds_prepared_preview(tmp_path, monkeypatch):
    from ai_cull_assistant import preview
    path = tmp_path/'source.png'; pixels().save(path)
    asset = asset_for(path)
    monkeypatch.setattr(preview,'ensure_preview',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('write')))
    found = refresh_asset_roi_features(asset,CropSettings(),resolve_rois([],[[0,0,1,1]]))
    assert found.feature_schema == FEATURE_SCHEMA
    asset.preview_path = tmp_path/'missing.png'
    with pytest.raises(OSError):
        refresh_asset_roi_features(asset,CropSettings(),resolve_rois([],[]))
    assert asset.subject_features is None


def _gradient_people():
    # Actual distinguishable ROI pixels, not mocked descriptors: increasing
    # grayscale at left and decreasing grayscale at right yield opposite dHashes.
    row = np.concatenate((np.linspace(0,255,60),np.linspace(255,0,60))).astype(np.uint8)
    return Image.fromarray(np.tile(row,(100,1))).convert('RGB')


def _grouping_asset(tmp_path, name, image, roi):
    path = tmp_path/(name+'.png'); image.save(path)
    asset = asset_for(path)
    refresh_asset_roi_features(asset,CropSettings(),roi)
    return asset


def test_single_subject_real_descriptor_uses_original_body_distance_gate(tmp_path):
    from ai_cull_assistant.grouping import _subject_compatible, GroupingConfig, hamming_distance
    roi = resolve_rois([[.1,.1,.2,.2]],[[0,0,.5,1]])
    image = _gradient_people()
    left = _grouping_asset(tmp_path,'left',image,roi)
    same = _grouping_asset(tmp_path,'same',image,roi)
    other = _grouping_asset(tmp_path,'other',Image.fromarray(255-np.asarray(image)),roi)
    assert not left.subject_features.roi_primary_selection_required
    assert hamming_distance(left.subject_features.body,other.subject_features.body) == 64
    assert _subject_compatible(left,same,GroupingConfig())
    assert not _subject_compatible(left,other,GroupingConfig())


def test_explicit_primary_restores_body_gate_and_manual_primary_change_refreshes(tmp_path):
    from ai_cull_assistant.grouping import _subject_compatible, GroupingConfig
    # Keep face primary fixed to isolate the actual body ROI descriptor gate.
    entry = {'selected_faces':[[.1,.1,.2,.2],[.7,.1,.2,.2]],
             'selected_bodies':[[0,0,.5,1],[.5,0,.5,1]],
             'primary_face_index':0,'primary_body_index':0}
    first_roi = resolve_rois([],[],entry)
    image = _gradient_people()
    left = _grouping_asset(tmp_path,'primary_a',image,first_roi)
    right = _grouping_asset(tmp_path,'primary_b',image,first_roi)
    original = right.subject_features
    assert len(original.analysis_faces) == len(original.analysis_bodies) == 2
    assert not original.roi_primary_selection_required
    assert _subject_compatible(left,right,GroupingConfig())
    entry['primary_body_index'] = 1
    changed_roi = resolve_rois([],[],entry)
    changed = refresh_asset_roi_features(right,CropSettings(),changed_roi)
    assert changed.roi_revision_digest != original.roi_revision_digest
    assert changed.face == original.face
    assert changed.body_hashes == original.body_hashes
    assert changed.body == changed.body_hashes[1] != original.body
    assert changed.whole == original.whole
    assert not _subject_compatible(left,right,GroupingConfig())


def test_ambiguous_primary_is_diagnostic_and_uses_original_whole_fallback(tmp_path):
    from ai_cull_assistant import grouping
    roi = resolve_rois([[.1,.1,.2,.2],[.7,.1,.2,.2]],[[0,0,.5,1],[.5,0,.5,1]])
    image = _gradient_people()
    left = _grouping_asset(tmp_path,'ambiguous_a',image,roi)
    right = _grouping_asset(tmp_path,'ambiguous_b',Image.fromarray(255-np.asarray(image)),roi)
    assert left.subject_features.roi_primary_selection_required
    assert right.subject_features.roi_primary_selection_required
    assert left.subject_features.face is left.subject_features.body is None
    # Policy A: missing subject evidence does not veto a whole-image match.
    assert grouping._subject_compatible(left,right,grouping.GroupingConfig())
    # It does NOT mean body thresholds passed. Full-image visual criteria are
    # still evaluated and reject this contrasting actual-pixel pair.
    left_visual, right_visual = grouping.visual_features(left),grouping.visual_features(right)
    assert not grouping._similar(left,right,left_visual,right_visual,
                                 grouping.GroupingConfig(),grouping.GroupingStats())


@pytest.mark.parametrize('face', [[.6,.1,.2,.2],[.1,.1,.4,.4]])
def test_explicit_roi_preserves_original_face_displacement_and_area_gates(tmp_path,face):
    from ai_cull_assistant.grouping import _subject_compatible, GroupingConfig
    image = _gradient_people()
    left = _grouping_asset(tmp_path,'gate_left',image,resolve_rois([[.1,.1,.2,.2]],[[0,0,.5,1]]))
    right = _grouping_asset(tmp_path,'gate_right',image,resolve_rois([face],[[0,0,.5,1]]))
    assert left.subject_features.body == right.subject_features.body
    assert not _subject_compatible(left,right,GroupingConfig())
