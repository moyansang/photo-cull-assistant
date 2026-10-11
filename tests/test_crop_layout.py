"""Keep face-editing controls stable across photo orientation and DPI."""
from datetime import datetime
import time
import tkinter as tk
from tkinter import ttk

import pytest
from PIL import Image

from ai_cull_assistant.crop_dialog import CropDialog
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset


def settle(root, page):
    deadline = time.monotonic() + 8
    quiet = 0
    while time.monotonic() < deadline:
        root.update()
        if page._future is None and page._wanted is None and not page._pending:
            quiet += 1
            if quiet >= 5:
                return
        else:
            quiet = 0
        time.sleep(.02)
    pytest.fail('face preview did not settle')


def rectangle(widget):
    return (widget.winfo_rootx(), widget.winfo_rooty(),
            widget.winfo_width(), widget.winfo_height())


@pytest.mark.parametrize('size,dpi', [
    ((960, 540), 96), ((1180, 660), 120), ((1280, 720), 144),
    ((1600, 900), 144), ((1920, 1000), 192),
])
def test_orientation_and_resize_keep_controls_stable(tmp_path, monkeypatch, size, dpi):
    from ai_cull_assistant import crop_dialog
    monkeypatch.setattr(crop_dialog, 'detect', lambda *_args, **_kwargs: [])
    assets = []
    for name, dimensions in [('landscape', (900, 600)), ('portrait', (600, 900)), ('square', (700, 700))]:
        path = tmp_path / (name + '.jpg')
        Image.new('RGB', dimensions, '#808080').save(path)
        assets.append(PhotoAsset(name, path, path, None, path, datetime.now(), '.jpg', preview_path=path))
    root = tk.Tk()
    original_scaling = root.tk.call('tk', 'scaling')
    root.tk.call('tk', 'scaling', dpi / 72)
    root.geometry(f'{size[0]}x{size[1]}+0+0')
    root._page_chrome = tk.Frame(root)
    page = None
    try:
        settings = CropSettings()
        settings.photos[settings.key(assets[1])] = {'manual_face': [.3, .2, .25, .25]}
        page = CropDialog(root, assets, settings, lambda _: None, embedded=True)
        page.pack(fill='both', expand=True)
        settle(root, page)
        controls = list(page._navigation.winfo_children()) + list(page._side_actions.winfo_children())
        controls += [page.person_picker, page.ratio_picker, page.clear_faces_button, page.restore_face_button]
        controls = list(dict.fromkeys(controls))
        for width, height in [size, (size[0] - 60, size[1] - 30)]:
            root.geometry(f'{width}x{height}+0+0')
            settle(root, page)
            baseline = [rectangle(w) for w in controls]
            canvas = rectangle(page.canvas)
            for step in (1, 1, -1, -1):
                page.navigate(step)
                settle(root, page)
                assert [rectangle(w) for w in controls] == baseline
                assert rectangle(page.canvas) == canvas
                px, py, pw, ph = rectangle(page)
                for widget in controls:
                    x, y, w, h = rectangle(widget)
                    assert widget.winfo_viewable()
                    assert px <= x and py <= y
                    assert x + w <= px + pw and y + h <= py + ph
                    if isinstance(widget, ttk.Button):
                        assert w >= widget.winfo_reqwidth()
                x, y, w, h, original_w, original_h = page._image_rect
                assert x >= 0 and y >= 0
                assert x + w <= page.canvas.winfo_width() + 1
                assert y + h <= page.canvas.winfo_height() + 1
                assert abs(w / h - original_w / original_h) < .02
    finally:
        if page is not None:
            page.destroy()
        root.tk.call('tk', 'scaling', original_scaling)
        root.destroy()


@pytest.mark.parametrize('size,dpi', [
    ((960, 600), 96), ((1180, 720), 120), ((1280, 800), 144),
    ((1600, 900), 144), ((1920, 1080), 192),
])
def test_main_and_embedded_pages_fit_viewport(tmp_path, monkeypatch, size, dpi):
    from ai_cull_assistant.app import App
    from ai_cull_assistant import window_layout
    original_init = tk.Tk.__init__
    def init(root, *args, **kwargs):
        original_init(root, *args, **kwargs)
        root._test_original_scaling = root.tk.call('tk', 'scaling')
        root.tk.call('tk', 'scaling', dpi / 72)
    monkeypatch.setattr(tk.Tk, '__init__', init)
    monkeypatch.setattr(window_layout, 'work_area_for',
                        lambda _: window_layout.WorkArea(0, 0, size[0] + 64, size[1] + 64))
    app = App(settings_dir=tmp_path)
    try:
        app.geometry(f'{size[0]}x{size[1]}+0+0')
        for page in ('home', 'focus', 'faces'):
            if page == 'focus':
                app._open_focus_page()
            elif page == 'faces':
                app._open_crop_settings()
            app.update()
            def buttons(parent):
                for widget in parent.winfo_children():
                    if isinstance(widget, ttk.Button) and widget.winfo_viewable():
                        yield widget
                    yield from buttons(widget)
            ax, ay, aw, ah = rectangle(app)
            for button in buttons(app):
                x, y, w, h = rectangle(button)
                assert ax <= x and ay <= y, (page, button.cget('text'))
                assert x + w <= ax + aw and y + h <= ay + ah, (page, button.cget('text'))
                assert w >= button.winfo_reqwidth(), (page, button.cget('text'))
    finally:
        app.tk.call('tk', 'scaling', app._test_original_scaling)
        app.destroy()
