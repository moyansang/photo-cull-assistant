import time
import tkinter as tk
from types import SimpleNamespace

from ai_cull_assistant.app import App
from test_ai_project import setup_project


def test_main_tabs_preserve_drafts_and_shared_footer(tmp_path, monkeypatch):
    project, assets, _, _ = setup_project(tmp_path)
    app = App(settings_dir=tmp_path/'settings')
    app.geometry('1100x780')
    monkeypatch.setattr(app, '_sync_selected_workspace', lambda: True)
    monkeypatch.setattr(app, '_ensure_selected_session', lambda: None)
    monkeypatch.setattr(app, '_sheets_ready', lambda: True)
    app.scan_result = SimpleNamespace(assets=assets, workspace_dir=project.workspace,
                                     main_pages=[], rejected_pages=[])
    errors = []
    app.report_callback_exception = lambda *args: errors.append(args)
    try:
        app._log('persistent footer message')
        app._set_progress(37)
        app.update()
        app._navigate_workspace('groups')
        groups = app._workspace_pages['groups']
        groups._select_asset(assets[0].stem)
        app._navigate_workspace('faces')
        faces = app._workspace_pages['faces']
        faces.edits['test-draft'] = {'hidden': True}
        for target in ('home', 'focus', 'groups', 'faces'):
            app._navigate_workspace(target)
            app.update()
            assert app.log_text.winfo_viewable()
            assert app.progress_label.get() == '进度：'
            assert app.progress_text.get() == '37%'
            assert 'persistent footer message' in app.log_text.get('1.0', 'end')
            assert not any(isinstance(w, tk.Toplevel) for w in app.winfo_children())
        assert app._workspace_pages['faces'] is faces
        assert faces.edits['test-draft'] == {'hidden': True}
        assert app._workspace_pages['groups'] is groups
        assert groups.selected_stem == assets[0].stem
        assert faces.winfo_toplevel() is app
        assert app.grab_current() is None
        app._navigate_workspace('review')
        review = app._workspace_pages['review']
        deadline = time.time()+8
        while review._preparing_task and time.time()<deadline:
            app.update(); time.sleep(.01)
        assert not review._preparing_task
        app._show_workspace_page('home')
        review.status_var.set('background selection status')
        app._review_progress(62, 'AI 选片')
        app.update()
        assert app.progress_text.get() == '62%'
        assert 'background selection status' in app.log_text.get('1.0', 'end')
        app._navigate_workspace('review')
        assert app._workspace_pages['review'] is review
        assert not errors
        app._discard_workspace_pages()
        app.update()
        assert not app._workspace_pages
        assert app._visible_page == 'home'
    finally:
        app.destroy()


def test_busy_page_navigation_does_not_discard_active_task(tmp_path, monkeypatch):
    app = App(settings_dir=tmp_path)
    notices=[]
    monkeypatch.setattr('ai_cull_assistant.app.messagebox.showinfo', lambda *a,**kw:notices.append(a))
    try:
        app._set_processing_busy(True)
        app._page_chrome.workflow_buttons['focus'].invoke()
        app.update()
        assert app._visible_page == 'focus'
        app._page_chrome.workflow_buttons['groups'].invoke()
        assert app._visible_page == 'focus' and notices
        app._page_chrome.workflow_buttons['home'].invoke()
        assert app._visible_page == 'home'
        assert app._processing_busy
    finally:
        app.destroy()
