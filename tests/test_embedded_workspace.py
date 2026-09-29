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
        monkeypatch.setattr(app, '_save_session', lambda: None)
        app.detection_confidence_var.set('0.76')
        app._commit_detection_confidence()
        assert faces.global_settings().detection_confidence == .76
        assert all(asset.ai_focus_dirty for asset in assets)
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
        app.geometry('900x700'); app.update()
        assert review.export_button.winfo_viewable()
        assert review.export_button.winfo_rooty() == review.split_button.winfo_rooty()
        assert review.export_button.winfo_rootx() > review.split_button.winfo_rootx()
        assert review.batch_tree.winfo_height() > 40
        review.notebook.select(review.web_tab); app.update()
        assert str(review.page_actions.grid_info()['in']) == str(review.web_actions)
        assert review.export_button.winfo_viewable()
        assert review.web_tree.winfo_height() > 40
        assert review.export_button.winfo_rootx() + review.export_button.winfo_width() < app.winfo_rootx() + app.winfo_width()
        review.notebook.select(review.task_tab); app.update()
        assert str(review.page_actions.grid_info()['in']) == str(review.api_actions)
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


def test_page_space_and_return_actions(tmp_path, monkeypatch):
    from tkinter import ttk
    project, assets, _, _ = setup_project(tmp_path)
    app = App(settings_dir=tmp_path/'settings')
    monkeypatch.setattr(app, '_sync_selected_workspace', lambda: True)
    monkeypatch.setattr(app, '_ensure_selected_session', lambda: None)
    app.scan_result = SimpleNamespace(assets=assets, workspace_dir=project.workspace,
                                     main_pages=[], rejected_pages=[])
    def buttons(widget):
        found=[]
        for child in widget.winfo_children():
            if isinstance(child, ttk.Button): found.append(child)
            found.extend(buttons(child))
        return found
    try:
        app.geometry('1100x780'); app.update()
        small_log = app.log_text.winfo_height()
        app.geometry('1200x940'); app.update()
        assert app.log_text.winfo_height() > small_log + 30
        for name in ('focus', 'groups', 'faces'):
            app._navigate_workspace(name); app.update()
            page = app._workspace_pages[name]
            home = next(b for b in buttons(page) if b.cget('text') == '返回主页')
            assert home.winfo_viewable()
            if name == 'focus':
                resume = next(b for b in buttons(page) if b.cget('text') == '继续处理')
                assert home.winfo_rooty() == resume.winfo_rooty()
                assert home.winfo_rootx() > resume.winfo_rootx() + resume.winfo_width() + 100
            if name == 'faces':
                assert page.caption.winfo_viewable()
                assert abs(page.clear_faces_button.winfo_rooty() - page.ratio_picker.winfo_rooty()) < 5
                assert page.detail_canvas.winfo_width() >= 220
                scan = next(b for b in buttons(page) if b.cget('text') == '重新扫描')
                assert scan.winfo_rooty() < home.winfo_rooty()
                assert page.canvas.winfo_height() > 250
                # Both orientations keep controls beside the image; flipping
                # repeatedly must preserve drafts and keep every action visible.
                for portrait in (True, False, True):
                    page._fit_preview_layout(portrait=portrait)
                    app.update_idletasks()
                    assert page._navigation.winfo_viewable()
                    assert page.detail_canvas.winfo_height() > 100
                    assert page.canvas.winfo_rooty() + page.canvas.winfo_height() <= app.log_text.winfo_rooty()
                    assert all(b.winfo_viewable() for b in buttons(page))
                assert not any(b.cget('text') in ('关闭', '重新扫描修改过的图片') for b in buttons(page))
            home.invoke(); app.update()
            assert app._visible_page == 'home'
            assert app._workspace_pages[name] is page
    finally:
        app.destroy()
