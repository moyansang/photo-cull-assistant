import tkinter as tk
import json
from types import SimpleNamespace
from pathlib import Path
from ai_cull_assistant.app import App
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.settings import save_values
from ai_cull_assistant.shared_api import NO_API_LABEL


def make_app(tmp_path):
    save_values(tmp_path,dict(input=str(tmp_path/'photos'), workspace=str(tmp_path/'workspace')))
    app=App(settings_dir=tmp_path);app.withdraw()
    return app


def test_clear_workspace_removes_all_project_state(tmp_path, monkeypatch):
    app=make_app(tmp_path)
    workspace=Path(app.workspace_var.get()); workspace.mkdir(parents=True, exist_ok=True)
    (workspace/'scan-session.json').write_text('{}')
    (workspace/'nested').mkdir();(workspace/'nested'/'preview.jpg').write_bytes(b'x')
    monkeypatch.setattr('ai_cull_assistant.app.messagebox.askyesno',lambda *a,**kw:True)
    try:
        app._clear_workspace();app.update()
        assert list(workspace.iterdir()) == []
        assert app.scan_result is None
        assert app.next_step_var.get() == '推荐下一步：扫描图片'
    finally:app._close()


def test_busy_controls_and_stop(tmp_path):
    app=make_app(tmp_path)
    try:
        app._set_processing_busy(True)
        assert str(app.stop_button.cget('state'))=='normal'
        assert str(app.clear_log_button.cget('state'))=='disabled'
        assert str(app.continue_button.cget('state'))=='disabled'
        app._stop_processing()
        assert app._stop_event.is_set()
        app._set_processing_busy(False)
        assert str(app.stop_button.cget('state'))=='disabled'
        app._set_progress(45)
        assert app.progress_label.get()=='扫描图片进度：'
        assert app.progress_text.get()=='45%'
    finally:app._close()


def test_update_progress_temporarily_replaces_scan_progress_and_disables_scan(tmp_path):
    app=make_app(tmp_path)
    try:
        app._set_progress(45)
        app._set_update_busy(True)
        app._show_update_progress(23)
        assert app.progress_label.get()=='更新进度：'
        assert app.progress_text.get()=='23%'
        assert str(app.scan_button.cget('state'))=='disabled'
        app._set_progress(60)
        assert app.progress_text.get()=='23%'
        app._restore_scan_progress()
        app._set_update_busy(False)
        assert app.progress_label.get()=='扫描图片进度：'
        assert app.progress_text.get()=='60%'
        assert str(app.scan_button.cget('state'))=='normal'
    finally:app._close()


def test_saving_face_settings_rescans_only_changed_photos(tmp_path,monkeypatch):
    app=make_app(tmp_path)
    started=[]
    monkeypatch.setattr(app,'_start_processing',lambda **kwargs:started.append(kwargs))
    try:
        first=SimpleNamespace(primary_path=tmp_path/'first.jpg', ai_focus_dirty=False)
        second=SimpleNamespace(primary_path=tmp_path/'second.jpg', ai_focus_dirty=False)
        app.scan_result=SimpleNamespace(assets=[first,second])
        saved=[]
        monkeypatch.setattr(app, '_save_session', lambda: saved.append(True))
        settings=CropSettings(photos={str(first.primary_path.resolve()).casefold(): {'manual_face':[.1,.1,.2,.2]}})
        app._save_crop_settings(settings)
        assert first.ai_focus_dirty and not second.ai_focus_dirty
        assert saved == [True]
        assert started==[{'mode':'rescan'}]
        assert not hasattr(app,'regenerate_button')
    finally:
        app.scan_result=None
        app._close()


