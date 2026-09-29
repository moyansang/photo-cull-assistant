import tkinter as tk

from ai_cull_assistant.app import App
from ai_cull_assistant.ui_help import PAGE_HELP, show_page_help


def test_tools_switch_one_window_keep_api_draft_and_return_home(tmp_path):
    app = App(settings_dir=tmp_path)
    try:
        app.update()
        app._open_api_config()
        window = app._tool_window
        editor = app._api_config_window
        editor.key_var.set('unsaved-test-key')
        editor.model_var.set('unsaved-model')
        editor.advanced_var.set(True)
        editor._sync_advanced()
        app.update()
        handle = window.winfo_id()
        for action in ('update', 'lr', 'api', 'update'):
            window._page_chrome.buttons[action].invoke()
            app.update()
            assert app._tool_window is window
            assert window.winfo_id() == handle
            assert window._page_id == action
            assert [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)] == [window]
        window._page_chrome.help_button.invoke()
        app.update()
        assert window._page_id == 'help'
        assert window.help_heading.cget('text') == PAGE_HELP['update'][0]
        assert set(window._page_chrome.buttons) == {'home', 'api', 'update', 'lr'}
        assert window.bind('<Alt-Key-1>') and window.bind('<Alt-Key-4>') and window.bind('<F1>')
        window._page_chrome.buttons['api'].invoke()
        app.update()
        assert app._api_config_window is editor
        assert editor.key_var.get() == 'unsaved-test-key'
        assert editor.model_var.get() == 'unsaved-model'
        assert editor.settings_notebook.select() == str(editor.advanced_tab)
        window._page_chrome.buttons['home'].invoke()
        app.update()
        assert not window.winfo_exists()
        assert app._page_id == 'home'
        assert 'unsaved-test-key' not in (tmp_path / 'settings.json').read_text('utf-8')
        show_page_help(app, 'home')
        app.update()
        assert app._tool_window is not window
        assert app._tool_window._page_id == 'help'
        app._tool_window._page_chrome.buttons['api'].invoke()
        assert app._tool_window._page_id == 'api'
    finally:
        app.destroy()


def test_hidden_api_work_close_waits_then_returns_home(tmp_path, monkeypatch):
    app = App(settings_dir=tmp_path)
    try:
        app.update()
        app._open_api_config()
        window = app._tool_window
        editor = app._api_config_window
        editor._busy = True
        window._page_chrome.buttons['update'].invoke()
        assert window._page_id == 'update'
        window._page_chrome.buttons['home'].invoke()
        assert window.winfo_exists() and editor._close_pending
        assert window._page_id == 'api'
        monkeypatch.setattr(editor, '_saved', lambda *a, **k: None)
        editor._events.put(('saved', {}))
        editor._poll_events()
        app.update()
        assert not window.winfo_exists()
        assert app._page_id == 'home'
    finally:
        app.destroy()


def test_successful_api_test_closes_shared_window_after_switch(tmp_path, monkeypatch):
    app = App(settings_dir=tmp_path)
    try:
        app.update()
        app._open_api_config()
        window = app._tool_window
        editor = app._api_config_window
        app._open_help_page('api')
        activated = []
        monkeypatch.setattr(editor, '_saved', lambda profile, **kw: activated.append(kw['activate']))
        editor._events.put(('tested', ({}, 'ok')))
        editor._poll_events()
        app.update()
        assert activated == [True]
        assert not window.winfo_exists()
    finally:
        app.destroy()
