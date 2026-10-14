"""Real viewer entry/controller methods with only Tk surfaces substituted.

These tests verify orchestration and PIL output pixels, not window-manager,
physical-input, font, DPI, or rendered-widget behavior.
"""
from datetime import datetime
from pathlib import Path
from threading import Event
import time
from types import MethodType, SimpleNamespace

import numpy as np
from PIL import Image, ImageOps
import pytest

from ai_cull_assistant.image_viewer import (
    DisplayFrame, ImageViewer, LoadResult, ViewerLoader, Viewport, load_source,
)
from ai_cull_assistant.group_editor import GroupEditor
from ai_cull_assistant.ai_review_ui import ReviewDialog
from ai_cull_assistant.models import PhotoAsset


class Surface:
    def __init__(self, width=40, height=64):
        self.value = ''
        self.width, self.height = width, height
        self.options = {}
        self.bindings = {}
    def set(self, value): self.value = value
    def get(self): return self.value
    def configure(self, **kwargs): self.options.update(kwargs)
    def winfo_width(self): return self.width
    def winfo_height(self): return self.height
    def delete(self, *args): pass
    def create_text(self, *args, **kwargs): pass
    def create_rectangle(self, *args, **kwargs): pass
    def create_image(self, *args, **kwargs): pass
    def focus_set(self): pass
    def canvasy(self, value): return value
    def tag_bind(self, tag, event, callback): self.bindings[tag, event] = callback


class PixelPhoto:
    """Retain output from the real PIL renderer in place of ImageTk upload."""
    def __init__(self, image, **kwargs): self.image = image.copy()
    def width(self): return self.image.width
    def height(self): return self.image.height
    def __del__(self): self.image.close()


def photo(path, *, group=1, preview=None):
    path = Path(path)
    return PhotoAsset(path.stem, path, path, None, path, datetime(2026, 10, 13),
                      path.suffix, group_id=group, preview_path=preview)


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.setattr('ai_cull_assistant.image_viewer.ImageTk.PhotoImage', PixelPhoto)
    monkeypatch.setattr('ai_cull_assistant.ai_api.call_model', lambda *a, **k: pytest.fail('browsing invoked paid AI'))
    viewers = []
    def create(assets, loader):
        viewer = object.__new__(ImageViewer)
        viewer.assets, viewer.index, viewer.loader = assets, 0, loader
        viewer.viewport = Viewport(40, 64)
        viewer.on_index_changed = None
        viewer._frame = viewer._photo = viewer._drag = None
        viewer._poll_id = viewer._render_id = None
        viewer._closed = viewer._fullscreen = viewer._native_pending = False
        viewer._load_error = None
        viewer._loading_source = viewer._raw_requested = False
        viewer.source_var, viewer.file_var = Surface(), Surface()
        viewer.raw_button, viewer.prev_button, viewer.next_button = Surface(), Surface(), Surface()
        viewer.fullscreen_button = Surface()
        viewer.canvas = Surface()
        viewer.title = lambda value: None
        viewer.after = lambda *args: 'scheduled'
        viewer.after_cancel = lambda *args: None
        viewers.append(viewer)
        return viewer
    yield create
    for viewer in viewers:
        viewer._closed = True
        viewer.loader.close()
        viewer.loader.thread.join(3)
        assert not viewer.loader.thread.is_alive()
        if viewer._frame is not None:
            viewer._frame.close()
        viewer._photo = None


