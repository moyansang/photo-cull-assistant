"""Exercise the real application chrome, not just an isolated review frame."""
import time
import tkinter as tk
from tkinter import ttk

from ai_cull_assistant import ai_api, window_layout
from ai_cull_assistant.app import App
from ai_cull_assistant.ai_review_ui import ReviewDialog
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.settings import save_values
from test_ai_project import setup_project


def test_compact_review_scroll_actions_and_task_controls(tmp_path, monkeypatch):
    original_init = tk.Tk.__init__
    def scaled_init(root, *args, **kwargs):
        original_init(root, *args, **kwargs)
        root._old_scaling = root.tk.call('tk', 'scaling')
        root.tk.call('tk', 'scaling', 192 / 72)
    monkeypatch.setattr(tk.Tk, '__init__', scaled_init)
    monkeypatch.setattr(window_layout, 'work_area_for',
                        lambda _: window_layout.WorkArea(0, 0, 900, 600))
    settings = tmp_path / 'settings'
    save_values(settings, {'input': '', 'workspace': '', 'options': {'no_auto_updates': True}})
    monkeypatch.setattr(ai_api, 'load_profiles', lambda _: [])
    app = App(settings_dir=settings)
    callback_errors = []
    app.report_callback_exception = lambda *error: callback_errors.append(error)
    review = None
    try:
        folder = tmp_path / 'project'
        folder.mkdir()
        project, assets, _, _ = setup_project(folder)
        review = ReviewDialog(app._page_host, project, assets, CropSettings(), settings,
                              embedded=True, on_close=lambda: None)
        app._mount_workspace_page('review', review)
        deadline = time.monotonic() + 15
        while review._preparing_task and time.monotonic() < deadline:
            app.update()
            time.sleep(.02)
        assert not review._preparing_task
        app.update()
        assert app._shared_toggle.winfo_viewable()
        assert not app._shared_controls.winfo_ismapped()
        assert not app._shared_log_frame.winfo_ismapped()
        assert review.winfo_height() >= 250
        app._shared_toggle.invoke()
        app.update()
        assert app._shared_controls.winfo_viewable()
        assert app._shared_log_frame.winfo_viewable()
        app._shared_toggle.invoke()
        app.update()
        for kind in ('processing', 'update'):
            setter = app._set_processing_busy if kind == 'processing' else app._set_update_busy
            if kind == 'update':
                app.updates.busy = True
            setter(True)
            app.update()
            assert app.stop_button.winfo_viewable()
            assert app._shared_log_frame.winfo_viewable()
            assert app._shared_toggle.instate(['disabled'])
            if kind == 'update':
                app.updates.busy = False
            setter(False)
            app.update()
            assert not app._shared_controls.winfo_ismapped()

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def assert_reachable(widget, canvas):
            total = canvas.bbox('all')[3]
            offset = canvas.yview()[0] * total
            y = widget.winfo_rooty() - canvas.winfo_rooty() + offset
            canvas.yview_moveto(max(0, y - (canvas.winfo_height() - widget.winfo_height()) / 2) / total)
            app.update()
            label = widget.cget('text') if 'text' in widget.keys() else str(widget)
            assert widget.winfo_width() >= widget.winfo_reqwidth(), label
            assert widget.winfo_height() >= widget.winfo_reqheight(), label
            assert canvas.winfo_rootx() <= widget.winfo_rootx(), label
            assert widget.winfo_rootx() + widget.winfo_width() <= canvas.winfo_rootx() + canvas.winfo_width(), label
            assert canvas.winfo_rooty() <= widget.winfo_rooty(), label
            assert widget.winfo_rooty() + widget.winfo_height() <= canvas.winfo_rooty() + canvas.winfo_height(), (
                label, canvas.yview(), canvas.cget('scrollregion'), canvas.bbox('all'),
                canvas.itemcget(canvas.find_all()[0], 'height'), total, offset, y)

        for index, canvas in ((0, review.api_scroll.canvas), (1, review.web_scroll.canvas), (2, review.review_canvas)):
            review.notebook.select(index)
            app.update()
            buttons = [w for w in descendants(canvas) if isinstance(w, ttk.Button) and w.winfo_ismapped()]
            assert review.export_button in buttons  # Canvas owns and clips the actual native child.
            for button in buttons:
                assert_reachable(button, canvas)
        for index, canvas in ((0, review.api_scroll.canvas), (1, review.web_scroll.canvas)):
            review.notebook.select(index)
            app.update()
            if not getattr(review, '_prefs_expanded', False):
                review.prefs_toggle.invoke()
            app.update()
            for widget in descendants(review.prefs_scroll.canvas):
                if isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Button)):
                    assert_reachable(widget, review.prefs_scroll.canvas)
            for widget in descendants(canvas):
                if isinstance(widget, ttk.Button) and widget.winfo_ismapped():
                    assert_reachable(widget, canvas)
            review.prefs_toggle.invoke()
            app.update()
        app._show_workspace_page('home')
        app.update()
        assert app._shared_controls.winfo_viewable()
        assert not app._shared_toggle.winfo_ismapped()
        assert not callback_errors
    finally:
        if review is not None:
            review.dispose()
        app.tk.call('tk', 'scaling', app._old_scaling)
        app.destroy()
