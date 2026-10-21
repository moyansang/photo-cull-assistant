"""W04: production publication cannot re-sign unmeasured historical pixels."""
from copy import deepcopy
import json
from threading import Event

from PIL import Image
import pytest

from test_scan_review_integration import demo
from ai_cull_assistant.processing_job import start_job, load_job
from ai_cull_assistant.session_store import load_session, save_session


def inventory(result):
    return json.loads((result.workspace_dir / 'scan-session.json').read_text('utf-8'))['source_stats']


def replaced_scan(tmp_path):
    photos = tmp_path / 'synthetic'
    demo.create_inputs(photos)
    result, crops, options = demo.production_scan(photos, tmp_path / 'workspace')
    old = inventory(result)
    target = next(a for a in result.assets if a.screening_reason != 'decode_unavailable')
    with Image.new('RGB', (520, 720), 'red') as replacement:
        replacement.save(target.primary_path)
    restored = load_session(result.workspace_dir, photos, preserve_unavailable=True)
    return result, restored, target, crops, options, old


@pytest.mark.parametrize("legacy_checkpoint", [False, True])
def test_sheets_resume_preserves_changed_inventory_and_core(tmp_path, legacy_checkpoint):
    before, restored, target, crops, options, old = replaced_scan(tmp_path)
    old_core = json.loads(json.dumps([a.clarity_evidence for a in restored.assets]))
    job = start_job(restored.input_dir, restored.workspace_dir, options, crops,
                    result=restored, mode='sheets')
    assert job._data['work_indices'] == []
    stop = Event(); stop.set()
    assert job.run(options, crops, stop, None) is None
    if legacy_checkpoint:
        job._data.pop('measurement_source_stats')
        job._persist()
    job = load_job(restored.workspace_dir, restored.input_dir)
    published = job.run(options, crops, Event(), None)
    assert published is not None
    assert inventory(published) == old
    assert json.loads(json.dumps([a.clarity_evidence for a in published.assets])) == old_core
    assert json.loads((published.workspace_dir / '.source-location.json').read_text('utf-8'))['files'][target.primary_path.name] == old[str(target.primary_path.resolve())]
    with pytest.raises(ValueError, match='已修改'):
        load_session(published.workspace_dir, published.input_dir)


def test_manual_and_fresh_shared_saves_reject_stale_without_rewriting(tmp_path):
    _, restored, _, _, _, old = replaced_scan(tmp_path)
    from ai_cull_assistant.workflow import persist_manual_groups
    for asset in restored.assets:
        asset.group_id = 99
    persist_manual_groups(restored)
    session = restored.workspace_dir / 'scan-session.json'
    before = session.read_bytes()
    for fresh in (False, True):
        with pytest.raises(ValueError, match='原片已变化'):
            save_session(restored, fresh=fresh)
        assert session.read_bytes() == before
    assert inventory(restored) == old
    # The real App save adapter catches errors and must report, not re-sign.
    from types import SimpleNamespace
    from ai_cull_assistant.app import App
    messages = []
    app = SimpleNamespace(scan_result=restored, _workspace_blocked=False, _log=messages.append)
    App._save_session(app)
    assert session.read_bytes() == before
    assert len(messages) == 1 and '分析记录保存失败' in messages[0] and '原片已变化' in messages[0]


def test_explicit_single_recheck_updates_only_successfully_checked_member(tmp_path):
    _, restored, target, crops, options, old = replaced_scan(tmp_path)
    from ai_cull_assistant.image_decode import asset_source_signature
    from ai_cull_assistant.scan_review_actions import prepare_scope
    from ai_cull_assistant.scan_review_store import asset_key
    other = next(a for a in restored.assets if a.primary_path != target.primary_path and a.screening_reason != 'decode_unavailable')
    with Image.new('RGB', (530, 710), 'blue') as replacement:
        replacement.save(other.primary_path)
    restored = load_session(restored.workspace_dir, restored.input_dir, preserve_unavailable=True)
    target = next(a for a in restored.assets if a.primary_path == target.primary_path)
    region = dict(kind='face', box=[.3,.25,.6,.6], coordinate_space='normalized_display',
                  preview_version='v05', source_path=str(target.primary_path),
                  source_signature=json.loads(json.dumps(asset_source_signature(target))))
    changed, context = prepare_scope(restored, target, crops, region)
    copied = deepcopy(restored)
    next(a for a in copied.assets if asset_key(a) == asset_key(target)).ai_focus_dirty = True
    job = start_job(restored.input_dir, restored.workspace_dir, options, changed,
                    result=copied, mode='rescan', scan_review=context)
    published = job.run(options, changed, Event(), None)
    after = inventory(published)
    key = str(target.primary_path.resolve())
    assert after[key] != old[key]
    assert after == dict(old, **{key: after[key]})
    assert after[str(other.primary_path.resolve())] == old[str(other.primary_path.resolve())]
    with pytest.raises(ValueError, match='已修改'):
        load_session(published.workspace_dir, published.input_dir)


def test_fresh_normal_scan_still_accepts_changed_sources(tmp_path):
    _, restored, target, crops, options, old = replaced_scan(tmp_path)
    with pytest.raises(ValueError, match='已修改'):
        load_session(restored.workspace_dir, restored.input_dir)
    published = start_job(restored.input_dir, restored.workspace_dir, options, crops,
                          mode='scan').run(options, crops, Event(), None)
    assert inventory(published)[str(target.primary_path.resolve())] != old[str(target.primary_path.resolve())]
    assert load_session(published.workspace_dir, published.input_dir) is not None


def test_sheets_missing_member_keeps_original_inventory(tmp_path):
    _, restored, target, crops, options, old = replaced_scan(tmp_path)
    target.primary_path.unlink()
    restored = load_session(restored.workspace_dir, restored.input_dir, preserve_unavailable=True)
    job = start_job(restored.input_dir, restored.workspace_dir, options, crops,
                    result=restored, mode='sheets')
    published = job.run(options, crops, Event(), None)
    assert inventory(published) == old
    missing = next(a for a in published.assets if a.primary_path == target.primary_path)
    assert 'source_unavailable' in missing.notes


def test_focus_only_does_not_resign_old_local_measurement(tmp_path, monkeypatch):
    _, restored, _, crops, options, old = replaced_scan(tmp_path)
    from ai_cull_assistant import ai_focus
    monkeypatch.setattr(ai_focus, 'review_focus', lambda *args, **kwargs: pytest.fail('No paid review authorized'))
    job = start_job(restored.input_dir, restored.workspace_dir, options, crops,
                    result=restored, mode='focus')
    published = job.run(options, crops, Event(), None, focus_profile={'id': 'synthetic'})
    assert published is not None
    assert inventory(published) == old
    with pytest.raises(ValueError, match='已修改'):
        load_session(published.workspace_dir, published.input_dir)
