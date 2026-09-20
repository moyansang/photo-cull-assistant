import json
from pathlib import Path
import shutil
from threading import Event

import pytest

from ai_cull_assistant.ai_project import ReviewProject, fingerprint, photo_id
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.project_storage import save_workspace_preferences, workspace_for
from ai_cull_assistant.session_store import load_session
from ai_cull_assistant.source_relocation import relocate_source, recover_relocation
from test_workspace_archive import _completed_workspace


def test_relocate_archived_workspace_preserves_ai_and_crops(tmp_path):
    from ai_cull_assistant.workspace_archive import compact_workspace
    photos, workspace, result, project, task, batch, export, crops = _completed_workspace(tmp_path)
    asset = result.assets[0]
    crops.photos[crops.key(asset)] = {'offset_x_factor': .1, 'manual_face': [.2, .2, .3, .4]}
    project.refresh(result.assets, crops)
    task = project.create_task(result.assets, crops, {})
    batch = task['batches'][0]
    answer = dict(task_id=task['id'], batch_id=batch['id'], photos=[dict(
        photo_id=pid, rating=4, suggest_reject=False, reason='清晰', review_items=[])
        for pid in batch['photo_ids']])
    assert project.ingest(task, batch, json.dumps(answer)) == []
    save_workspace_preferences(workspace, crops.__dict__, {})
    before_id, before_fp = photo_id(asset), fingerprint(asset, crops)
    assert compact_workspace(workspace)['compacted']
    new_source = tmp_path / 'G-drive' / photos.name
    new_source.parent.mkdir()
    shutil.copytree(photos, new_source)
    copied = tmp_path / 'other-computer-workspace'
    shutil.copytree(workspace, copied)
    stats = relocate_source(copied, new_source, tmp_path / 'settings')
    assert stats['matched'] == 1
    restored = load_session(copied, new_source)
    new_crops = CropSettings.from_dict(json.loads((copied/'workspace-settings.json').read_text())['face_crop'])
    assert new_crops.photos[new_crops.key(restored.assets[0])]['offset_x_factor'] == .1
    assert photo_id(restored.assets[0]) == before_id
    assert fingerprint(restored.assets[0], new_crops) == before_fp
    reopened = ReviewProject(copied)
    reopened.refresh(restored.assets, new_crops)
    assert all(not row['stale'] and row['ai']['rating'] == 4 for row in reopened.data['photos'].values())
    current = reopened.current_task()
    assert current['id'] == task['id']
    assert all(p.is_file() for p in reopened.batch_images(current, current['batches'][0]))
    # Rebinding back again also preserves IDs/signatures, not just the first move.
    relocate_source(copied, photos, tmp_path / 'settings')
    again = load_session(copied, photos)
    again_crops = CropSettings.from_dict(json.loads((copied/'workspace-settings.json').read_text())['face_crop'])
    assert photo_id(again.assets[0]) == before_id
    assert fingerprint(again.assets[0], again_crops) == before_fp
    assert (workspace/'.workspace-archive.zip').is_file()


def test_paused_scan_journal_moves_and_resumes(tmp_path):
    from test_processing_job import _photos, _options
    from ai_cull_assistant.processing_job import start_job, load_job
    photos = _photos(tmp_path, 3)
    workspace = tmp_path / 'workspace'
    workspace_for(tmp_path/'settings', photos, preferred=workspace)
    crops = CropSettings()
    job = start_job(photos, workspace, _options(), crops, mode='scan')
    stop = Event()
    assert job.run(_options(), crops, stop, lambda value: stop.set() if value else None) is None
    assert job._data['completed_photos'] > 0
    copied = tmp_path / 'copied'
    shutil.copytree(workspace, copied)
    new = tmp_path / 'new-photos'
    shutil.copytree(photos, new)
    relocate_source(copied, new, tmp_path/'new-settings')
    resumed = load_job(copied, new)
    assert resumed is not None
    assert resumed._data['completed_photos'] == job._data['completed_photos']
    result = resumed.run(_options(), crops, Event(), None)
    assert len(result.assets) == 3
    assert all(a.primary_path.is_relative_to(new) for a in result.assets)


