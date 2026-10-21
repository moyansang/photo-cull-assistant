"""Real pixels/production scan and shared review services, not mock verdicts."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('demo_scan_review', ROOT/'tools/demo_scan_review.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.fixture
def scanned(tmp_path):
    photos = tmp_path/'照片 inputs'
    demo.create_inputs(photos)
    before = demo.source_hashes(photos)
    result, crops, options = demo.production_scan(photos, tmp_path/'工作区 workspace')
    yield result, crops, options
    assert demo.source_hashes(photos) == before


def test_real_scan_all_inputs_feedback_decisions_groups_reopen_export(scanned, monkeypatch):
    result, crops, _ = scanned
    import ai_cull_assistant.ai_api as ai_api
    import ai_cull_assistant.screening as screening
    import ai_cull_assistant.analysis_modes as analysis
    import ai_cull_assistant.face_focus as focus

    def forbidden(*args, **kwargs):
        raise AssertionError('Viewing/feedback/decisions must not score or call AI')

    # The initial scan above is real. Only forbid accidental additional work.
    monkeypatch.setattr(ai_api, 'call_model', forbidden)
    monkeypatch.setattr(ai_api, '_open_request', forbidden)
    monkeypatch.setattr(screening, 'screen_assets', forbidden)
    monkeypatch.setattr(analysis, 'assess_asset_mode', forbidden)
    monkeypatch.setattr(focus, 'assess_asset_focus', forbidden)
    assert len(result.assets) == 4
    report = demo.exercise_review(result, crops)
    assert all(report.values())


def test_current_production_matches_received_baseline(tmp_path):
    photos = tmp_path/'same inputs'
    demo.create_inputs(photos)
    report = demo.baseline_compare(photos, tmp_path)
    assert report['core_exact_equal'] is True
    assert report['before_sha256'] == report['after_sha256']


def test_real_source_viewport_no_scoring_and_exact_bytes(scanned, monkeypatch):
    from ai_cull_assistant import image_viewer, screening, ai_api
    from ai_cull_assistant.scan_review_viewer import recorded_overlays
    result, _, _ = scanned
    def forbidden(*args, **kwargs):
        raise AssertionError('Image browsing must not score or call AI')
    monkeypatch.setattr(screening, 'screen_assets', forbidden)
    monkeypatch.setattr(ai_api, 'call_model', forbidden)
    before = demo.core_results(result)
    for asset in result.assets:
        if asset.screening_reason == 'decode_unavailable':
            continue
        frame = image_viewer.load_source(asset)
        try:
            viewport = image_viewer.Viewport()
            viewport.set_image(frame.image.size)
            viewport.resize(640, 480)
            viewport.fit()
            viewport.native()
            assert viewport.scale == 1
            viewport.zoom_at(2, 100, 100)
            viewport.pan(10, 20)
            rendered = viewport.render(frame.image)
            rendered.close()
            overlays = recorded_overlays(asset, frame.image.info.get('decode_evidence') or {})
            assert any(item['role'] == 'selected' for item in overlays)
            assert all('eye' not in item.get('label', '') for item in overlays)
        finally:
            frame.close()
    assert demo.core_results(result) == before


def test_explicit_real_recheck_preserves_manual_groups_and_snapshots(scanned):
    from threading import Event
    from ai_cull_assistant.scan_review_actions import prepare_scope
    from ai_cull_assistant.scan_review_store import ScanReviewStore, asset_key
    from ai_cull_assistant.processing_job import start_job, load_job
    from ai_cull_assistant.workflow import persist_manual_groups
    from ai_cull_assistant.session_store import save_session, load_session
    from ai_cull_assistant.scan_review_decisions import ReviewDecisions
    from ai_cull_assistant.ai_project import photo_id
    result, crops, options = scanned
    for i, asset in enumerate(result.assets):
        asset.group_id = i+31
    persist_manual_groups(result)
    save_session(result)
    target = next(a for a in result.assets if a.screening_reason != 'decode_unavailable')
    decisions = ReviewDecisions(result.workspace_dir, result.assets, crops)
    decisions.rows()
    decisions.set(target, pick_status=1, rating=4)
    groups_before = (result.workspace_dir/'groups.json').read_bytes()
    session_before = (result.workspace_dir/'scan-session.json').read_bytes()
    from ai_cull_assistant.image_decode import asset_source_signature
    draft = dict(kind='face', box=[.30,.25,.60,.60], coordinate_space='normalized_display',
                 preview_version='v05', source_path=str(target.primary_path),
                 source_signature=json.loads(json.dumps(asset_source_signature(target))))
    changed, context = prepare_scope(result, target, crops, draft)
    assert (result.workspace_dir/'scan-session.json').read_bytes() == session_before
    copy = deepcopy(result)
    next(a for a in copy.assets if asset_key(a) == asset_key(target)).ai_focus_dirty = True
    job = start_job(result.input_dir, result.workspace_dir, options, changed,
                    result=copy, mode='rescan', regroup=False, scan_review=context)
    stop = Event()
    stop.set()
    assert job.run(options, changed, stop, None) is None
    assert (result.workspace_dir/'scan-session.json').read_bytes() == session_before
    assert ScanReviewStore(result.workspace_dir).photo(target)['rechecks'][-1]['status'] == 'pending'
    resumed = load_job(result.workspace_dir, result.input_dir)
    completed = resumed.run(dict(options, technical_screening=False), changed, Event(), None)
    assert resumed._data['last_options']['technical_screening'] is True
    assert completed is not None
    assert (result.workspace_dir/'groups.json').read_bytes() == groups_before
    operation = ScanReviewStore(result.workspace_dir).photo(target)['rechecks'][-1]
    assert operation['status'] == 'completed'
    assert operation['before'] != operation['after']
    from ai_cull_assistant.scan_review_viewer import load_inspection_source, recorded_overlays
    measured = next(a for a in completed.assets if asset_key(a) == asset_key(target))
    frame, _ = load_inspection_source(measured)
    try:
        overlays = recorded_overlays(measured, frame.image.info.get('decode_evidence') or {})
        assert any(item['role'] == 'confirmed' for item in overlays)
    finally:
        frame.close()
    reopened = load_session(result.workspace_dir, result.input_dir)
    rows = ReviewDecisions(result.workspace_dir, reopened.assets, changed).rows()
    assert rows[photo_id(target)]['final']['rating'] == 4
    assert rows[photo_id(target)]['final']['pick_status'] == 1
    history_before = ScanReviewStore(result.workspace_dir).read()
    with pytest.raises(ValueError, match='没有变化'):
        prepare_scope(completed, measured, changed, draft)
    assert ScanReviewStore(result.workspace_dir).read() == history_before


def test_missing_input_is_visible_failed_and_opening_does_not_write(scanned):
    from ai_cull_assistant.session_store import load_session
    from ai_cull_assistant.scan_review_ui import result_state
    result, _, _ = scanned
    target = next(a for a in result.assets if a.screening_reason != 'decode_unavailable')
    source = target.primary_path
    payload, stat = source.read_bytes(), source.stat()
    before = {p.name: p.read_bytes() for p in result.workspace_dir.glob('*.json')}
    try:
        source.unlink()
        reopened = load_session(result.workspace_dir, result.input_dir, preserve_unavailable=True)
        assert len(reopened.assets) == 4
        missing = next(a for a in reopened.assets if a.primary_path == source)
        assert result_state(missing) == 'failed'
        assert {p.name: p.read_bytes() for p in result.workspace_dir.glob('*.json')} == before
    finally:
        import os
        source.write_bytes(payload)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_old_drawn_region_rejects_replaced_source(scanned):
    import os
    from ai_cull_assistant.image_decode import asset_source_signature
    from ai_cull_assistant.scan_review_actions import prepare_scope
    from ai_cull_assistant.scan_review_store import ReviewConflict
    result, crops, _ = scanned
    target = next(a for a in result.assets if a.screening_reason != 'decode_unavailable')
    draft = dict(kind='face', box=[.30,.25,.60,.60], coordinate_space='normalized_display',
                 preview_version='v05', source_path=str(target.primary_path),
                 source_signature=json.loads(json.dumps(asset_source_signature(target))))
    source = target.primary_path
    payload, stat = source.read_bytes(), source.stat()
    before = (result.workspace_dir/'scan-session.json').read_bytes()
    try:
        source.write_bytes(payload+b'changed after the user drew this scope')
        with pytest.raises((ReviewConflict, ValueError), match='来源|原图|变化'):
            prepare_scope(result, target, crops, draft)
        assert (result.workspace_dir/'scan-session.json').read_bytes() == before
        assert not (result.workspace_dir/'scan-review.json').exists()
    finally:
        source.write_bytes(payload)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_app_scan_decision_invalidates_old_ai_page(scanned, monkeypatch):
    from types import SimpleNamespace
    from ai_cull_assistant.app import App
    from ai_cull_assistant import scan_review_ui
    result, crops, _ = scanned
    captured = {}
    destroyed = []
    old = SimpleNamespace(winfo_exists=lambda: True, dispose=lambda: destroyed.append(True))
    def page(*args, **kwargs):
        captured.update(kwargs)
        return object()
    monkeypatch.setattr(scan_review_ui, 'ScanReviewPage', page)
    app = SimpleNamespace(_processing_busy=False, updates=SimpleNamespace(busy=False),
        _review_busy=lambda:False, _sync_selected_workspace=lambda:True, scan_result=result,
        _show_workspace_page=lambda name:False, crop_settings=crops, _page_host=None,
        _workspace_pages={'review':old}, review_project=object(),
        _apply_scan_review_scope=lambda *a:None, _cancel_scan_review_recheck=lambda *a:None, _open_group_editor=lambda:None,
        _open_ai_review=lambda:None, _mount_workspace_page=lambda *a:None)
    App._open_scan_review(app)
    captured['on_read_decisions']()
    captured['on_decision'](result.assets[0], 1, 4)
    assert destroyed == [True] and app.review_project is None


def test_app_offline_history_route_is_available_without_sources(scanned, monkeypatch):
    from types import SimpleNamespace
    from ai_cull_assistant.app import App
    from ai_cull_assistant import scan_review_ui
    result, _, _ = scanned
    original = result.input_dir
    moved = original.with_name('temporarily offline fixture')
    captured = {}
    status = []
    def page(parent, assets, workspace, **kwargs):
        captured.update(assets=assets, workspace=workspace, **kwargs)
        return SimpleNamespace(status=SimpleNamespace(set=status.append))
    monkeypatch.setattr(scan_review_ui, 'ScanReviewPage', page)
    app = SimpleNamespace(workspace_var=SimpleNamespace(get=lambda:str(result.workspace_dir)),
                          _page_host=None, _mount_workspace_page=lambda *a:None)
    before = {p.name:p.read_bytes() for p in result.workspace_dir.glob('*.json')}
    try:
        original.rename(moved)
        assert App._open_offline_scan_review(app) is True
        assert len(captured['assets']) == 4
        assert 'on_apply_scope' not in captured and 'on_decision' not in captured
        assert captured['on_read_decisions']()
        assert status and '离线' in status[-1]
        assert {p.name:p.read_bytes() for p in result.workspace_dir.glob('*.json')} == before
    finally:
        moved.rename(original)


def test_two_process_feedback_writers_cannot_overwrite(scanned):
    import os
    import subprocess
    import sys
    from ai_cull_assistant.scan_review_store import ScanReviewStore
    result, _, _ = scanned
    code = r'''
import sys
from ai_cull_assistant.session_store import load_session
from ai_cull_assistant.scan_review_store import ScanReviewStore
result=load_session(sys.argv[1],sys.argv[2],preserve_unavailable=True)
try:
    ScanReviewStore(sys.argv[1]).record_feedback(result.assets[0],text=sys.argv[3],expected_revision=0)
except ValueError:
    raise SystemExit(8)
'''
    env = dict(os.environ, PYTHONPATH=str(ROOT/'src'), PYTHONDONTWRITEBYTECODE='1')
    processes = [subprocess.Popen([sys.executable, '-X', 'utf8', '-Werror', '-c', code,
                  str(result.workspace_dir), str(result.input_dir), text],
                  cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for text in ('writer one', 'writer two')]
    outcomes = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert not stderr, stderr.decode('utf-8')
        outcomes.append(process.returncode)
    assert sorted(outcomes) == [0, 8]
    saved = ScanReviewStore(result.workspace_dir).read()
    assert saved['revision'] == 1
    assert sum(len(row['feedback']) for row in saved['photos'].values()) == 1


def test_explicit_fresh_scope_can_recheck_a_changed_source(scanned):
    import os
    from threading import Event
    from ai_cull_assistant.image_decode import asset_source_signature
    from ai_cull_assistant.session_store import load_session
    from ai_cull_assistant.scan_review_actions import prepare_scope
    from ai_cull_assistant.processing_job import start_job
    from ai_cull_assistant.scan_review_store import ScanReviewStore
    result, crops, options = scanned
    original = next(a for a in result.assets if a.screening_reason != 'decode_unavailable')
    source = original.primary_path
    payload, stat = source.read_bytes(), source.stat()
    try:
        source.write_bytes(payload+b'changed before explicit fresh inspection')
        stale_result = load_session(result.workspace_dir, result.input_dir, preserve_unavailable=True)
        target = next(a for a in stale_result.assets if a.primary_path == source)
        assert target.ai_focus_dirty is True
        draft = dict(kind='face', box=[.30,.25,.60,.60], coordinate_space='normalized_display',
                     preview_version='v05', source_path=str(source),
                     source_signature=json.loads(json.dumps(asset_source_signature(target))))
        changed, context = prepare_scope(stale_result, target, crops, draft)
        job = start_job(result.input_dir, result.workspace_dir, options, changed,
                        result=stale_result, mode='rescan', scan_review=context)
        completed = job.run(options, changed, Event(), None)
        assert completed is not None
        assert ScanReviewStore(result.workspace_dir).photo(target)['rechecks'][-1]['status'] == 'completed'
    finally:
        source.write_bytes(payload)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_cancel_stale_pending_job_preserves_results_then_allows_fresh_recheck(scanned):
    from threading import Event
    from ai_cull_assistant.image_decode import asset_source_signature
    from ai_cull_assistant.scan_review_actions import prepare_scope
    from ai_cull_assistant.scan_review_store import ScanReviewStore, ReviewConflict
    from ai_cull_assistant.scan_review_cancel import cancel_recheck
    from ai_cull_assistant.processing_job import start_job, load_job
    result, crops, options = scanned
    target = next(a for a in result.assets if a.screening_reason != 'decode_unavailable')
    draft = dict(kind='face', box=[.30,.25,.60,.60], coordinate_space='normalized_display',
                 preview_version='v05', source_path=str(target.primary_path),
                 source_signature=json.loads(json.dumps(asset_source_signature(target))))
    unchanged = {p.name:p.read_bytes() for p in result.workspace_dir.glob('*.json')}
    changed, context = prepare_scope(result, target, crops, draft)
    job = start_job(result.input_dir, result.workspace_dir, options, changed,
                    result=result, mode='rescan', scan_review=context, regroup=False)
    stop = Event()
    stop.set()
    assert job.run(options, changed, stop, None) is None
    store = ScanReviewStore(result.workspace_dir)
    revised = dict(draft, box=[.20,.20,.70,.70])
    store.record_feedback(target, kind='region', region=revised)
    resumed = load_job(result.workspace_dir, result.input_dir)
    with pytest.raises(ReviewConflict):
        resumed.run(options, changed, Event(), None)
    assert cancel_recheck(result.workspace_dir, target, result.input_dir) is True
    from ai_cull_assistant.workspace_layout import workspace_path as resolve_workspace_path
    assert not (resolve_workspace_path(result.workspace_dir, '.processing')/'active.json').exists()
    assert store.photo(target)['rechecks'][-1]['status'] == 'interrupted'
    for name, payload in unchanged.items():
        assert (result.workspace_dir/name).read_bytes() == payload
    assert cancel_recheck(result.workspace_dir, target, result.input_dir) is False
    fresh, context = prepare_scope(result, target, crops, revised)
    new_job = start_job(result.input_dir, result.workspace_dir, options, fresh,
                       result=result, mode='rescan', scan_review=context, regroup=False)
    assert new_job.run(options, fresh, Event(), None) is not None
    assert [op['status'] for op in store.photo(target)['rechecks']] == ['interrupted', 'completed']
