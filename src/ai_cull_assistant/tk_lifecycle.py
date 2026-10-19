"""Cancel Tcl scheduling without breaking Tkinter's command ownership."""
import tkinter as tk


def cancel_pending_callbacks(widget, *, all_callbacks=False):
    """Cancel this widget's pending after/idle scripts, or an entire root's.

    Leave command deletion to the owning widget's destroy(). Calling the root's
    after_cancel() on a child's timer deletes the command but leaves it in the
    child's _tclCommands registry, causing a second deletion during teardown.
    This works with a Tcl-only interpreter too; no display or update is needed.
    """
    commands = set(getattr(widget, '_tclCommands', None) or ())
    for token in widget.tk.splitlist(widget.tk.call('after', 'info')):
        if all_callbacks:
            widget.tk.call('after', 'cancel', token)
            continue
        script, _kind = widget.tk.call('after', 'info', token)
        try:
            parts = widget.tk.splitlist(script)
        except tk.TclError:
            # Arbitrary Tcl scripts need not be valid Tcl lists. Tkinter's
            # after() registers a single command name, so these are not ours.
            continue
        # Tcl may return a typed, unhashable Tcl_Obj for a command word.
        # Tkinter registers command names as strings; compare their Tcl text.
        if parts and str(parts[0]) in commands:
            widget.tk.call('after', 'cancel', token)
