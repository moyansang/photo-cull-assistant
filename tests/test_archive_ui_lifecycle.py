from pathlib import Path
from threading import Event

from PIL import Image

from ai_cull_assistant.app import App
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.processing_job import start_job
from ai_cull_assistant.settings import save_values
from ai_cull_assistant.session_store import load_session, save_session
from ai_cull_assistant.subject import SubjectFeatures
from ai_cull_assistant.workflow import persist_manual_groups, run_scan
from ai_cull_assistant.workspace_archive import compact_workspace, restore_workspace
from test_workspace_archive import _completed_workspace


def test_close_keeps_active_state_and_reopen_without_scan(tmp_path):
    photos, workspace, _result, _project, _task, _batch, export, _crops = _completed_workspace(tmp_path)
    config = tmp_path / "settings"
    save_values(config, {"input": str(photos), "workspace": str(workspace)})
    app = App(settings_dir=config)
    app.withdraw()
    assert app.scan_result is not None
    app._close()
    assert not (workspace / ".workspace-archive.zip").exists()
    assert export.is_file()
    assert (workspace / "previews").exists()

    reopened = App(settings_dir=config)
    reopened.withdraw()
    try:
        assert reopened.scan_result is not None
        assert not (workspace / ".workspace-archive.zip").exists()
        assert reopened._sheets_ready()
        assert Path(reopened.scan_result.assets[0].preview_path).exists()
    finally:
        reopened._close()


def test_sheets_after_archive_rebuild_previews_without_analysis(tmp_path, monkeypatch):
    photos, workspace, _result, _project, _task, _batch, _export, crops = _completed_workspace(tmp_path)
    assert compact_workspace(workspace)["compacted"]
    restore_workspace(workspace)
    result = load_session(workspace, photos)
    def forbidden(*args, **kwargs):
        raise AssertionError("Rendering restored previews must not scan or request AI")
    monkeypatch.setattr("ai_cull_assistant.processing_job.screen_assets", forbidden)
    monkeypatch.setattr("ai_cull_assistant.ai_focus.review_focus", forbidden)
    options = {"technical_screening": False, "columns": 4, "photos_per_page": 16}
    job = start_job(photos, workspace, options, crops, result=result, mode="sheets")
    rendered = job.run(options, crops, Event(), None)
    assert rendered.main_pages and all(path.is_file() for path in rendered.main_pages)
    assert Path(rendered.assets[0].preview_path).is_file()


def test_close_after_scan_only_restores_groups_and_face_settings(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (120, 180), "white").save(photos / "A.jpg")
    workspace = tmp_path / "workspace"
    config = tmp_path / "settings"
    save_values(config, {"input": str(photos), "workspace": str(workspace)})

    app = App(settings_dir=config)
    app.withdraw()
    options = {
        "technical_screening": False,
        "grouping_preset": "standard",
        "columns": 4,
        "photos_per_page": 16,
    }
    job = start_job(photos, workspace, options, CropSettings(), mode="scan")
    result = job.run(options, CropSettings(), Event(), None)
    assert result.main_pages == [] and result.rejected_pages == []
    assert not result.contact_dir.exists()
    app.scan_result = result
    app.scan_result.assets[0].group_id = 23
    app.scan_result.assets[0].subject_features = SubjectFeatures(
        whole="whole", center="center", body=None,
        face=(0.2, 0.1, 0.4, 0.5), head=None, head_source="manual",
    )
    persist_manual_groups(app.scan_result)
    app.crop_settings = CropSettings(scale_factor=1.35, shift_factor=-0.2)
    app._close()

    assert not (workspace / ".workspace-archive.zip").exists()
    assert (workspace / "previews").exists()
    reopened = App(settings_dir=config)
    reopened.withdraw()
    try:
        assert reopened.scan_result is not None
        assert reopened.scan_result.assets[0].group_id == 23
        assert reopened.scan_result.assets[0].subject_features.face == (0.2, 0.1, 0.4, 0.5)
        assert reopened.crop_settings.scale_factor == 1.35
        assert reopened.crop_settings.shift_factor == -0.2
        from ai_cull_assistant.workspace_summary import focus_counts
        assert focus_counts(reopened.scan_result.assets)['pending'] == 1
        assert reopened._next_step_after_restore() == "AI 复核"
    finally:
        reopened._close()


