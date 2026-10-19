"""Real Tcl scheduling regression tests; these do not require a display."""
import tkinter as tk

import pytest

from ai_cull_assistant.tk_lifecycle import cancel_pending_callbacks
from ai_cull_assistant.ui_page import WorkspacePage, _PageWindow


def owner(interpreter):
    widget = tk.Misc()
    widget.tk = interpreter.tk
    widget._tclCommands = []
    return widget


@pytest.fixture
def interpreter():
    root = tk.Tcl()
    # Tk's report_callback_exception does not catch a missing Tcl command.
    # Capture bgerror itself, including errors triggered by a later update().
    root.eval('set callback_errors {}; proc bgerror {msg} {lappend ::callback_errors $msg}')
    yield root
    for token in root.tk.splitlist(root.tk.call('after', 'info')):
        root.tk.call('after', 'cancel', token)


def test_destroy_without_cancellation_reproduces_missing_command(interpreter):
    widget = owner(interpreter)
    widget.after(0, lambda: None)
    widget.destroy()
    interpreter.eval('update')
    assert 'invalid command name' in interpreter.eval('set callback_errors')


@pytest.mark.parametrize('idle', [False, True])
def test_owner_cleanup_leaves_other_widgets_running(interpreter, idle):
    widget, survivor = owner(interpreter), owner(interpreter)
    calls = []
    if idle:
        widget.after_idle(lambda: calls.append('closed'))
    else:
        widget.after(0, lambda: calls.append('closed'))
    command = widget._tclCommands[-1]
    survivor.after(0, lambda: calls.append('survivor'))
    cancel_pending_callbacks(widget)
    # The owner must remain able to delete its command exactly once.
    assert interpreter.tk.call('info', 'commands', command)
    widget.destroy()
    interpreter.eval('update')
    assert calls == ['survivor']
    assert interpreter.eval('set callback_errors') == ''
    survivor.destroy()


def test_root_cleanup_cancels_child_and_raw_tcl_timers(interpreter):
    root, child = owner(interpreter), owner(interpreter)
    root.after(0, lambda: None)
    child.after_idle(lambda: None)
    interpreter.eval('after 0 {error unexpected_raw_timer}')
    cancel_pending_callbacks(root, all_callbacks=True)
    assert interpreter.tk.call('after', 'info') == ''
    child.destroy()
    root.destroy()
    cancel_pending_callbacks(root, all_callbacks=True)  # Safe on repeated cleanup.
    interpreter.eval('update')
    assert interpreter.eval('set callback_errors') == ''


@pytest.mark.parametrize('owned', [False, True])
def test_typed_tcl_command_word_preserves_ownership(interpreter, owned):
    widget = owner(interpreter)
    word = interpreter.tk.call('dict', 'create', 'typed', 'command')
    with pytest.raises(TypeError):
        hash(word)
    calls = []
    interpreter.tk.createcommand(str(word), lambda: calls.append('called'))
    if owned:
        widget._tclCommands.append(str(word))
    token = interpreter.tk.call('after', 0, (word,))
    script, _ = interpreter.tk.call('after', 'info', token)
    with pytest.raises(TypeError):
        hash(interpreter.tk.splitlist(script)[0])
    cancel_pending_callbacks(widget)
    widget.destroy()
    interpreter.eval('update')
    assert calls == ([] if owned else ['called'])
    assert interpreter.eval('set callback_errors') == ''
    if not owned:
        interpreter.tk.deletecommand(str(word))


@pytest.mark.parametrize('all_callbacks', [False, True])
def test_non_list_tcl_script_does_not_interrupt_cleanup(interpreter, all_callbacks):
    widget = owner(interpreter)
    widget.after(0, lambda: None)
    # Valid executable Tcl, but not a valid Tcl list (braces followed by ]).
    raw_token = interpreter.eval('after 0 {set x [list {a b}]}')
    cancel_pending_callbacks(widget, all_callbacks=all_callbacks)
    widget.destroy()
    pending = interpreter.tk.splitlist(interpreter.tk.call('after', 'info'))
    assert pending == (() if all_callbacks else (raw_token,))
    interpreter.eval('update')
    assert interpreter.eval('set callback_errors') == ''
    assert bool(interpreter.eval('info exists x') == '1') is (not all_callbacks)


@pytest.mark.parametrize('parent_closing', [False, True])
def test_page_destroy_cancels_poll_and_never_schedules_on_host(interpreter, monkeypatch, parent_closing):
    # Exercise the real page destroy method with real Tcl after commands while
    # substituting only native window destruction (which needs a display).
    page = WorkspacePage.__new__(WorkspacePage)
    page.tk = interpreter.tk
    page._tclCommands = []
    hosts_destroyed = []

    class Host:
        _destroying = parent_closing

        def destroy(self):
            hosts_destroyed.append(True)

    page._host_window = Host()
    page.after(0, lambda: None)
    page.after_idle(lambda: None)
    monkeypatch.setattr(tk.Frame, 'destroy', tk.Misc.destroy)
    page.destroy()
    assert hosts_destroyed == ([] if parent_closing else [True])
    interpreter.eval('update')
    assert interpreter.eval('set callback_errors') == ''
    assert interpreter.tk.call('after', 'info') == ''


def test_host_marks_recursive_destruction_before_children(monkeypatch):
    host = _PageWindow.__new__(_PageWindow)
    calls = []

    def destroy_children(self):
        assert self._destroying
        calls.append(True)
        self.destroy()

    monkeypatch.setattr(tk.Toplevel, 'destroy', destroy_children)
    host.destroy()
    host.destroy()
    assert calls == [True]


def test_app_direct_destroy_cancels_all_timers_including_teardown_work(interpreter, monkeypatch):
    from ai_cull_assistant.app import App

    app = App.__new__(App)
    app.tk = interpreter.tk
    app._tclCommands = []
    child = owner(interpreter)
    app.after(0, lambda: None)
    child.after_idle(lambda: None)

    def destroy_tree(self):
        # Model a child Destroy binding queuing work on the root while closing.
        self.after_idle(lambda: None)
        child.destroy()
        tk.Misc.destroy(self)

    monkeypatch.setattr(tk.Tk, 'destroy', destroy_tree)
    app.destroy()
    interpreter.eval('update')
    assert interpreter.eval('set callback_errors') == ''
    assert interpreter.tk.call('after', 'info') == ''
