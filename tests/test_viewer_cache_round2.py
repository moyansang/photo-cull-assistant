"""Round-two source-replacement regressions through the real ViewerLoader.

Only worker timing is controlled. Images are actual files decoded by the shared
source loader; no Tk window, network, paid model, or camera support is implied.
"""
from datetime import datetime
import os
from pathlib import Path
from threading import Event

from PIL import Image
import pytest

from ai_cull_assistant.image_decode import clear_decode_cache
from ai_cull_assistant.image_viewer import BrowseCache, ViewerLoader, load_source
from ai_cull_assistant.models import PhotoAsset


def asset(path):
    return PhotoAsset(path.stem, path, path, None, None, datetime(2026, 10, 13), path.suffix)


def replace_preserving_size_mtime(path, color):
    """Atomic replacement preserves old weak key while changing source bytes."""
    before = path.stat()
    replacement = path.with_name('replacement-' + path.name)
    Image.new('RGB', (80, 64), color).save(replacement, format='BMP')
    assert replacement.stat().st_size == before.st_size
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    os.replace(replacement, path)
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.stat().st_size == before.st_size


@pytest.fixture(autouse=True)
def decode_cache_boundary():
    clear_decode_cache()
    yield
    clear_decode_cache()


def stop(loader):
    loader.close()
    loader.thread.join(3)
    assert not loader.thread.is_alive()


def result(loader, photo):
    generation = loader.submit(photo)
    value = loader.results.get(timeout=3)
    assert value.generation == generation
    return value


def test_same_path_size_and_mtime_replacement_does_not_hit_old_browse_cache(tmp_path):
    path = tmp_path / 'photo.bmp'
    Image.new('RGB', (80, 64), 'red').save(path)
    photo = asset(path)
    calls = []
    def decode(a, **kwargs):
        calls.append(a.primary_path)
        return load_source(a, **kwargs)
    loader = ViewerLoader(decoder=decode)
    try:
        first = result(loader, photo)
        assert first.error is None
        assert first.frame.image.getpixel((0, 0)) == (255, 0, 0)
        first.frame.close()
        replace_preserving_size_mtime(path, 'blue')
        second = result(loader, photo)
        try:
            assert second.error is None
            assert second.frame.image.getpixel((0, 0)) == (0, 0, 255)
            assert len(calls) == 2
        finally:
            if second.frame is not None:
                second.frame.close()
    finally:
        stop(loader)


@pytest.mark.parametrize('replace_before_read', [False, True], ids=['after-native-read', 'before-native-read'])
def test_replacement_during_decode_neither_caches_nor_publishes_an_unversioned_frame(tmp_path, replace_before_read):
    path = tmp_path / 'photo.bmp'
    Image.new('RGB', (80, 64), 'red').save(path)
    photo = asset(path)
    entered, release = Event(), Event()
    calls, frames = [], []
    def decode(a, **kwargs):
        calls.append(a.primary_path)
        if replace_before_read:
            entered.set()
            assert release.wait(3)
        frame = load_source(a, **kwargs)
        frames.append(frame)
        if not replace_before_read:
            entered.set()
            assert release.wait(3)
        return frame
    cache = BrowseCache()
    loader = ViewerLoader(decoder=decode, cache=cache)
    try:
        generation = loader.submit(photo)
        assert entered.wait(3)
        replace_preserving_size_mtime(path, 'blue')
        release.set()
        changed = loader.results.get(timeout=3)
        try:
            assert changed.generation == generation
            assert changed.frame is None, 'changed source pixels must not be published as a successful frame'
            assert changed.error and '源文件' in changed.error
            assert cache.bytes_used == 0 and not cache._items
            assert len(calls) == 1, 'source-change recovery must not retry without a new request'
            with pytest.raises(ValueError):
                frames[0].image.getpixel((0, 0))
        finally:
            if changed.frame is not None:
                changed.frame.close()
        # A later explicit request succeeds on the new version without a loop.
        recovered = result(loader, photo)
        try:
            assert recovered.error is None
            assert recovered.frame.image.getpixel((0, 0)) == (0, 0, 255)
            assert len(calls) == 2
        finally:
            if recovered.frame is not None:
                recovered.frame.close()
    finally:
        release.set()
        stop(loader)


def test_replacement_during_cache_hit_is_not_published(tmp_path):
    path = tmp_path / 'photo.bmp'
    Image.new('RGB', (80, 64), 'red').save(path)
    photo = asset(path)
    class ReplacingCache(BrowseCache):
        replace_on_hit = False
        def get(self, key):
            frame = super().get(key)
            if frame is not None and self.replace_on_hit:
                self.replace_on_hit = False
                replace_preserving_size_mtime(path, 'blue')
            return frame
    cache = ReplacingCache()
    loader = ViewerLoader(cache=cache)
    try:
        first = result(loader, photo)
        first.frame.close()
        cache.replace_on_hit = True
        changed = result(loader, photo)
        try:
            assert changed.frame is None
            assert changed.error and '源文件' in changed.error
            assert cache.bytes_used == 0 and not cache._items
        finally:
            if changed.frame is not None:
                changed.frame.close()
    finally:
        stop(loader)


def test_replacement_during_cache_retention_is_discarded_before_publish(tmp_path):
    path = tmp_path / 'photo.bmp'
    Image.new('RGB', (80, 64), 'red').save(path)
    photo = asset(path)
    class ReplacingCache(BrowseCache):
        def put(self, key, frame):
            stored = super().put(key, frame)
            replace_preserving_size_mtime(path, 'blue')
            return stored
    cache = ReplacingCache()
    loader = ViewerLoader(cache=cache)
    try:
        changed = result(loader, photo)
        try:
            assert changed.frame is None
            assert changed.error and '源文件' in changed.error
            assert cache.bytes_used == 0 and not cache._items
        finally:
            if changed.frame is not None:
                changed.frame.close()
    finally:
        stop(loader)
