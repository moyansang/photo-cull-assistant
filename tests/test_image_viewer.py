from datetime import datetime
from pathlib import Path
from threading import Event
import time

import pytest
from PIL import Image

from ai_cull_assistant.image_viewer import (
    BrowseCache, DisplayFrame, Viewport, ViewerLoader, load_preview, source_key,
)
from ai_cull_assistant.models import PhotoAsset


def asset(path, preview=None):
    path = Path(path)
    return PhotoAsset(path.stem, path, path, None, path, datetime(2026, 1, 1),
                      path.suffix, preview_path=preview)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.01)
    raise AssertionError('Timed out waiting for viewer worker')


def close_loader(loader):
    loader.close()
    loader.thread.join(timeout=3)
    assert not loader.thread.is_alive()


def test_mouse_center_zoom_keeps_image_point():
    view = Viewport(1000, 800, 4000, 3000, zoom=4)
    before = view.image_point(300, 250)
    view.zoom_at(1.2, 300, 250)
    assert view.image_point(300, 250) == pytest.approx(before)


def test_resolution_change_preserves_normalized_view():
    view = Viewport(1000, 800, 1600, 1200, .7, .6, 4)
    field = (view.width / view.scale / view.image_width,
             view.height / view.scale / view.image_height)
    view.set_image((8000, 6000))
    assert (view.center_x, view.center_y) == pytest.approx((.7, .6))
    assert (view.width / view.scale / view.image_width,
            view.height / view.scale / view.image_height) == pytest.approx(field)


def test_fit_native_and_clamped_pan():
    view = Viewport(1000, 600, 6000, 4000)
    assert view.scale == .15
    view.native()
    assert view.scale == pytest.approx(1)
    view.pan(-999999, 999999)
    assert view.center_x == pytest.approx(1 - 1000 / 12000)
    assert view.center_y == pytest.approx(600 / 8000)
    view.fit()
    assert (view.center_x, view.center_y, view.zoom) == (.5, .5, 1)


def test_render_allocates_viewport_not_zoomed_image():
    image = Image.new('RGB', (300, 200), 'red')
    view = Viewport(80, 60, 300, 200, zoom=200)
    rendered = view.render(image)
    assert rendered.size == (80, 60)
    assert rendered.getpixel((40, 30)) == (255, 0, 0)
    rendered.close()
    image.close()


def test_fit_render_has_theme_matte_and_correct_orientation():
    image = Image.new('RGB', (20, 10), 'red')
    image.putpixel((0, 0), (0, 255, 0))
    view = Viewport(40, 40, 20, 10)
    rendered = view.render(image)
    assert rendered.getpixel((20, 0)) == (48, 48, 48)
    assert rendered.getpixel((0, 10)) == (0, 255, 0)
    rendered.close()
    image.close()


def test_browse_cache_enforces_bytes_entries_and_copies():
    cache = BrowseCache(max_bytes=800, max_entries=2)
    first = DisplayFrame(Image.new('RGB', (10, 10), 'red'), 'paired_jpeg')
    assert cache.put(('a',), first)
    copy = cache.get(('a',))
    copy.image.putpixel((0, 0), (0, 0, 0))
    assert first.image.getpixel((0, 0)) == (255, 0, 0)
    copy.close()
    assert cache.put(('b',), DisplayFrame(Image.new('RGB', (10, 10)), 'paired_jpeg'))
    assert cache.put(('c',), DisplayFrame(Image.new('RGB', (10, 10)), 'paired_jpeg'))
    assert cache.get(('a',)) is None
    assert cache.bytes_used == 800
    with pytest.raises(ValueError):
        first.image.getpixel((0, 0))
    cache.clear()
    assert cache.bytes_used == 0


def test_oversized_frame_is_not_retained_or_closed():
    cache = BrowseCache(max_bytes=10)
    frame = DisplayFrame(Image.new('RGB', (20, 20)), 'paired_jpeg')
    assert not cache.put(('a',), frame)
    assert cache.bytes_used == 0
    assert frame.image.getpixel((0, 0)) == (0, 0, 0)
    frame.close()


def test_source_key_separates_folders_and_full_raw(tmp_path):
    assert source_key(asset(tmp_path / 'a/photo.jpg'), False) != source_key(asset(tmp_path / 'b/photo.jpg'), False)
    a = asset(tmp_path / 'photo.jpg')
    assert source_key(a, True) != source_key(a, False)
    before = source_key(a, False)
    a.primary_path.write_bytes(b'new source')
    assert source_key(a, False) != before


def test_preview_read_only_and_unknown_origin(tmp_path):
    preview = tmp_path / 'preview.jpg'
    Image.new('RGB', (32, 24)).save(preview)
    a = asset(tmp_path / 'source.jpg', preview)
    before = preview.read_bytes()
    frame = load_preview(a)
    assert frame.preview
    assert frame.source_kind == 'cached_preview'
    assert frame.image.size == (32, 24)
    assert preview.read_bytes() == before
    assert a.preview_path == preview
    assert not a.primary_path.exists()
    frame.close()