def test_close_keeps_active_job_without_attempting_archive(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (120, 180), "white").save(photos / "A.jpg")
    workspace = tmp_path / "workspace"
    result = run_scan(photos, workspace, technical_screening=False)
    save_session(result)
    active = workspace / "cache" / "processing" / "active.json"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text('{"job_id":"pending"}', encoding="utf-8")
    config = tmp_path / "settings"
    save_values(config, {"input": str(photos), "workspace": str(workspace)})

    app = App(settings_dir=config)
    app.withdraw()
    app._close()

    assert not (workspace / ".workspace-archive.zip").exists()
    assert active.read_text("utf-8") == '{"job_id":"pending"}'
    assert "工作区未整理" not in (workspace / "logs" / "session.log").read_text("utf-8")


def test_legacy_archive_restores_once_and_repeated_close_keeps_active_state(tmp_path,monkeypatch):
    import json
    photos,workspace,_,project,task,_,export,_=_completed_workspace(tmp_path)
    original=json.loads((workspace/'ai_project.json').read_text('utf-8'))
    assert compact_workspace(workspace)['compacted']
    config=tmp_path/'settings';save_values(config,{'input':str(photos),'workspace':str(workspace)})
    def forbidden(*args,**kwargs):raise AssertionError('Automatic archive must not run')
    monkeypatch.setattr('ai_cull_assistant.workspace_archive.compact_workspace',forbidden)
    monkeypatch.setattr('ai_cull_assistant.processing_job.screen_assets',forbidden)
    for _ in range(3):
        app=App(settings_dir=config);app.withdraw()
        try:
            assert app.scan_result is not None and app._sheets_ready()
            assert json.loads((workspace/'ai_project.json').read_text('utf-8'))==original
            assert export.is_file()
        finally:app._close()
        assert (workspace/'scan-session.json').is_file()
        assert not (workspace/'.workspace-archive.zip').exists()


def test_switch_saves_preferences_and_preserves_old_cache_and_results(tmp_path,monkeypatch):
    (tmp_path/'first').mkdir();(tmp_path/'second').mkdir()
    first,old,*_=_completed_workspace(tmp_path/'first')
    second,new,*_=_completed_workspace(tmp_path/'second')
    config=tmp_path/'settings';save_values(config,{'input':str(first),'workspace':str(old)})
    before=(old/'ai_project.json').read_bytes()
    def forbidden(*args,**kwargs):raise AssertionError('Automatic archive must not run')
    monkeypatch.setattr('ai_cull_assistant.workspace_archive.compact_workspace',forbidden)
    app=App(settings_dir=config);app.withdraw()
    try:
        app.crop_settings=CropSettings(scale_factor=1.45)
        app.input_var.set(str(second));app._activate_workspace(str(second),new)
        assert (old/'scan-session.json').exists() and (old/'previews').is_dir()
        assert (old/'ai_project.json').read_bytes()==before
        assert not (old/'.workspace-archive.zip').exists()
        app.input_var.set(str(first));app._activate_workspace(str(first),old)
        assert app.crop_settings.scale_factor==1.45 and app.scan_result is not None
    finally:app._close()
    assert not (new/'.workspace-archive.zip').exists()


def test_close_save_failure_keeps_last_checkpoint_and_cache(tmp_path,monkeypatch):
    photos,workspace,*_=_completed_workspace(tmp_path)
    config=tmp_path/'settings';save_values(config,{'input':str(photos),'workspace':str(workspace)})
    app=App(settings_dir=config);app.withdraw()
    before=(workspace/'scan-session.json').read_bytes()
    def failed(*args,**kwargs):raise OSError('synthetic save failure')
    monkeypatch.setattr('ai_cull_assistant.session_store.save_session',failed)
    app._close()
    assert (workspace/'scan-session.json').read_bytes()==before
    assert (workspace/'previews').is_dir() and not (workspace/'.workspace-archive.zip').exists()
    assert 'synthetic save failure' in (workspace/'logs/session.log').read_text('utf-8')


def test_switch_save_failure_preserves_selection_and_files(tmp_path,monkeypatch):
    photos,workspace,*_=_completed_workspace(tmp_path)
    config=tmp_path/'settings';save_values(config,{'input':str(photos),'workspace':str(workspace)})
    app=App(settings_dir=config);app.withdraw()
    second=tmp_path/'second';second.mkdir()
    before=(workspace/'scan-session.json').read_bytes()
    def failed(*args,**kwargs):raise OSError('synthetic preferences failure')
    with monkeypatch.context() as patch:
        patch.setattr('ai_cull_assistant.app.save_workspace_preferences',failed)
        app.input_var.set(str(second))
        assert not app._sync_selected_workspace()
        assert app._active_workspace==str(workspace) and app.input_var.get()==str(photos)
        assert (workspace/'scan-session.json').read_bytes()==before
        assert (workspace/'previews').is_dir() and not (workspace/'.workspace-archive.zip').exists()
    app._close()
