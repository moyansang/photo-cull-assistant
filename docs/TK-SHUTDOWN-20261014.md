# Tk shutdown callback repair, 2026-10-14

Baseline inspected: `fe0aaf3e0a0a0b15f47b70a2759e281a8195427d`.
No HEIF, decoding, visual layout, update-installation or persistence behavior was
changed. The repair is uncommitted pending integration.

## Findings and changes

The reported Windows run passed 1028 tests and skipped 2, but emitted 65 callback
diagnostics (28 lambda, 15 processing poll, 15 update poll, 5 log append,
1 API poll, 1 preference save). That prior result is not a test of this patch.
These diagnostics cannot be dismissed solely because pytest passed: Tcl's
background errors are not Python assertion failures.

Two concrete lifecycle defects were identified:

1. `App._close()` canceled scheduled callbacks, but direct `App.destroy()` did
   not. Ten existing test call sites use direct destruction. Tkinter deletes
   registered Python callback commands when destroying widgets, but Tcl keeps
   queued `after` scripts. A later event-loop update then executes a missing
   command. Processing/update polls, queued log appends and preference saves
   all follow this mechanism.
2. `WorkspacePage.destroy()` queued an idle lambda on its host, including while
   the host/root was already being destroyed. Its comment incorrectly claimed
   Tk automatically cancels the callback. Direct page destruction also bypassed
   specialized disposal's API-poll cancellation.

`App.destroy()` now cancels interpreter scheduling both before tree teardown
and afterward, covering callbacks scheduled by Destroy bindings. It does not
invoke the confirmation/save workflow; `_close()` retains that responsibility.

`WorkspacePage.destroy()` cancels callbacks owned by the page only. A private
Toplevel subclass marks parent teardown before destroying children. Direct page
destruction removes its empty wrapper synchronously; parent destruction does
not recurse and no new idle lambda is registered. Embedded pages retain their
existing hide-versus-dispose behavior.

The shared cancellation helper uses Tcl `after cancel`, leaving registered
command deletion to each owning widget. Whole-root cleanup cancels scripts
without parsing them; owner-scoped cleanup ignores non-list Tcl scripts that
cannot be Tkinter-registered callback names. Calling a root's Tkinter `after_cancel`
for a child's callback can otherwise delete the command twice.

These mechanisms explain the reported callback categories; the cloud run does
not establish that every one of the 65 Windows messages is eliminated.

## Cloud verification

Python 3.12; Tcl 9.0.4; isolated environment with project-pinned
`ttkbootstrap==2.2.3` and official PyPI test/runtime dependencies.

- Real Tcl before/after reproduction: before cleanup, two queued callbacks
  survived command destruction and both produced `invalid command name`;
  after cleanup, no pending callbacks and no Tcl background errors.
- 10 new Tcl lifecycle tests passed, including the intentionally reproduced
  old failure, timer/idle ownership, preserving another widget's callbacks,
  root/child command ownership, parent-close recursion, and callbacks added
  during root teardown, plus valid non-list Tcl scripts in both cleanup modes.
- 27 targeted tests passed in total: lifecycle, update controller, viewer
  controller integration, headless results, window layout calculation, and
  archive-to-contact-sheet rebuild.
- Python compileall passed. Diff whitespace validation passed with
  `core.whitespace=cr-at-eol` (existing modified files retain CRLF).
- GUI probe failed at Tk creation: no DISPLAY and no Xvfb in this environment.
  A broader attempted batch recorded 25 passes and 7 failures, all seven at
  native Tk construction for the same display limitation. They were not
  skipped or weakened. The final available-headless batch, after the Tcl-script parsing review fix,
  is 27/27.

Six new native-window tests are supplied in `tests/test_tk_shutdown_ui.py`.
They are not claimed as passing here. They capture both Tcl `bgerror` and
Python `report_callback_exception`, assert both are empty, and inspect pending
callbacks without sleeps.

## Required Windows verification

Run in the existing isolated source/test environment, without replacing the
installed application:

```
python -X utf8 -m pytest tests/test_tk_lifecycle.py tests/test_tk_shutdown_ui.py tests/test_processing_app.py tests/test_embedded_workspace.py tests/test_ai_review_ui.py tests/test_tool_window.py tests/test_tool_layout.py tests/test_crop_layout.py tests/test_app_workspaces.py -q -p no:cacheprovider --junitxml=tk-shutdown-targeted.xml
python -X utf8 -m pytest tests -q -p no:cacheprovider --junitxml=tk-shutdown-full.xml
```

Capture both stdout and stderr in raw files. Check `invalid command name`,
`("after" script)`, `TclError`, and callback tracebacks independently of pytest's
exit code. Acceptance requires no dangling callbacks in new lifecycle tests,
no regression in the full suite, and no corresponding shutdown diagnostics.

Manually verify normal close, direct/test teardown, closing a standalone review
window via its titlebar, returning from embedded pages without losing drafts,
and closing while preferences/log messages are queued. Existing busy-worker
close semantics must remain intact. Windows build/HEIF tests remain separate
and are not replaced by these cloud checks.