def test_preview_precedes_full_resolution(tmp_path):
    started, release = Event(), Event()
    def decode(a, **kwargs):
        started.set()
        assert release.wait(3)
        return DisplayFrame(Image.new('RGB', (40, 30)), 'paired_jpeg')
    loader = ViewerLoader(decoder=decode, preview_loader=lambda a: DisplayFrame(Image.new('RGB', (4, 3)), 'cached_preview', True))
    try:
        generation = loader.submit(asset(tmp_path / 'a.jpg'))
        assert started.wait(3)
        preview = loader.results.get(timeout=3)
        assert preview.generation == generation and preview.frame.preview
        preview.frame.close()
        release.set()
        full = loader.results.get(timeout=3)
        assert full.generation == generation and full.frame.image.size == (40, 30)
        full.frame.close()
    finally:
        release.set()
        close_loader(loader)


def test_latest_pending_generation_only_and_old_decode_discarded(tmp_path):
    started, release = Event(), Event()
    calls, frames = [], []
    def decode(a, **kwargs):
        calls.append(a.stem)
        if a.stem == 'first':
            started.set()
            assert release.wait(3)
        frame = DisplayFrame(Image.new('RGB', (8, 8)), 'paired_jpeg')
        frames.append(frame)
        return frame
    loader = ViewerLoader(decoder=decode, preview_loader=lambda a: None)
    try:
        loader.submit(asset(tmp_path / 'first.jpg'))
        assert started.wait(3)
        loader.submit(asset(tmp_path / 'skipped.jpg'))
        latest = loader.submit(asset(tmp_path / 'last.jpg'))
        release.set()
        result = loader.results.get(timeout=3)
        assert result.generation == latest
        assert calls == ['first', 'last']
        with pytest.raises(ValueError):
            frames[0].image.getpixel((0, 0))
        result.frame.close()
        assert loader.results.empty()
    finally:
        release.set()
        close_loader(loader)


def test_close_during_decode_drops_result_and_releases_cache(tmp_path):
    started, release = Event(), Event()
    frames = []
    def decode(a, **kwargs):
        started.set()
        assert release.wait(3)
        frame = DisplayFrame(Image.new('RGB', (8, 8)), 'paired_jpeg')
        frames.append(frame)
        return frame
    loader = ViewerLoader(decoder=decode, preview_loader=lambda a: None)
    loader.submit(asset(tmp_path / 'a.jpg'))
    assert started.wait(3)
    loader.close()
    release.set()
    loader.thread.join(3)
    assert not loader.thread.is_alive()
    assert loader.results.empty()
    assert loader.cache.bytes_used == 0
    with pytest.raises(ValueError):
        frames[0].image.getpixel((0, 0))


def test_browsing_reuses_cache_and_full_raw_is_explicit(tmp_path):
    calls = []
    def decode(a, *, full_raw):
        calls.append(full_raw)
        return DisplayFrame(Image.new('RGB', (8, 8)), 'raw_full_decode' if full_raw else 'raw_embedded_jpeg')
    loader = ViewerLoader(decoder=decode, preview_loader=lambda a: None)
    a = asset(tmp_path / 'a.raw')
    try:
        for full_raw in (False, False, True, True):
            generation = loader.submit(a, full_raw=full_raw)
            result = loader.results.get(timeout=3)
            assert result.generation == generation
            assert result.frame.source_kind == ('raw_full_decode' if full_raw else 'raw_embedded_jpeg')
            result.frame.close()
        assert calls == [False, True]
        assert not a.auto_rejected and a.focus_score is None and a.ai_focus_result is None
    finally:
        close_loader(loader)


def test_decode_failure_keeps_preview_and_reports_error(tmp_path):
    def fail(*args, **kwargs):
        raise OSError('damaged source')
    loader = ViewerLoader(decoder=fail, preview_loader=lambda a: DisplayFrame(Image.new('RGB', (4, 3)), 'cached_preview', True))
    try:
        loader.submit(asset(tmp_path / 'a.jpg'))
        preview = loader.results.get(timeout=3)
        error = loader.results.get(timeout=3)
        assert preview.frame.preview
        assert error.frame is None
        assert error.error == 'OSError: damaged source'
        preview.frame.close()
    finally:
        close_loader(loader)


def test_real_jpg_decoder_uses_oriented_original_without_mutating_asset(tmp_path):
    from ai_cull_assistant.image_viewer import load_source
    path = tmp_path / 'source.jpg'
    exif = Image.Exif()
    exif[274] = 6
    Image.new('RGB', (30, 20), 'blue').save(path, exif=exif)
    before = path.read_bytes()
    a = asset(path)
    frame = load_source(a)
    try:
        assert frame.image.size == (20, 30)
        assert frame.source_kind == 'standard_image'
        assert path.read_bytes() == before
        assert a.focus_score is None and a.ai_focus_result is None
    finally:
        frame.close()


def test_review_viewer_entrypoint_uses_filtered_assets_and_preserves_system_open(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from ai_cull_assistant.ai_review_ui import ReviewDialog
    import ai_cull_assistant.image_viewer as module
    first, second = asset(tmp_path / 'a.jpg'), asset(tmp_path / 'b.jpg')
    photos = {'b': {'asset': second}, 'a': {'asset': first}}
    calls = []
    monkeypatch.setattr(module, 'ImageViewer', lambda parent, assets, index: calls.append((parent, assets, index)))
    view = SimpleNamespace(_photo_ids=['b', 'a'], _selected_photo_id=lambda: 'a',
                           _photos=lambda: photos, _asset_for_photo=lambda photo: photo['asset'])
    ReviewDialog._open_original(view)
    assert calls == [(view, [second, first], 1)]
    assert callable(ReviewDialog._open_system_original)
