import tkinter as tk
from types import SimpleNamespace
from pathlib import Path
from ai_cull_assistant.app import App
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.settings import save_values


def make_app(tmp_path):
    save_values(tmp_path,dict(input=str(tmp_path/'photos'), workspace=str(tmp_path/'workspace')))
    app=App(settings_dir=tmp_path);app.withdraw()
    return app


def test_clear_log_removes_queued_and_persisted_history(tmp_path):
    app=make_app(tmp_path)
    app._log('must disappear')
    app._clear_log();app.update()
    assert 'must disappear' not in app.log_text.get('1.0','end')
    app._close()
    app=make_app(tmp_path);app.update()
    try:assert 'must disappear' not in app.log_text.get('1.0','end')
    finally:app._close()


def test_busy_controls_and_stop(tmp_path):
    app=make_app(tmp_path)
    try:
        app._set_processing_busy(True)
        assert str(app.stop_button.cget('state'))=='normal'
        assert str(app.clear_log_button.cget('state'))=='normal'
        assert str(app.continue_button.cget('state'))=='disabled'
        app._stop_processing()
        assert app._stop_event.is_set()
        app._set_processing_busy(False)
        assert str(app.stop_button.cget('state'))=='disabled'
        app._set_progress(45)
        assert app.progress_label.get()=='生成联系表进度：'
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
        assert app.progress_label.get()=='生成联系表进度：'
        assert app.progress_text.get()=='60%'
        assert str(app.scan_button.cget('state'))=='normal'
    finally:app._close()


def test_saving_face_settings_regenerates_existing_scan_without_main_button(tmp_path,monkeypatch):
    app=make_app(tmp_path)
    regenerated=[]
    monkeypatch.setattr(app,'_regenerate_contacts',lambda:regenerated.append(True))
    try:
        app.scan_result=SimpleNamespace(assets=[])
        app._save_crop_settings(CropSettings(scale_factor=1.2))
        assert regenerated==[True]
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
        assert started==[dict(regenerate=True,regroup=False)]
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
        assert app.next_step_var.get()=='推荐下一步：AI 选片与 LR 导出'
        log=app.log_text.get('1.0','end')
        assert '初筛技术模糊/抖动弃置 0 张' in log
        assert '剩余可进入 AI 选片 1 张' in log
    finally:app._close()
