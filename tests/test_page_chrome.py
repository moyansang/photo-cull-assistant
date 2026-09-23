import tkinter as tk
from tkinter import ttk

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