def drive(viewer, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        viewer._poll()
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('Viewer controller did not reach expected state')


def test_native_render_is_pixel_exact_at_odd_dimensions_and_fractional_pan():
    pixels = np.zeros((81, 101, 3), dtype=np.uint8)
    pixels[:, ::2] = 255
    image = Image.fromarray(pixels)
    view = Viewport(40, 30, 101, 81)
    view.native()
    for center in ((.5, .5), (.557, .473)):
        view.center_x, view.center_y = center
        result = view.render(image)
        left = round(center[0] * 101 - 20)
        top = round(center[1] * 81 - 15)
        with image.crop((left, top, left + 40, top + 30)) as expected:
            assert result.tobytes() == expected.tobytes()
        assert set(np.unique(np.asarray(result))) == {0, 255}
        result.close()
    image.close()


def test_preview_upgrade_100_percent_displays_exact_oriented_jpg_pixels(controller, tmp_path):
    # JPEG compression is intentional: compare against decoded original pixels,
    # not an uncompressed synthetic image that is no longer the source file.
    pixels = np.indices((101, 161)).sum(axis=0).astype('uint8')
    rgb = np.stack((pixels, pixels * 2, pixels * 3), axis=-1)
    original = tmp_path / 'oriented.jpg'
    exif = Image.Exif(); exif[274] = 6
    Image.fromarray(rgb).save(original, quality=95, exif=exif)
    with Image.open(original) as source:
        expected = ImageOps.exif_transpose(source).convert('RGB')
    preview_path = tmp_path / 'preview.jpg'
    expected.resize((50, 80)).save(preview_path)
    a = photo(original, preview=preview_path)
    started, release = Event(), Event()
    def decoder(asset, **kwargs):
        started.set()
        assert release.wait(3)
        return load_source(asset, **kwargs)
    viewer = controller([a], ViewerLoader(decoder=decoder))
    original_bytes = original.read_bytes()
    try:
        viewer._request()
        assert started.wait(3)
        drive(viewer, lambda: viewer._frame is not None and viewer._frame.preview)
        assert '缓存预览' in viewer.source_var.get()
        viewer.native()
        assert viewer.viewport.scale == pytest.approx(1)
        release.set()
        drive(viewer, lambda: viewer._frame is not None and not viewer._frame.preview)
        assert viewer._frame.image.size == (101, 161)
        assert viewer.viewport.scale == pytest.approx(1)
        assert 'JPG 原图' in viewer.source_var.get() and '100%' in viewer.source_var.get()
        with expected.crop((30, 48, 70, 112)) as crop:
            assert viewer._photo.image.tobytes() == crop.tobytes()
        assert original.read_bytes() == original_bytes
        assert a.focus_score is None and a.ai_focus_result is None and not a.auto_rejected
    finally:
        release.set()
        expected.close()


def test_real_controller_navigation_preserves_view_and_rejects_late_generation(controller, tmp_path):
    paths = [tmp_path / 'first.png', tmp_path / 'second.png']
    for path, size, color in zip(paths, [(200, 320), (400, 640)], ['red', 'blue']):
        Image.new('RGB', size, color).save(path)
    viewer = controller([photo(path) for path in paths], ViewerLoader())
    viewer._request()
    drive(viewer, lambda: viewer._frame is not None)
    viewer.viewport.zoom = 4
    viewer.viewport.center_x, viewer.viewport.center_y = .65, .6
    viewer._render()
    old_generation = viewer._generation
    viewer.navigate(1)
    drive(viewer, lambda: viewer._frame is not None)
    assert viewer.index == 1
    assert (viewer.viewport.center_x, viewer.viewport.center_y, viewer.viewport.zoom) == pytest.approx((.65, .6, 4))
    assert viewer.file_var.get().startswith('2 / 2')
    stale = DisplayFrame(Image.new('RGB', (40, 64), 'green'), 'paired_jpeg')
    viewer.loader.results.put(LoadResult(old_generation, frame=stale))
    viewer._poll()
    assert viewer._frame.image.getpixel((0, 0)) == (0, 0, 255)
    with pytest.raises(ValueError): stale.image.getpixel((0, 0))
    generation = viewer._generation
    viewer.navigate(1)
    assert viewer._generation == generation  # End boundary performs no new decode.


def test_explicit_raw_request_keeps_jpg_until_full_decode_and_preserves_center(controller, tmp_path):
    a = photo(tmp_path / 'a.jpg'); a.raw_path = tmp_path / 'a.nef'
    started, release = Event(), Event()
    calls = []
    def decoder(asset, *, full_raw):
        calls.append(full_raw)
        if full_raw:
            started.set()
            assert release.wait(3)
        return DisplayFrame(Image.new('RGB', (400, 640), 'blue' if full_raw else 'red'),
                            'raw_full_decode' if full_raw else 'paired_jpeg')
    loader = ViewerLoader(decoder=decoder, preview_loader=lambda a: DisplayFrame(Image.new('RGB', (25, 40)), 'cached_preview', True))
    viewer = controller([a], loader)
    try:
        viewer._request()
        drive(viewer, lambda: viewer._frame is not None and not viewer._frame.preview)
        viewer.viewport.zoom = 4
        viewer.viewport.center_x, viewer.viewport.center_y = .6, .65
        viewer.show_raw()
        assert started.wait(3)
        viewer._poll()
        assert viewer._frame.source_kind == 'paired_jpeg'
        assert 'RAW 完整解码' in viewer.source_var.get()
        release.set()
        drive(viewer, lambda: viewer._frame.source_kind == 'raw_full_decode')
        assert (viewer.viewport.center_x, viewer.viewport.center_y, viewer.viewport.zoom) == pytest.approx((.6, .65, 4))
        assert calls == [False, True]
        assert 'RAW 完整解码图' in viewer.source_var.get()
    finally:
        release.set()


def test_group_doubleclick_closure_opens_exact_member_and_group_order(monkeypatch, tmp_path):
    first, second = photo(tmp_path / 'a.jpg'), photo(tmp_path / 'b.jpg')
    canvas = Surface(640, 480)
    calls = []
    monkeypatch.setattr('ai_cull_assistant.image_viewer.ImageViewer',
                        lambda parent, assets, **kwargs: calls.append((parent, assets, kwargs)))
    font = SimpleNamespace(metrics=lambda key: 16, measure=lambda text: len(text) * 5)
    owner = SimpleNamespace(detail_canvas=canvas, _detail_images=[], selected_group_id=1,
                            selected_stem=first.stem, _members_by_group={1: [first, second]},
                            _fonts={'small': font}, _refresh_detail_status=lambda *args: None,
                            _thumbnail_key=lambda asset, size: (asset.stem, size),
                            _cancel_stale_thumbnail_requests=lambda: None,
                            _cached_photo=lambda *args: None, _select_asset=lambda stem: None)
    owner._open_viewer = MethodType(GroupEditor._open_viewer, owner)
    GroupEditor._redraw_detail(owner)
    canvas.bindings['detail:b', '<Double-Button-1>'](None)
    assert calls == [(owner, [first, second], {'index': 1, 'fullscreen': True})]


def test_review_entrypoint_replaces_old_viewer_and_never_substitutes_missing_asset(monkeypatch, tmp_path):
    first, second = photo(tmp_path / 'a.jpg'), photo(tmp_path / 'b.jpg')
    photos = {'a': {'stem': 'a', 'path': str(first.primary_path)},
              'b': {'stem': 'b', 'path': str(second.primary_path)}}
    calls, errors = [], []
    class Child:
        def __init__(self, owner, assets, index):
            self.closed = False
            calls.append((assets, index))
        def winfo_exists(self): return not self.closed
        def close(self): self.closed = True
    monkeypatch.setattr('ai_cull_assistant.image_viewer.ImageViewer', Child)
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.messagebox.showerror', lambda *args, **kw: errors.append(args))
    owner = SimpleNamespace(_photo_ids=['b', 'a'], _selected_photo_id=lambda: 'a',
                            _photos=lambda: photos, _asset_by_stem={'a': first, 'b': second},
                            assets=[first, second])
    owner._asset_for_photo = MethodType(ReviewDialog._asset_for_photo, owner)
    ReviewDialog._open_original(owner)
    old = owner._image_viewer
    ReviewDialog._open_original(owner)
    assert old.closed and calls == [([second, first], 1), ([second, first], 1)]
    owner._asset_by_stem.pop('a'); owner.assets = [second]
    ReviewDialog._open_original(owner)
    assert errors and len(calls) == 2


def test_real_controller_input_handlers_zoom_pan_fit_native_and_escape(controller, tmp_path):
    a = photo(tmp_path / 'image.png')
    Image.new('RGB', (200, 320), 'red').save(a.primary_path)
    viewer = controller([a], ViewerLoader())
    viewer._request()
    drive(viewer, lambda: viewer._frame is not None)
    viewer.viewport.zoom = 4
    point = SimpleNamespace(x=12, y=25, delta=120, num=0)
    before = viewer.viewport.image_point(point.x, point.y)
    viewer._wheel(point)
    assert viewer.viewport.image_point(point.x, point.y) == pytest.approx(before)
    assert viewer._render_id == 'scheduled'
    center = viewer.viewport.center_x, viewer.viewport.center_y
    viewer._drag_start(SimpleNamespace(x=10, y=10))
    viewer._drag_move(SimpleNamespace(x=13, y=12))
    assert viewer.viewport.center_x < center[0] and viewer.viewport.center_y < center[1]
    viewer.native()
    assert viewer.viewport.scale == pytest.approx(1)
    viewer._resize(SimpleNamespace(width=60, height=96))
    assert viewer.viewport.scale == pytest.approx(1)
    viewer.fit()
    assert (viewer.viewport.center_x, viewer.viewport.center_y, viewer.viewport.zoom) == (.5, .5, 1)
    attributes, closed = [], []
    viewer.attributes = lambda *args: attributes.append(args)
    viewer.close = lambda: closed.append(True)
    viewer.toggle_fullscreen()
    assert viewer._fullscreen and attributes[-1] == ('-fullscreen', True)
    viewer._escape()
    assert not viewer._fullscreen and not closed
    viewer._escape()
    assert closed == [True]


def test_real_multiformat_switching_releases_active_image_and_bounds_cache(controller, tmp_path):
    from ai_cull_assistant.image_viewer import BrowseCache
    suffixes = ['jpg', 'png', 'bmp', 'tiff', 'webp']
    assets = []
    for suffix in suffixes:
        path = tmp_path / ('picture.' + suffix)
        Image.new('RGB', (80, 128), 'red').save(path)
        assets.append(photo(path))
    originals = {a.primary_path: a.primary_path.read_bytes() for a in assets}
    cache = BrowseCache(max_bytes=2 * 80 * 128 * 4, max_entries=2)
    viewer = controller(assets, ViewerLoader(cache=cache))
    viewer._request()
    for index, suffix in enumerate(suffixes):
        drive(viewer, lambda: viewer._frame is not None)
        assert viewer.index == index
        assert (suffix.upper() + ' 原图') in viewer.source_var.get()
        assert viewer._frame.image.size == (80, 128)
        assert cache.bytes_used <= cache.max_bytes and len(cache._items) <= 2
        frame = viewer._frame
        if index + 1 < len(assets):
            viewer.navigate(1)
            with pytest.raises(ValueError): frame.image.getpixel((0, 0))
    assert originals == {path: path.read_bytes() for path in originals}
