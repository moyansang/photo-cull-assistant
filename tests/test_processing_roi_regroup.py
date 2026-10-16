"""Rescan grouping uses staged ROI features and atomic membership publication."""
from copy import deepcopy
import json
from threading import Event

from PIL import Image
import pytest

from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.processing_job import ProcessingJobError, SourceChangedError, load_job, start_job
from ai_cull_assistant.session_store import load_session
from ai_cull_assistant.subject import SubjectFeatures
from ai_cull_assistant.roi_features import FEATURE_SCHEMA
from ai_cull_assistant.workflow import persist_manual_groups

OPTIONS = dict(grouping_preset='standard', technical_screening=False)


def _refresh(asset, settings):
    asset.subject_features = SubjectFeatures(
        whole='0'*16, center='1'*16, body='2'*16, face=(.1, .1, .2, .2),
        feature_schema=FEATURE_SCHEMA, body_box=(.1, .2, .5, .7),
        face_hashes=('1'*16,), body_hashes=('2'*16,))
    asset.subject_checked = True


@pytest.fixture
def scanned(tmp_path, monkeypatch):
    photos, workspace = tmp_path/'photos', tmp_path/'workspace'
    photos.mkdir()
    for index in range(3):
        Image.new('RGB', (80, 100), (30+index, 60, 90)).save(photos/f'P{index}.jpg')
    monkeypatch.setattr('ai_cull_assistant.roi_features.refresh_asset_roi_features', _refresh)
    result = start_job(photos, workspace, OPTIONS, CropSettings(), mode='scan').run(
        OPTIONS, CropSettings(), Event(), None)
    return photos, workspace, result


def _published(workspace):
    return {name: (workspace/name).read_bytes() for name in (
        'groups.json', 'scan-session.json', 'screening_results.json', 'processing-settings.json')}


def _job(scanned, crops=None, **kwargs):
    photos, workspace, result = scanned
    return start_job(photos, workspace, OPTIONS, crops or CropSettings(),
                     result=result, mode='rescan', regroup=True, **kwargs)


def test_regroup_refreshes_untouched_assets_and_publishes_reopen(scanned, monkeypatch):
    photos, workspace, first = scanned
    before_ids = [asset.group_id for asset in first.assets]
    first.assets[0].ai_focus_dirty = True
    refreshed, paired = [], []
    def refresh(asset, crops):
        refreshed.append(asset.stem)
        _refresh(asset, crops)
    def assign(assets, preset, **kwargs):
        assert refreshed == [asset.stem for asset in assets]
        assert kwargs['preserve_existing'] is False
        assert all(a.subject_features.feature_schema == FEATURE_SCHEMA for a in assets)
        paired.append(preset)
        for index, asset in enumerate(assets):
            asset.group_id = index+21
    monkeypatch.setattr('ai_cull_assistant.roi_features.refresh_asset_roi_features', refresh)
    monkeypatch.setattr('ai_cull_assistant.processing_job.assign_groups', assign)
    job = _job(scanned)
    assert job._data['work_indices'] == [0]
    result = job.run(OPTIONS, CropSettings(), Event(), None)
    assert paired == ['standard']
    assert [a.group_id for a in first.assets] == before_ids
    assert [a.group_id for a in result.assets] == [21, 22, 23]
    assert set(json.loads((workspace/'groups.json').read_text())['groups']) == {'21','22','23'}
    reopened = load_session(workspace, photos)
    assert [a.group_id for a in reopened.assets] == [21,22,23]
    assert all(a.subject_features.feature_schema == FEATURE_SCHEMA for a in reopened.assets)


def test_all_manual_groups_and_store_bytes_survive_regroup(scanned):
    _, workspace, first = scanned
    for index, asset in enumerate(first.assets):
        asset.group_id = 31+index
    persist_manual_groups(first)
    before = (workspace/'groups.json').read_bytes()
    result = _job(scanned).run(OPTIONS, CropSettings(), Event(), None)
    assert [a.group_id for a in result.assets] == [31,32,33]
    assert (workspace/'groups.json').read_bytes() == before


def test_stop_during_roi_refresh_never_publishes_and_can_resume(scanned, monkeypatch):
    photos, workspace, first = scanned
    before = _published(workspace)
    stop = Event()
    def refresh(asset, crops):
        _refresh(asset, crops)
        stop.set()
    monkeypatch.setattr('ai_cull_assistant.roi_features.refresh_asset_roi_features', refresh)
    job = _job(scanned)
    assert job.run(OPTIONS, CropSettings(), stop, None) is None
    assert _published(workspace) == before
    monkeypatch.setattr('ai_cull_assistant.roi_features.refresh_asset_roi_features', _refresh)
    restored = load_job(workspace, photos)
    assert restored.run(OPTIONS, CropSettings(), Event(), None) is not None


