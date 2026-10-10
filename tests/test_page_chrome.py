import tkinter as tk
from tkinter import ttk
from unittest.mock import Mock

import pytest

from ai_cull_assistant.ui_help import install_page_chrome, install_control_help, ToolTip, show_page_help


def test_toolbar_actions_help_and_tooltip_lifecycle():
    root = tk.Tk()
    calls = []
    root._open_api_config = lambda: calls.append('api')
    root._open_update_page = lambda: calls.append('update')
    root._open_lr_page = lambda: calls.append('lr')
    try:
        bar = install_page_chrome(root, 'home')
        root.geometry('1000x600')
        root.update()
        for action in ('api', 'update', 'lr'):
            bar.buttons[action].invoke()
        assert calls == ['api', 'update', 'lr']
        assert root.bind('<Alt-Key-1>') and root.bind('<Alt-Key-4>') and root.bind('<F1>')
        assert install_page_chrome(root, 'home') is bar
        assert [button.cget('text') for button in bar.workflow_buttons.values()] == ['主页', '选片组', '人脸框', '复核', '选片']
        from ai_cull_assistant.ui_style import COLORS
        assert bar.brand.winfo_rootx() < bar.buttons['home'].winfo_rootx()
        assert bar.help_button.winfo_rootx() > root.winfo_rootx() + root.winfo_width() / 2
        assert bar.workflow_markers['home'].cget('background') == COLORS['accent']
        assert bar.workflow_markers['faces'].cget('background') == COLORS['surface']
        assert ttk.Style(root).lookup('Active.Nav.TButton', 'background') == COLORS['surface']
        install_control_help(root, 'home')
        tooltip = bar.buttons['api']._control_tooltip
        tooltip.show()
        assert tooltip.popup is not None
        tooltip.hide()
        assert tooltip.popup is None
        tooltip.schedule()
        tooltip.hide()
        assert tooltip.timer is None
        show_page_help(root, 'home')
        root.update()
        assert root._page_help_window.winfo_exists()
        root._page_help_window.destroy()
    finally:
        root.destroy()


def test_main_layout_fits_compact_desktop_and_keeps_flow_on_one_row(tmp_path):
    from ai_cull_assistant.app import App
    from ai_cull_assistant.settings import save_values
    save_values(tmp_path, {'input': '', 'workspace': ''})
    app = App(settings_dir=tmp_path)
    try:
        app.geometry('1100x620')
        app.update()
        flow = [app.scan_button, app.group_button, app.crop_button, app.focus_button,
                app.sheets_button, app.review_button]
        assert [w.cget('text') for w in flow] == [
            '扫描图片', '编辑选片组', '检测/调整人脸框', 'AI 复核', '生成联系表', 'AI 选片与导出']
        assert [int(w.grid_info()['column']) for w in flow] == list(range(6))
        assert [w.winfo_rootx() for w in flow] == sorted(w.winfo_rootx() for w in flow)
        assert len({w.winfo_rooty() for w in flow}) == 1
        for widget in flow + [app.log_text, app.clear_log_button, app._page_chrome]:
            assert widget.winfo_rootx() >= app.winfo_rootx()
            assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + app.winfo_width()
            assert widget.winfo_rooty() + widget.winfo_height() <= app.winfo_rooty() + app.winfo_height()
        assert not hasattr(app.log_text.master, '_scroll_container')
        app._set_processing_busy(True)
        help_buttons = []
        def walk(w):
            for child in w.winfo_children():
                if isinstance(child, ttk.Button) and child.cget('text') == '帮助':
                    help_buttons.append(child)
                walk(child)
        walk(app._page_chrome)
        assert help_buttons and not help_buttons[0].instate(['disabled'])
        app._set_processing_busy(False)
    finally:
        app.destroy()


