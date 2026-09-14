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


def test_close_archives_and_reopen_restores_without_scan(tmp_path):
    photos, workspace, _result, _project, _task, _batch, export, _crops = _completed_workspace(tmp_path)
    config = tmp_path / "settings"
    save_values(config, {"input": str(photos), "workspace": str(workspace)})
    app = App(settings_dir=config)
    app.withdraw()
    assert app.scan_result is not None
    app._close()
    assert (workspace / ".workspace-archive.zip").is_file()
    assert export.is_file()
    assert not (workspace / "previews").exists()

    reopened = App(settings_dir=config)
    reopened.withdraw()
    try:
        assert reopened.scan_result is not None
        assert not (workspace / ".workspace-archive.zip").exists()
        assert reopened._sheets_ready()
        assert not Path(reopened.scan_result.assets[0].preview_path).exists()
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

    assert (workspace / ".workspace-archive.zip").is_file()
    assert not (workspace / "previews").exists()
    reopened = App(settings_dir=config)
    reopened.withdraw()
    try:
        assert reopened.scan_result is not None
        assert reopened.scan_result.assets[0].group_id == 23
        assert reopened.scan_result.assets[0].subject_features.face == (0.2, 0.1, 0.4, 0.5)
        assert reopened.crop_settings.scale_factor == 1.35
        assert reopened.crop_settings.shift_factor == -0.2
        assert reopened._next_step_after_restore() == "检测/调整人脸框"
    finally:
        reopened._close()


def test_close_logs_why_active_workspace_was_not_compacted(tmp_path):
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
    assert "工作区未整理：工作区仍有未完成任务" in (workspace / "logs" / "session.log").read_text("utf-8")
