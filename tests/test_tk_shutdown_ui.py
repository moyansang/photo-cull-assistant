"""Native Tk lifecycle tests: run on Windows or a working X display."""
import tkinter as tk

import pytest

from ai_cull_assistant.app import App
from ai_cull_assistant.ui_page import WorkspacePage


def capture_background_errors(root):
    root.eval('set callback_errors {}; proc bgerror {msg} {lappend ::callback_errors $msg}')
    root._shutdown_callback_errors = []
    root.report_callback_exception = lambda *error: root._shutdown_callback_errors.append(error)


def assert_no_background_errors(root):
    # The Tcl interpreter remains usable after the root window has closed.
    # This exposes dangling idle scripts; after-info assertions also catch
    # long-delay polls without relying on timing or sleeping in the tests.
    root.tk.call('update')
    assert root.eval('set callback_errors') == ''
    assert root._shutdown_callback_errors == []


@pytest.mark.parametrize('close_target', ['page', 'host', 'root'])
def test_standalone_page_teardown_has_no_orphan_callbacks(close_target):
    root = tk.Tk()
    root.withdraw()
    root.update()
    capture_background_errors(root)
    page = WorkspacePage(root)
    host = page.window
    page.after(60000, lambda: None)
    page.after_idle(lambda: None)
    try:
        {'page': page, 'host': host, 'root': root}[close_target].destroy()
        assert not root.tk.call('after', 'info')
        assert_no_background_errors(root)
        if close_target != 'root':
            assert not host.winfo_exists()
            # Closing a standalone page must leave its main window usable.
            assert root.winfo_exists()
    finally:
        if close_target != 'root':
            root.destroy()


def test_embedded_page_cleanup_preserves_other_page_timers():
    root = tk.Tk()
    root.withdraw()
    root.update()
    capture_background_errors(root)
    page = WorkspacePage(root, embedded=True)
    survivor = WorkspacePage(root, embedded=True)
    calls = []
    page.after_idle(lambda: calls.append('closed'))
    survivor.after_idle(lambda: calls.append('survivor'))
    try:
        page.destroy()
        assert_no_background_errors(root)
        assert calls == ['survivor']
        assert survivor.winfo_exists()
    finally:
        root.destroy()


@pytest.mark.parametrize('close_target', ['destroy', '_close'])
def test_app_close_cancels_poll_preferences_log_and_child_callbacks(tmp_path, close_target):
    app = App(settings_dir=tmp_path)
    app.withdraw()
    capture_background_errors(app)
    calls = []
    child = tk.Button(app)
    child.after(60000, lambda: calls.append('child'))
    WorkspacePage(app).after_idle(lambda: calls.append('page'))
    app._schedule_settings_save()
    app._log('queued just before close')
    assert app.tk.call('after', 'info')
    getattr(app, close_target)()
    assert not app.tk.call('after', 'info')
    assert_no_background_errors(app)
    assert calls == []
    assert child._tclCommands is None
