"""Real Tk tests. A working display is required; failures are never skipped."""
from datetime import datetime
from pathlib import Path
import time
import tkinter as tk
from types import SimpleNamespace

from PIL import Image
import pytest

from ai_cull_assistant.image_viewer import DisplayFrame, ImageViewer, ViewerLoader
from ai_cull_assistant.models import PhotoAsset


@pytest.fixture
def root():
    window = tk.Tk()
    window.geometry('900x650')
    window.update()
    errors = []
    window.report_callback_exception = lambda *args: errors.append(args)
    yield window, errors
    window.destroy()
    assert not errors


def photo(tmp_path, name):
    path = tmp_path / name
    return PhotoAsset(path.stem, path, path, None, path, datetime(2026, 1, 1), path.suffix)


def drive(root, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Viewer did not become ready')


def fake_loader():
    return ViewerLoader(decoder=lambda a, **kw: DisplayFrame(Image.new('RGB', (1800, 1200), 'red'), 'paired_jpeg'),
                        preview_loader=lambda a: None)


def test_real_tk_render_controls_and_close(root, tmp_path):
    window, errors = root
    viewer = ImageViewer(window, [photo(tmp_path, 'a.jpg')], fullscreen=False, loader=fake_loader())
    drive(window, lambda: viewer._frame is not None)
    assert viewer._photo.width() == viewer.canvas.winfo_width()
    assert viewer._photo.height() == viewer.canvas.winfo_height()
    assert viewer.canvas.find_all()
    assert 'JPG 原图' in viewer.source_var.get()
    assert str(viewer.raw_button['state']) == 'disabled'
    viewer.native()
    assert viewer.viewport.scale == pytest.approx(1)
    viewer.fit()
    assert viewer.viewport.zoom == 1
    viewer.close()
    window.update()
    assert not viewer.winfo_exists()
    viewer.loader.thread.join(3)
    assert not viewer.loader.thread.is_alive()


def test_real_tk_wheel_pan_navigation_preserves_view(root, tmp_path):
    window, errors = root
    viewer = ImageViewer(window, [photo(tmp_path, 'a.jpg'), photo(tmp_path, 'b.jpg')],
                         fullscreen=False, loader=fake_loader())
    drive(window, lambda: viewer._frame is not None)
    viewer.viewport.zoom = 4
    point = SimpleNamespace(x=300, y=250, delta=120, num=0)
    before = viewer.viewport.image_point(point.x, point.y)
    assert viewer._wheel(point) == 'break'
    assert viewer.viewport.image_point(point.x, point.y) == pytest.approx(before)
    viewer._drag_start(SimpleNamespace(x=100, y=100))
    viewer._drag_move(SimpleNamespace(x=150, y=120))
    center = viewer.viewport.center_x, viewer.viewport.center_y
    zoom = viewer.viewport.zoom
    viewer.navigate(1)
    drive(window, lambda: viewer._frame is not None)
    assert viewer.index == 1
    assert (viewer.viewport.center_x, viewer.viewport.center_y) == pytest.approx(center)
    assert viewer.viewport.zoom == zoom
    viewer.navigate(1)
    assert viewer.index == 1
    viewer.close()


def test_real_tk_fullscreen_escape_and_parent_teardown(root, tmp_path):
    window, errors = root
    owner = tk.Toplevel(window)
    owner.update()
    owner.grab_set()
    viewer = ImageViewer(owner, [photo(tmp_path, 'a.jpg')], fullscreen=True, loader=fake_loader())
    drive(window, lambda: viewer._frame is not None)
    assert viewer._fullscreen
    viewer._escape()
    assert not viewer._fullscreen
    assert viewer.winfo_exists()
    viewer._escape()
    window.update()
    assert owner.grab_current() == owner
    second = ImageViewer(owner, [photo(tmp_path, 'b.jpg')], fullscreen=False, loader=fake_loader())
    drive(window, lambda: second._frame is not None)
    owner.destroy()
    window.update()
    assert second._closed
    second.loader.thread.join(3)
    assert not second.loader.thread.is_alive()


def test_real_tk_embedded_workspace_frame_owner(root, tmp_path):
    window, errors = root
    page = tk.Frame(window)
    page.pack(fill='both', expand=True)
    window.update()
    viewer = ImageViewer(page, [photo(tmp_path, 'a.jpg')], fullscreen=False, loader=fake_loader())
    drive(window, lambda: viewer._frame is not None)
    assert str(viewer.transient()) == str(window)
    page.destroy()
    window.update()
    assert viewer._closed
    viewer.loader.thread.join(3)
    assert not viewer.loader.thread.is_alive()