def test_roi_failure_leaves_live_session_and_groups_unchanged(scanned, monkeypatch):
    _, workspace, first = scanned
    before = _published(workspace)
    original = deepcopy(first.assets[0].subject_features)
    def fail(asset, crops):
        asset.subject_features = None
        raise OSError('missing pixels')
    monkeypatch.setattr('ai_cull_assistant.roi_features.refresh_asset_roi_features', fail)
    with pytest.raises(OSError, match='missing pixels'):
        _job(scanned).run(OPTIONS, CropSettings(), Event(), None)
    assert _published(workspace) == before
    assert first.assets[0].subject_features == original


def test_publication_failure_rolls_back_groups_and_session(scanned, monkeypatch):
    _, workspace, _ = scanned
    before = _published(workspace)
    def fail(*args, **kwargs):
        raise OSError('session write failed')
    monkeypatch.setattr('ai_cull_assistant.processing_job.save_session', fail)
    with pytest.raises(OSError, match='session write failed'):
        _job(scanned).run(OPTIONS, CropSettings(), Event(), None)
    assert _published(workspace) == before


def test_changed_crops_refuse_stale_rescan_checkpoint(scanned):
    _, workspace, _ = scanned
    before = _published(workspace)
    job = _job(scanned)
    with pytest.raises(ProcessingJobError, match='设置已变化'):
        job.run(OPTIONS, CropSettings(scale_factor=1.5), Event(), None)
    assert _published(workspace) == before


def test_changed_source_refuses_stale_rescan_without_publishing(scanned):
    photos, workspace, _ = scanned
    before = _published(workspace)
    job = _job(scanned)
    (photos/'P0.jpg').write_bytes(b'changed')
    with pytest.raises(SourceChangedError):
        job.run(OPTIONS, CropSettings(), Event(), None)
    assert _published(workspace) == before


def test_unrequested_rescan_regroup_keeps_historical_behavior(scanned, monkeypatch):
    photos, workspace, result = scanned
    before = (workspace/'groups.json').read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('No regroup requested')
    monkeypatch.setattr('ai_cull_assistant.roi_features.refresh_asset_roi_features', forbidden)
    monkeypatch.setattr('ai_cull_assistant.processing_job.assign_groups', forbidden)
    followup = start_job(photos, workspace, OPTIONS, CropSettings(), result=result, mode='rescan')
    assert followup.run(OPTIONS, CropSettings(), Event(), None) is not None
    assert (workspace/'groups.json').read_bytes() == before


def test_crop_snapshot_accepts_mapping_and_tuple_json_roundtrip(scanned):
    photos, workspace, first = scanned
    settings = {'photos': {str(first.assets[0].primary_path.resolve()).casefold(): {
        'selected_faces': [(0.1, 0.1, 0.2, 0.2)]}}}
    job = start_job(photos, workspace, OPTIONS, settings, result=first, mode='rescan', regroup=True)
    assert job.run(OPTIONS, settings, Event(), None) is not None


def test_real_pixels_roi_edit_split_merge_and_reopen(tmp_path, monkeypatch):
    """No mocks for extraction, pair decisions, components, publication or reload."""
    import numpy as np
    from ai_cull_assistant.grouping import hamming_distance
    # Candidate inference is irrelevant: this task uses explicit face/body boxes.
    monkeypatch.setattr('ai_cull_assistant.subject.detect', lambda *args, **kwargs: [])
    monkeypatch.setattr('ai_cull_assistant.body_focus.detect_body_candidates', lambda *args, **kwargs: [])
    photos, workspace = tmp_path/'photos', tmp_path/'workspace'
    photos.mkdir()
    stripe = np.concatenate([np.linspace(15, 240, 100), np.linspace(240, 15, 100)]).astype('uint8')
    pixels = np.repeat(np.tile(stripe, (200, 1))[:, :, None], 3, axis=2)
    for name in ('a', 'b'):
        Image.fromarray(pixels).save(photos/f'{name}.png')
    left, right = [.05, .4, .4, .5], [.55, .4, .4, .5]
    overrides = {str((photos/f'{name}.png').resolve()).casefold(): {
        'selected_faces': [[.4, .05, .2, .2]], 'selected_bodies': [left]}
        for name in ('a', 'b')}
    crops = CropSettings(photos=deepcopy(overrides))
    first = start_job(photos, workspace, OPTIONS, crops, mode='scan').run(OPTIONS, crops, Event(), None)
    assert len({a.group_id for a in first.assets}) == 1
    assert first.assets[0].subject_features.body == first.assets[1].subject_features.body
    original = _published(workspace)
    edited = deepcopy(overrides)
    edited[crops.key(first.assets[1])]['selected_bodies'] = [right]
    changed_crops = CropSettings(photos=edited)
    first.assets[1].ai_focus_dirty = True
    job = start_job(photos, workspace, OPTIONS, changed_crops, result=first, mode='rescan', regroup=True)
    # Creating the ROI task does not yet publish any membership or feature.
    assert _published(workspace) == original
    second = job.run(OPTIONS, changed_crops, Event(), None)
    assert len({a.group_id for a in second.assets}) == 2
    assert hamming_distance(second.assets[0].subject_features.body, second.assets[1].subject_features.body) > 20
    assert all(a.subject_features.feature_schema == FEATURE_SCHEMA for a in second.assets)
    reopened = load_session(workspace, photos)
    assert [a.group_id for a in reopened.assets] == [a.group_id for a in second.assets]
    assert [a.subject_features.body for a in reopened.assets] == [a.subject_features.body for a in second.assets]
    reopened.assets[1].ai_focus_dirty = True
    restored = start_job(photos, workspace, OPTIONS, crops, result=reopened, mode='rescan', regroup=True).run(
        OPTIONS, crops, Event(), None)
    assert len({a.group_id for a in restored.assets}) == 1
    assert len({a.group_id for a in load_session(workspace, photos).assets}) == 1


