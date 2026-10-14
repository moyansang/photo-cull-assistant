"""Regression cases for replacement races, cache publication and ownership.

Small generated images exercise filesystem/Pillow behavior. Injected backends
exercise failure lifetimes only and do not claim additional camera support.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
import os
from pathlib import Path
from threading import BoundedSemaphore, Event
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageCms, JpegImagePlugin

from ai_cull_assistant import image_decode as decode, preview
from ai_cull_assistant.models import PhotoAsset
import importlib

shared = importlib.import_module('ai_cull_assistant.shared_decode')


def photo(path, *, raw=None):
    return PhotoAsset(path.stem, path, raw or path, raw, path,
                      datetime(2026, 10, 13), path.suffix.lower())


def write_rgb(path, color='red', size=(80, 60)):
    with Image.new('RGB', size, color) as image:
        image.save(path)


def replace_preserving_size_and_mtime(path, color):
    before = path.stat()
    replacement = path.with_name('replacement' + path.suffix)
    write_rgb(replacement, color)
    assert replacement.stat().st_size == before.st_size
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, path)
    assert path.stat().st_mtime_ns == before.st_mtime_ns


@pytest.fixture(autouse=True)
def empty_cache():
    decode.clear_decode_cache()
    yield
    decode.clear_decode_cache()


def test_same_path_equal_size_preserved_mtime_replacement_invalidates_rgb(tmp_path):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    with decode.load_standard_image(path) as first:
        assert first.getpixel((0, 0)) == (255, 0, 0)
    replace_preserving_size_and_mtime(path, 'blue')
    with decode.load_standard_image(path) as replaced:
        assert replaced.getpixel((0, 0)) == (0, 0, 255)


def test_ensure_preview_rebuilds_after_original_is_replaced(tmp_path):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    asset = photo(path)
    first = preview.build_preview(asset, tmp_path / 'cache')
    replace_preserving_size_and_mtime(path, 'blue')
    rebuilt = preview.ensure_preview(asset, tmp_path / 'cache')
    assert rebuilt == first
    with Image.open(rebuilt) as image:
        red, _, blue = image.getpixel((0, 0))
        assert blue > 240 and red < 10


def test_build_preview_rejects_truncated_jpeg_that_verify_accepts(tmp_path):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    asset = photo(path)
    cached = preview.build_preview(asset, tmp_path / 'cache')
    cached.write_bytes(cached.read_bytes()[:-10])
    # Pillow's JPEG verify() validates the header, not the entropy data.
    with Image.open(cached) as truncated:
        truncated.verify()
    with pytest.raises(OSError), Image.open(cached) as truncated:
        truncated.load()
    rebuilt = preview.build_preview(asset, tmp_path / 'cache')
    with Image.open(rebuilt) as image:
        image.load()
        assert image.size == (80, 60)


def test_clear_during_decode_does_not_repopulate_cache(tmp_path):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    started, finish = Event(), Event()

    def slow_load():
        started.set()
        assert finish.wait(5)
        return Image.new('RGB', (80, 60), 'blue')

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(decode._cached_load, path, 'test', slow_load)
        try:
            assert started.wait(5)
            decode.clear_decode_cache()
        finally:
            finish.set()
        with future.result(timeout=5) as owned:
            assert owned.getpixel((0, 0)) == (0, 0, 255)
            assert decode.decode_cache_info()['entries'] == 0


def test_changed_source_during_decode_is_not_published_under_old_key(tmp_path):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)

    def replace_after_read():
        image = Image.new('RGB', (80, 60), 'red')
        replace_preserving_size_and_mtime(path, 'blue')
        return image

    with decode._cached_load(path, 'test', replace_after_read) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
    assert decode.decode_cache_info()['entries'] == 0


def test_cache_copy_failure_closes_decoded_owner(tmp_path, monkeypatch):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    decoded = Image.new('RGB', (80, 60), 'red')

    def fail_copy(_image):
        raise MemoryError('cache copy allocation failed')

    monkeypatch.setattr(decode, '_owned_copy', fail_copy)
    with pytest.raises(MemoryError):
        decode._cached_load(path, 'test', lambda: decoded)
    with pytest.raises(ValueError, match='closed'):
        decoded.getpixel((0, 0))
    assert decode.decode_cache_info()['entries'] == 0


def test_missing_raw_during_upgrade_keeps_usable_jpeg(tmp_path, monkeypatch):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    # A backend is available, but the originally paired RAW was moved/deleted.
    monkeypatch.setattr(decode, 'rawpy', SimpleNamespace())
    asset = photo(path, raw=tmp_path / 'missing.NEF')
    with decode.load_analysis_image(asset) as image:
        evidence = image.info['decode_evidence']
        assert image.getpixel((0, 0)) == (255, 0, 0)
        assert evidence['evidence_sufficient'] is False
        assert any('full_raw_unavailable' in value for value in evidence['warnings'])


def test_embedded_jpeg_dimension_limit_precedes_pixel_decode(tmp_path, monkeypatch):
    stream = BytesIO()
    with Image.new('RGB', (80, 60), 'red') as image:
        image.save(stream, 'JPEG')
    loads, full, closed = [], [], []
    original_load = JpegImagePlugin.JpegImageFile.load

    def tracked_load(self, *args, **kwargs):
        loads.append(self.size)
        return original_load(self, *args, **kwargs)

    class Raw:
        sizes = SimpleNamespace(width=80, height=60, flip=0)
        def __enter__(self): return self
        def __exit__(self, *args): closed.append(True)
        def extract_thumb(self):
            return SimpleNamespace(format='jpeg', data=stream.getvalue())
        def postprocess(self, **kwargs):
            full.append(True)
            return np.zeros((60, 80, 3), dtype=np.uint8)

    backend = SimpleNamespace(imread=lambda _: Raw(),
                              ThumbFormat=SimpleNamespace(JPEG='jpeg', BITMAP='bitmap'))
    monkeypatch.setattr(decode, 'MAX_IMAGE_PIXELS', 100)
    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, 'load', tracked_load)
    with pytest.raises(decode.DecodeError, match='内存保护'):
        decode.load_raw_image(tmp_path / 'small.NEF', backend=backend)
    assert loads == []
    assert full == [] and closed == [True]


def test_corrupt_rgba_icc_releases_temporary_alpha(tmp_path, monkeypatch):
    path = tmp_path / 'photo.png'
    with Image.new('RGBA', (80, 60), (255, 0, 0, 128)) as image:
        image.save(path, icc_profile=b'invalid ICC')
    channels = []
    original_channel = Image.Image.getchannel

    def tracked_channel(self, channel):
        result = original_channel(self, channel)
        channels.append(result)
        return result

    monkeypatch.setattr(Image.Image, 'getchannel', tracked_channel)
    with decode.load_standard_image(path) as image:
        assert image.info['decode_evidence']['color_space'] == 'unverified'
    assert channels
    for channel in channels:
        with pytest.raises(ValueError, match='closed'):
            channel.getpixel((0, 0))


def test_valid_but_incompatible_icc_keeps_pixels_and_marks_color_unverified(tmp_path):
    path = tmp_path / 'wrong-profile.png'
    # A syntactically valid LAB profile cannot transform an RGB source. Pillow
    # raises PyCMSError, which does not inherit OSError or ValueError.
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('LAB')).tobytes()
    image = Image.new('RGB', (80, 60), 'red')
    try:
        image.save(path, icc_profile=profile)
    finally:
        image.close()
    decoded = decode.load_standard_image(path)
    try:
        assert decoded.getpixel((0, 0)) == (255, 0, 0)
        assert decoded.info['decode_evidence']['color_space'] == 'unverified'
        assert 'icc_conversion_unavailable:PyCMSError' in decoded.info['decode_evidence']['warnings']
    finally:
        decoded.close()


def test_shared_scope_releases_capacity_even_when_image_close_raises(monkeypatch):
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(shared, '_scope_slots', slots)

    class BrokenClose:
        def close(self): raise OSError('simulated close failure')

    asset = object()
    with pytest.raises(OSError, match='close failure'), shared.shared_decode(asset):
        with shared.preview_image(asset, BrokenClose):
            pass
    acquired = slots.acquire(blocking=False)
    assert acquired, 'A close error permanently consumed the scan scope slot'
    if acquired:
        slots.release()
    assert shared.decoded_image(asset) is None


def test_unscoped_borrow_closes_caller_retained_image(monkeypatch):
    owned = Image.new('RGB', (80, 60), 'red')
    monkeypatch.setattr('ai_cull_assistant.face_focus.load_full_image', lambda _: owned)
    with shared.full_image(object()) as borrowed:
        assert borrowed.getpixel((0, 0)) == (255, 0, 0)
    # Image.Image.__exit__ does not destroy the pixel core in Pillow.
    with pytest.raises(ValueError, match='closed'):
        borrowed.getpixel((0, 0))


def test_shared_scope_invalidates_replaced_source_without_closing_borrower(tmp_path):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    asset = photo(path)
    with shared.shared_decode(asset):
        with shared.preview_image(asset, lambda: decode.load_standard_image(path)) as old:
            replace_preserving_size_and_mtime(path, 'blue')
            assert shared.decoded_image(asset) is None
            with shared.preview_image(asset, lambda: decode.load_standard_image(path)) as current:
                assert current.getpixel((0, 0)) == (0, 0, 255)
                assert old.getpixel((0, 0)) == (255, 0, 0)
        with pytest.raises(ValueError, match='closed'):
            old.getpixel((0, 0))


def test_legacy_unstamped_preview_survives_offline_original(tmp_path, monkeypatch):
    missing = tmp_path / 'offline-volume' / 'photo.bmp'
    existing = tmp_path / 'cache' / 'v04' / 'photo.jpg'
    existing.parent.mkdir(parents=True)
    write_rgb(existing, 'blue', size=(60, 80))
    asset = photo(missing)
    asset.preview_path = existing
    original = existing.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError('Offline preview reuse must not start a source decode')

    monkeypatch.setattr(preview, '_load_standard_image', forbidden)
    assert preview.ensure_preview(asset) == existing
    assert preview.build_preview(asset, existing.parent.parent, legacy_orientation=True) == existing
    assert existing.read_bytes() == original
    with Image.open(existing) as image:
        assert image.size == (60, 80)


@pytest.mark.parametrize('unavailable', ['missing', 'denied'])
def test_stamped_preview_survives_unavailable_source_without_new_analysis(tmp_path, monkeypatch, unavailable):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    asset = photo(path)
    cached = preview.build_preview(asset, tmp_path / 'cache')
    before = cached.read_bytes()
    if unavailable == 'missing':
        path.unlink()
    else:
        original_signature = decode.source_signature
        def denied_signature(candidate):
            if Path(candidate) == path:
                raise PermissionError('simulated inaccessible source volume')
            return original_signature(candidate)
        monkeypatch.setattr(decode, 'source_signature', denied_signature)

    def forbidden(*args, **kwargs):
        raise AssertionError('Cached display must not start new analysis')

    monkeypatch.setattr(decode, 'load_analysis_image', forbidden)
    assert preview.ensure_preview(asset) == cached
    assert cached.read_bytes() == before
    with pytest.raises(decode.DecodeError, match='源文件'):
        decode.load_standard_image(path)


def test_unchanged_preview_hit_validates_pixels_once(tmp_path, monkeypatch):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    asset = photo(path)
    cached = preview.build_preview(asset, tmp_path / 'cache')
    opens = []
    original_open = Image.open

    def tracked_open(filename, *args, **kwargs):
        opens.append(filename)
        return original_open(filename, *args, **kwargs)

    monkeypatch.setattr(Image, 'open', tracked_open)
    for _ in range(8):
        assert preview.ensure_preview(asset) == cached
    assert opens == [cached]


def test_preview_save_failure_preserves_old_file_and_removes_partial(tmp_path, monkeypatch):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    asset = photo(path)
    cache_root = tmp_path / 'cache'
    cached = preview.build_preview(asset, cache_root)
    original = cached.read_bytes()
    replace_preserving_size_and_mtime(path, 'blue')

    def interrupted_save(self, file, *args, **kwargs):
        file.write(b'incomplete new JPEG')
        raise OSError('simulated full disk')

    monkeypatch.setattr(Image.Image, 'save', interrupted_save)
    with pytest.raises(OSError, match='full disk'):
        preview.build_preview(asset, cache_root)
    assert cached.read_bytes() == original
    assert sorted(cached.parent.iterdir()) == [cached]


def test_waiting_decode_stats_only_after_acquiring_slot(tmp_path, monkeypatch):
    path = tmp_path / 'photo.bmp'
    write_rgb(path)
    with decode.load_standard_image(path):
        pass
    waiting, allowed = Event(), Event()

    class DelayedSlot:
        def __enter__(self):
            waiting.set()
            assert allowed.wait(5)
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(decode, '_decode_slots', DelayedSlot())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(decode.load_standard_image, path)
        try:
            assert waiting.wait(5)
            replace_preserving_size_and_mtime(path, 'blue')
        finally:
            allowed.set()
        image = future.result(timeout=5)
        try:
            assert image.getpixel((0, 0)) == (0, 0, 255)
        finally:
            image.close()