@pytest.mark.parametrize('entry', ['home', 'toolbar'])
@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('busy', ['idle', 'processing', 'review', 'update'])
def test_workflow_navigation_only_enters_focus_page_without_submitting(
        tmp_path, monkeypatch, entry, cached, busy):
    from ai_cull_assistant.app import App
    from ai_cull_assistant.settings import save_values
    save_values(tmp_path, {'input': '', 'workspace': '', 'no_auto_updates': True})
    # Install before widget creation: Tcl commands retain their bound callback.
    # Both spies call the real methods; stop only at workspace preparation so
    # an explicit start proves the dispatch chain without launching work or API.
    submitted = []
    run_focus = App._run_focus_review
    def tracked_focus(self):
        submitted.append('focus')
        return run_focus(self)
    monkeypatch.setattr(App, '_run_focus_review', tracked_focus)
    app = App(settings_dir=tmp_path)
    start = Mock(wraps=app._start_processing)
    monkeypatch.setattr(app, '_start_processing', start)
    prepare = Mock(return_value=False)
    monkeypatch.setattr(app, '_sync_selected_workspace', prepare)
    notices = []
    monkeypatch.setattr('ai_cull_assistant.app.messagebox.showinfo', lambda *a, **kw: notices.append(a))
    review_state = [False]
    monkeypatch.setattr(app, '_review_busy', lambda: review_state[0])
    callback_errors = []
    app.report_callback_exception = lambda *args: callback_errors.append(args)
    try:
        page = None
        if cached:
            app._navigate_workspace('focus')
            page = app._focus_page
            app._navigate_workspace('home')
        job = app._processing_job = object()  # Preserve an existing checkpoint.
        worker = getattr(app, '_scan_thread', None)
        app._set_progress(37)
        if busy == 'processing':
            app._set_processing_busy(True)
        elif busy == 'review':
            review_state[0] = True
            app._poll_processing()
        elif busy == 'update':
            app.updates.busy = True
            app._set_update_busy(True)
        for _ in range(2):
            button = app.focus_button if entry == 'home' else app._page_chrome.workflow_buttons['focus']
            assert not button.instate(['disabled'])
            button.invoke()
            app.update()
            if page is None:
                page = app._focus_page
            assert app._focus_page is page
            assert app._workspace_pages['focus'] is page
            assert app._visible_page == 'focus'
            assert page.winfo_exists() and page._page_id == 'focus'
            assert page.winfo_toplevel() is app
            assert app._page_chrome.workflow_buttons['focus'].cget('style') == 'Active.Nav.TButton'
            # Repeated activation on the visible page must also stay inert.
            app._page_chrome.workflow_buttons['focus'].invoke()
            app._page_chrome.workflow_buttons['home'].invoke()
            app.update()
            assert page.winfo_exists() and not page.winfo_ismapped()
            assert app._processing_job is job and getattr(app, '_scan_thread', None) is worker
            assert app._processing_busy == (busy == 'processing')
            assert review_state[0] == (busy == 'review')
            assert app.updates.busy == (busy == 'update')
            assert app.progress_text.get() == '37%'
            assert not app._stop_event.is_set()
            assert not submitted and not notices
            start.assert_not_called()
            prepare.assert_not_called()
        if busy == 'processing':
            app._set_processing_busy(False)
        elif busy == 'review':
            review_state[0] = False
            app._poll_processing()
        elif busy == 'update':
            app.updates.busy = False
            app._set_update_busy(False)
        app.focus_button.invoke()
        assert app._focus_page is page
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        begin = next(w for w in descendants(page)
                     if isinstance(w, ttk.Button) and w.cget('text') == '开始 AI 复核')
        assert not begin.instate(['disabled'])
        begin.invoke()
        assert submitted == ['focus']
        start.assert_called_once_with(mode='focus')
        prepare.assert_called_once_with()
        assert app._processing_job is job and getattr(app, '_scan_thread', None) is worker
        assert not callback_errors
    finally:
        app.destroy()


def test_update_window_reserves_close_button_after_theme_layout(tmp_path):
    from ai_cull_assistant.app import App
    from tkinter import ttk
    app = App(settings_dir=tmp_path)
    try:
        app._open_update_page()
        page = app._update_page
        app.update()
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)
        close = next(w for w in children(page) if isinstance(w, ttk.Button) and w.cget('text') == '关闭')
        for width, height in ((700,310), (640,260), (820,440)):
            page.geometry(f'{width}x{height}')
            app.update()
            assert close.winfo_height() >= close.winfo_reqheight()
            assert close.winfo_rooty() + close.winfo_height() <= page.winfo_rooty() + page.winfo_height()
    finally:
        app.destroy()
