"""Real Tk geometry and input checks; require a display, never real settings."""
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from unittest.mock import Mock

import pytest

from ai_cull_assistant import window_layout
from ai_cull_assistant.app import App
from ai_cull_assistant.settings import save_values


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def assert_chinese_log_line_visible(app):
    text = app.log_text
    sample = '日志检查：中文内容完整显示。'
    text.configure(state='normal')
    text.delete('1.0', 'end')
    text.insert('1.0', sample)
    text.configure(state='disabled')
    text.yview_moveto(0)
    app.update()
    # Reuse the rendered named font: copying it converts pixel sizes to
    # rounded point sizes and changes its metrics at fractional Tk scaling.
    line_height = tkfont.nametofont(str(text.cget('font')), root=app).metrics('linespace')
    inset = sum(text.winfo_pixels(text.cget(key))
                for key in ('pady', 'borderwidth', 'highlightthickness'))
    assert text.winfo_height() - 2 * inset >= line_height
    line = text.dlineinfo('1.0')
    assert line is not None and line[3] >= line_height, line
    assert inset <= line[1] and line[1] + line[3] <= text.winfo_height() - inset
    for index in ('1.0', f'1.{len(sample) - 1}'):
        box = text.bbox(index)
        assert box is not None and box[3] >= line_height, box
        assert inset <= box[1] and box[1] + box[3] <= text.winfo_height() - inset


@pytest.mark.parametrize('dpi', [96, 144, 192])
@pytest.mark.parametrize('size', [(1100, 620), (960, 540), (836, 536), (1180, 840)])
def test_home_form_fits_without_scrolling_or_overlapping(tmp_path, monkeypatch, dpi, size):
    original_init = tk.Tk.__init__
    def scaled_init(root, *args, **kwargs):
        original_init(root, *args, **kwargs)
        root._original_scaling = root.tk.call('tk', 'scaling')
        root.tk.call('tk', 'scaling', dpi / 72)
        root.maxsize(2200, 1400)
    monkeypatch.setattr(tk.Tk, '__init__', scaled_init)
    monkeypatch.setattr(window_layout, 'work_area_for',
                        lambda _: window_layout.WorkArea(0, 0, size[0] + 64, size[1] + 64))
    save_values(tmp_path, {'input': '', 'workspace': '', 'options': {'no_auto_updates': True}})
    app = App(settings_dir=tmp_path)
    errors = []
    app.report_callback_exception = lambda *args: errors.append(args)
    try:
        # Includes startup and returning from an embedded page: neither may
        # allocate the homepage a clipped viewport based on a prior page.
        for page in ('home', 'focus', 'home'):
            app._navigate_workspace(page)
            app.update()
            if page != 'home':
                continue
            assert (app.winfo_width(), app.winfo_height()) == size
            assert not any(isinstance(w, (tk.Canvas, ttk.Scrollbar)) for w in descendants(app._home_page))
            controls = [w for w in descendants(app._home_page)
                        if isinstance(w, (ttk.Button, ttk.Entry, ttk.Checkbutton, ttk.Label))]
            controls += [app.stop_button, app.continue_button, app.contact_button, app.results_button,
                         app.clear_log_button, app.clear_workspace_button, app.log_text]
            left, top = app.winfo_rootx(), app.winfo_rooty()
            right, bottom = left + app.winfo_width(), top + app.winfo_height()
            for widget in controls:
                assert widget.winfo_viewable(), str(widget)
                minimum_height = widget.winfo_reqheight()
                assert widget.winfo_height() >= minimum_height, str(widget)
                assert left <= widget.winfo_rootx() < right
                assert top <= widget.winfo_rooty() < bottom
                assert widget.winfo_rootx() + widget.winfo_width() <= right
                assert widget.winfo_rooty() + widget.winfo_height() <= bottom
                if isinstance(widget, (ttk.Button, ttk.Checkbutton)):
                    assert widget.winfo_width() >= widget.winfo_reqwidth(), widget.cget('text')
            assert app._home_page.winfo_rooty() + app._home_page.winfo_height() <= app._shared_controls.winfo_rooty()
            assert app.contact_button.winfo_rooty() == app.results_button.winfo_rooty()
            assert app.results_button.winfo_rootx() >= app.contact_button.winfo_rootx() + app.contact_button.winfo_width()
            assert_chinese_log_line_visible(app)
        assert not errors
    finally:
        app.tk.call('tk', 'scaling', app._original_scaling)
        app._close()


def test_home_wheel_does_not_change_api_or_options_and_result_button_only_opens(tmp_path, monkeypatch):
    opened = Mock()
    monkeypatch.setattr(App, '_open_results_dir', opened)
    save_values(tmp_path, {'input': '', 'workspace': '', 'options': {'no_auto_updates': True}})
    app = App(settings_dir=tmp_path)
    try:
        app.update()
        app.api_profile_combo.configure(values=('provider A', 'provider B', 'provider C'))
        app.api_profile_combo.current(1)
        for widget in descendants(app._home_page):
            if not isinstance(widget, (ttk.Combobox, ttk.Spinbox)):
                continue
            before = widget.get()
            for delta in (-120, 120):
                widget.event_generate('<MouseWheel>', delta=delta)
                app.update()
                assert widget.get() == before
            for event in ('<Button-4>', '<Button-5>'):
                widget.event_generate(event)
                app.update()
                assert widget.get() == before
        # Explicit selection remains possible after wheel protection.
        app.api_profile_combo.current(2)
        assert app.api_profile_var.get() == 'provider C'
        app.results_button.invoke()
        opened.assert_called_once_with()
        assert app._visible_page == 'home'
        assert not app._processing_busy and app._processing_job is None
    finally:
        app._close()
