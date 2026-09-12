import json

from ai_cull_assistant import app as app_module
from ai_cull_assistant.app import App
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.project_storage import save_workspace_preferences, workspace_for
from ai_cull_assistant.settings import read_values, save_values


def make_source(tmp_path, name):
    source = tmp_path / name
    source.mkdir()
    return source


def make_app(settings_dir, source, workspace, *, no_updates=False):
    save_values(
        settings_dir,
        {
            "input": str(source),
            "workspace": str(workspace),
            "options": {"no_auto_updates": no_updates},
        },
    )
    app = App(settings_dir=settings_dir)
    app.withdraw()
    return app


def test_startup_loads_project_preferences_and_keeps_update_global(tmp_path):
    source = make_source(tmp_path, "photos")
    workspace = tmp_path / "project"
    workspace_for(tmp_path, source, preferred=workspace)
    save_workspace_preferences(
        workspace,
        {"scale_factor": 1.4},
        {"grouping": "严格", "per_page": 24, "columns": 3, "screening": False},
    )

    app = make_app(tmp_path, source, workspace, no_updates=True)
    try:
        assert app.settings_dir == tmp_path
        assert app.crop_settings.scale_factor == 1.4
        assert app.preset_var.get() == "严格"
        assert app.per_page_var.get() == 24
        assert app.columns_var.get() == 3
        assert app.screening_var.get() is False
        assert app.no_updates_var.get() is True

        app._save_preferences()
        global_values = read_values(tmp_path)
        assert global_values["options"] == {"no_auto_updates": True}
        project_values = json.loads((workspace / "workspace-settings.json").read_text("utf-8"))
        assert project_values["options"]["grouping"] == "严格"
        assert "no_auto_updates" not in project_values["options"]
    finally:
        app._close()


def test_typed_input_switch_saves_old_project_and_loads_registered_project(tmp_path):
    first_source = make_source(tmp_path, "first")
    second_source = make_source(tmp_path, "second")
    first_workspace = workspace_for(tmp_path, first_source, preferred=tmp_path / "first-project")
    second_workspace = workspace_for(tmp_path, second_source, preferred=tmp_path / "second-project")
    save_workspace_preferences(
        first_workspace,
        {"scale_factor": 1.1},
        {"grouping": "标准", "per_page": 16, "columns": 4, "screening": True},
    )
    save_workspace_preferences(
        second_workspace,
        {"scale_factor": 1.7},
        {"grouping": "宽松", "per_page": 32, "columns": 5, "screening": False},
    )
    (second_workspace / "logs").mkdir()
    (second_workspace / "logs" / "session.log").write_text("second project log\n", encoding="utf-8")

    app = make_app(tmp_path, first_source, first_workspace)
    try:
        app.crop_settings = CropSettings(scale_factor=1.3)
        app.preset_var.set("严格")
        app.per_page_var.set(20)
        app.scan_result = object()
        app.review_project = object()

        unfinished = tmp_path / "second-typ"
        app.input_var.set(str(unfinished))
        app._save_preferences()
        assert app.input_var.get() == str(unfinished)
        assert app.workspace_var.get() == str(first_workspace)
        assert app.scan_result is not None
        assert read_values(tmp_path)["input"] == str(first_source)

        app.input_var.set(str(second_source))
        app._save_preferences()

        assert app.workspace_var.get() == str(second_workspace)
        assert app.crop_settings.scale_factor == 1.7
        assert app.preset_var.get() == "宽松"
        assert app.per_page_var.get() == 32
        assert app.columns_var.get() == 5
        assert app.screening_var.get() is False
        assert app.scan_result is None
        assert app.review_project is None
        assert "second project log" in app.log_text.get("1.0", "end")

        first_values = json.loads((first_workspace / "workspace-settings.json").read_text("utf-8"))
        assert first_values["face_crop"]["scale_factor"] == 1.3
        assert first_values["options"]["grouping"] == "严格"
        assert first_values["options"]["per_page"] == 20

        app.input_var.set("")
        app._save_preferences()
        assert app.workspace_var.get() == ""
        assert app.scan_result is None
        assert app.review_project is None
        assert app.log_text.get("1.0", "end").strip() == ""
    finally:
        app.scan_result = None
        app._close()