def test_rescan_source_change_in_preview_preparation_never_rewrites_live(scanned, monkeypatch):
    from ai_cull_assistant import processing_job
    photos, workspace, first = scanned
    first.assets[0].ai_focus_dirty = True
    before = _published(workspace)
    previews = {a.preview_path: a.preview_path.read_bytes() for a in first.assets}
    real = processing_job.ensure_preview
    def replace_source_then_ensure(asset, preview_dir=None):
        Image.new('RGB', (160, 120), 'red').save(photos/'P0.jpg')
        return real(asset, preview_dir)
    monkeypatch.setattr(processing_job, 'ensure_preview', replace_source_then_ensure)
    with pytest.raises(SourceChangedError):
        _job(scanned).run(OPTIONS, CropSettings(), Event(), None)
    assert _published(workspace) == before
    assert all(path.read_bytes() == contents for path, contents in previews.items())


def test_stop_during_real_group_pairing_is_resumable_without_publication(scanned, monkeypatch):
    from ai_cull_assistant import grouping
    photos, workspace, first = scanned
    before = _published(workspace)
    memberships = [a.group_id for a in first.assets]
    stop = Event()
    compared = []
    real_similar = grouping._similar
    def stop_after_real_pair(*args, **kwargs):
        decision = real_similar(*args, **kwargs)
        compared.append(decision)
        stop.set()
        return decision
    monkeypatch.setattr(grouping, '_similar', stop_after_real_pair)
    job = _job(scanned)
    assert job.run(OPTIONS, CropSettings(), stop, None) is None
    assert compared
    assert job._data['grouping_complete'] is False
    assert [a.group_id for a in job.assets] == memberships
    assert _published(workspace) == before
    resumed = load_job(workspace, photos)
    assert resumed._data['grouping_complete'] is False
    monkeypatch.setattr(grouping, '_similar', real_similar)
    assert resumed.run(OPTIONS, CropSettings(), Event(), None) is not None
    assert load_session(workspace, photos) is not None


def test_paused_grouping_checkpoint_refuses_new_manual_memberships(scanned):
    photos, workspace, first = scanned
    job = _job(scanned)
    stop = Event()
    def pause_after_grouping(value):
        if job._data['grouping_complete']:
            stop.set()
    assert job.run(OPTIONS, CropSettings(), stop, pause_after_grouping) is None
    assert job._data['grouping_complete'] is True
    for index, asset in enumerate(first.assets):
        asset.group_id = 31+index
    persist_manual_groups(first)
    after_manual_edit = _published(workspace)
    with pytest.raises(ProcessingJobError, match='分组发生变化'):
        load_job(workspace, photos).run(OPTIONS, CropSettings(), Event(), None)
    assert _published(workspace) == after_manual_edit
    assert set(json.loads((workspace/'groups.json').read_text())['groups']) == {'31', '32', '33'}
    assert load_job(workspace, photos) is None
    # No workspace reset is required to restart from the new manual groups.
    replacement = _job(scanned)
    result = replacement.run(OPTIONS, CropSettings(), Event(), None)
    assert [a.group_id for a in result.assets] == [31, 32, 33]


def test_manual_edit_during_regroup_is_rechecked_before_publication(scanned, monkeypatch):
    from ai_cull_assistant import processing_job
    _, workspace, first = scanned
    before_session = (workspace/'scan-session.json').read_bytes()
    real_assign = processing_job.assign_groups
    def concurrent_edit(*args, **kwargs):
        result = real_assign(*args, **kwargs)
        for index, asset in enumerate(first.assets):
            asset.group_id = 41+index
        persist_manual_groups(first)
        return result
    monkeypatch.setattr(processing_job, 'assign_groups', concurrent_edit)
    with pytest.raises(ProcessingJobError, match='分组发生变化'):
        _job(scanned).run(OPTIONS, CropSettings(), Event(), None)
    assert (workspace/'scan-session.json').read_bytes() == before_session
    assert set(json.loads((workspace/'groups.json').read_text())['groups']) == {'41','42','43'}