def test_regroup_confirmation_and_layout_only(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    app=make_app(tmp_path)
    workspace=tmp_path/'workspace';workspace.mkdir(exist_ok=True)
    groupfile=workspace/'groups.json'
    groupfile.write_text(json.dumps(dict(source='manual')))
    (workspace/'processing-settings.json').write_text(json.dumps(dict(grouping_preset='standard')))
    app.scan_result=SimpleNamespace(workspace_dir=workspace,group_store_path=groupfile,input_dir=tmp_path/"photos")
    started=[]
    monkeypatch.setattr(app,'_start_processing',lambda **kw:started.append(kw))
    monkeypatch.setattr('ai_cull_assistant.app.messagebox.askyesno',lambda *a,**kw:False)
    try:
        app.preset_var.set('严格');app._regenerate_contacts()
        assert not started
        app.preset_var.set('标准');app.per_page_var.set(24);app._regenerate_contacts()
        assert started==[dict(mode='rescan',regroup=False)]
    finally:
        app.scan_result=None;app._close()


def test_continue_from_saved_job_updates_main_window(tmp_path):
    import time
    from threading import Event
    from PIL import Image
    from ai_cull_assistant.processing_job import start_job
    from ai_cull_assistant.crop_settings import CropSettings
    source=tmp_path/'photos';source.mkdir()
    Image.new('RGB',(80,120),'white').save(source/'a.jpg')
    options=dict(grouping_preset='standard',photos_per_page=8,columns=2,technical_screening=False)
    job=start_job(source,tmp_path/'workspace',options,CropSettings())
    stop=Event();stop.set()
    assert job.run(options,CropSettings(),stop,lambda _:None) is None
    app=make_app(tmp_path)
    try:
        assert str(app.continue_button.cget('state'))=='normal'
        app.screening_var.set(False)
        app._continue_processing()
        deadline=time.monotonic()+15
        while app._processing_busy and time.monotonic()<deadline:
            app.update();time.sleep(.01)
        assert not app._processing_busy
        assert app.scan_result and len(app.scan_result.assets)==1
        assert app.progress_var.get()==100
        assert app.next_step_var.get()=='推荐下一步：AI 选片与导出'
        log=app.log_text.get('1.0','end')
        assert '初筛技术模糊/抖动弃置 0 张' in log
        assert '剩余可进入 AI 选片 0 张' in log
        assert '清晰度仍不确定 1 张' in log
    finally:app._close()


def test_homepage_api_selection_persists_only_profile_id(tmp_path):
    profile = {
        "id": "11111111111111111111111111111111",
        "name": "视觉模型",
        "base_url": "https://example.test/v1",
        "model": "vision-model",
        "timeout": 30,
    }
    (tmp_path / "ai-api-profiles.json").write_text(
        json.dumps({"version": 1, "profiles": [profile]}, ensure_ascii=False), encoding="utf-8"
    )
    save_values(tmp_path, {"selected_api_profile_id": "11111111111111111111111111111111"})
    app = make_app(tmp_path)
    try:
        assert app.api_profile_var.get() == "视觉模型"
        assert app._selected_api_profile() == profile
        app.api_profile_var.set(NO_API_LABEL)
        app._api_selection_changed()
        assert app._selected_api_profile() is None
        saved = json.loads((tmp_path / "settings.json").read_text("utf-8"))
        assert saved["selected_api_profile_id"] == "manual-web"
        assert "base_url" not in saved and "secret" not in saved
        # A successful dialog test selects its profile even if the UI was manual.
        save_values(tmp_path, {"selected_api_profile_id": profile['id']})
        app._api_profiles_saved()
        assert app._selected_api_profile() == profile
        assert app.api_profile_var.get() == "视觉模型"
    finally:
        app._close()


def test_homepage_staged_button_layout(tmp_path):
    app=make_app(tmp_path)
    try:
        expected = [
            '扫描图片','编辑选片组','检测/调整人脸框',
            'AI 复核','生成联系表','AI 选片与导出',
            '停止处理','继续处理','打开联系表目录','清空工作区',
            'LR 插件','检查更新',
        ]
        found=[]
        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child,tk.ttk.Button):
                    found.append(child.cget('text'))
                walk(child)
        walk(app)
        assert [name for name in found if name in expected] == expected
        checks=[]
        def collect_checks(widget):
            for child in widget.winfo_children():
                if isinstance(child,tk.ttk.Checkbutton):checks.append(child.cget('text'))
                collect_checks(child)
        collect_checks(app)
        assert '不再自动检查更新' in checks
    finally:app._close()


def test_staged_scan_then_contact_sheet_flow(tmp_path):
    import time
    from PIL import Image
    source=tmp_path/'photos';source.mkdir()
    Image.new('RGB',(80,120),'white').save(source/'a.jpg')
    app=make_app(tmp_path)
    try:
        app.screening_var.set(False)
        app._run_scan_thread()
        deadline=time.monotonic()+15
        while app._processing_busy and time.monotonic()<deadline:
            app.update();time.sleep(.01)
        assert app.scan_result and not app.scan_result.main_pages
        assert not app._sheets_ready()
        assert app.next_step_var.get()=='推荐下一步：检测/调整人脸框'

        app._generate_contact_sheets()
        deadline=time.monotonic()+15
        while app._processing_busy and time.monotonic()<deadline:
            app.update();time.sleep(.01)
        assert app.scan_result.main_pages
        assert app._sheets_ready()
        assert app.next_step_var.get()=='推荐下一步：AI 选片与导出'
    finally:app._close()
def test_homepage_reopens_one_api_editor_and_restores_owner_grab(tmp_path):
    app = make_app(tmp_path)
    try:
        assert app.api_profile_var.get() == NO_API_LABEL
        saved = json.loads((tmp_path / "settings.json").read_text("utf-8"))
        assert saved["selected_api_profile_id"] == "manual-web"
        app.deiconify()
        app.update()
        app.grab_set()
        app._open_api_config()
        editor = app._api_config_window
        assert editor is not None and editor.winfo_exists()
        assert app.grab_current() is None

        editor.withdraw()
        app._open_api_config()
        app.update()
        assert app._api_config_window is editor
        assert editor.state() != "withdrawn"

        editor.destroy()
        app.update()
        assert app.grab_current() is app
        app.grab_release()
    finally:
        app._close()
