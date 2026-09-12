from pathlib import Path
from threading import Event

from ai_cull_assistant.app import App
from ai_cull_assistant.processing_job import start_job
from ai_cull_assistant.settings import save_values
from ai_cull_assistant.session_store import load_session
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