def test_mismatched_source_does_not_rewrite_state(tmp_path):
    photos, workspace, *_ = _completed_workspace(tmp_path)
    new = tmp_path/'wrong'; shutil.copytree(photos, new)
    next(new.iterdir()).write_bytes(b'wrong photograph')
    before = (workspace/'scan-session.json').read_bytes()
    with pytest.raises(ValueError, match='照片信息不一致'):
        relocate_source(workspace, new, tmp_path/'settings')
    assert (workspace/'scan-session.json').read_bytes() == before
    assert not (workspace/'.source-relocation-transaction.json').exists()


def test_failed_publication_rolls_back(tmp_path, monkeypatch):
    from ai_cull_assistant import source_relocation as module
    photos, workspace, *_ = _completed_workspace(tmp_path)
    new = tmp_path/'new'; shutil.copytree(photos, new)
    before = {p: p.read_bytes() for p in workspace.glob('*.json')}
    original_atomic = module._atomic
    def fail(path, value):
        if Path(path).name == 'groups.json':
            raise OSError('simulated write failure')
        original_atomic(path, value)
    monkeypatch.setattr(module, '_atomic', fail)
    with pytest.raises(OSError):
        relocate_source(workspace, new, tmp_path/'settings')
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not (workspace/'.source-relocation-transaction.json').exists()


def test_unavailable_drive_does_not_destroy_paused_job(tmp_path):
    from test_processing_job import _photos, _options
    from ai_cull_assistant.processing_job import start_job
    source = _photos(tmp_path, 1)
    settings = tmp_path/'settings'
    workspace = workspace_for(settings, source)
    start_job(source, workspace, _options(), CropSettings(), mode='scan')
    source.rename(tmp_path/'disconnected')
    active = workspace/'cache/processing/active.json'
    if not active.exists(): active = workspace/'.processing/active.json'
    before = active.read_bytes()
    with pytest.raises(ValueError, match='重新定位'):
        workspace_for(settings, source, preferred=workspace)
    assert active.read_bytes() == before


def test_relocation_ui_keeps_migrated_crop_settings(tmp_path, monkeypatch):
    import time
    from dataclasses import asdict
    from test_app_workspaces import make_app
    photos, workspace, result, *_ = _completed_workspace(tmp_path)
    crops = CropSettings()
    crops.photos[crops.key(result.assets[0])] = {'offset_x_factor': .25}
    save_workspace_preferences(workspace, asdict(crops), {})
    app = make_app(tmp_path/'settings', photos, workspace)
    new = tmp_path/'G-drive-photos'
    photos.rename(new)
    monkeypatch.setattr('ai_cull_assistant.app.filedialog.askdirectory', lambda **kw: str(new))
    monkeypatch.setattr('ai_cull_assistant.app.messagebox.askyesno', lambda *a, **kw: True)
    errors = []
    monkeypatch.setattr('ai_cull_assistant.app.messagebox.showerror', lambda *a, **kw: errors.append(a))
    try:
        app._relink_source()
        deadline = time.monotonic()+10
        while getattr(app, '_relocating_source', False) and time.monotonic() < deadline:
            app.update(); time.sleep(.01)
        assert not app._relocating_source and not errors
        assert app.input_var.get() == str(new)
        assert app.scan_result and len(app.scan_result.assets) == 1
        key = app.crop_settings.key(app.scan_result.assets[0])
        assert app.crop_settings.photos[key]['offset_x_factor'] == .25
        assert not app._processing_busy
    finally:
        app._close()


def test_unfinished_focus_and_source_copy_preserve_reviewed_results(tmp_path):
    from test_processing_job import _options
    from ai_cull_assistant.processing_job import start_job, load_job
    photos, workspace, result, *_ = _completed_workspace(tmp_path)
    job = start_job(photos, workspace, _options(), CropSettings(), result=result, mode='focus')
    job.assets[0].ai_focus_result = {'status': 'clear', 'reason': 'original face is sharp'}
    job._data['completed_photos'] = 1
    job._persist()
    new = tmp_path/'new-source'; shutil.copytree(photos, new)
    copied = tmp_path/'copied'; shutil.copytree(workspace, copied)
    relocate_source(copied, new, tmp_path/'settings')
    restored = load_job(copied, new)
    assert restored._data['completed_photos'] == 1
    assert restored.assets[0].ai_focus_result == job.assets[0].ai_focus_result
    assert restored.assets[0].primary_path.is_relative_to(new)