def test_typed_input_and_workspace_register_the_custom_pair(tmp_path):
    first_source = make_source(tmp_path, "first")
    second_source = make_source(tmp_path, "second")
    first_workspace = workspace_for(tmp_path, first_source, preferred=tmp_path / "first-project")
    custom_workspace = tmp_path / "chosen-second-project"
    app = make_app(tmp_path, first_source, first_workspace)
    try:
        app.input_var.set(str(second_source))
        app.workspace_var.set(str(custom_workspace))
        app._save_preferences()

        assert app.workspace_var.get() == str(custom_workspace.resolve())
        registry = read_values(tmp_path)["workspaces"]
        assert str(custom_workspace.resolve()) in registry.values()

        app.input_var.set(str(first_source))
        app._save_preferences()
        app.input_var.set(str(second_source))
        app._save_preferences()
        assert app.workspace_var.get() == str(custom_workspace.resolve())
    finally:
        app._close()


def test_conflicting_typed_workspace_is_rejected_and_selection_is_restored(tmp_path):
    first_source = make_source(tmp_path, "first")
    second_source = make_source(tmp_path, "second")
    first_workspace = workspace_for(tmp_path, first_source, preferred=tmp_path / "first-project")
    second_workspace = workspace_for(tmp_path, second_source, preferred=tmp_path / "second-project")
    app = make_app(tmp_path, first_source, first_workspace)
    try:
        app.workspace_var.set(str(second_workspace))
        assert app._sync_selected_workspace() is False
        assert app.input_var.get() == str(first_source)
        assert app.workspace_var.get() == str(first_workspace)
    finally:
        app._close()


def test_default_settings_use_runtime_location_but_explicit_directory_stays_literal(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime" / "settings"
    runtime.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(app_module, "prepare_runtime_settings", lambda: calls.append(True) or runtime)

    default_app = App()
    default_app.withdraw()
    try:
        assert default_app.settings_dir == runtime
        assert calls == [True]
        default_app._log("visible only")
        default_app.update()
        assert "visible only" in default_app.log_text.get("1.0", "end")
        assert not (runtime / "logs" / "session.log").exists()
    finally:
        default_app._close()

    explicit = tmp_path / "explicit"
    explicit.mkdir()
    explicit_app = App(settings_dir=explicit)
    explicit_app.withdraw()
    try:
        assert explicit_app.settings_dir == explicit
        assert calls == [True]
    finally:
        explicit_app._close()


def test_processing_syncs_typed_paths_before_reading_options(tmp_path, monkeypatch):
    source = make_source(tmp_path, "photos")
    workspace = workspace_for(tmp_path, source, preferred=tmp_path / "project")
    app = make_app(tmp_path, source, workspace)
    order = []
    monkeypatch.setattr(app, "_sync_selected_workspace", lambda: order.append("sync") or True)
    monkeypatch.setattr(
        app,
        "_processing_options",
        lambda: order.append("options") or {
            "grouping_preset": "standard",
            "photos_per_page": 16,
            "columns": 4,
            "technical_screening": True,
        },
    )
    monkeypatch.setattr(app, "_selected_api_profile", lambda: None)
    monkeypatch.setattr(app, "_save_preferences", lambda: None)

    class ThreadWithoutWorker:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", ThreadWithoutWorker)
    try:
        app._start_processing()
        assert order == ["sync", "options"]
    finally:
        app._processing_busy = False
        app._close()


def test_corrupt_archived_workspace_is_blocked_without_overwrite(tmp_path, monkeypatch):
    source = make_source(tmp_path, "photos")
    workspace = tmp_path / "project"
    workspace.mkdir()
    archive = workspace / ".workspace-archive.zip"
    archive.write_bytes(b"not a zip file")
    save_values(tmp_path, {"input": str(source), "workspace": str(workspace)})
    errors = []
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args, **kwargs: errors.append(args))

    app = App(settings_dir=tmp_path)
    app.withdraw()
    try:
        app.update()
        app._show_workspace_error()
        assert app._workspace_blocked is True
        assert app.scan_result is None
        assert archive.read_bytes() == b"not a zip file"
        assert not (workspace / "workspace-settings.json").exists()
        assert "清空工作区或选择其他工作区" in app.next_step_var.get()
        assert errors
    finally:
        app._close()
    assert archive.read_bytes() == b"not a zip file"
    assert not (workspace / "workspace-settings.json").exists()
