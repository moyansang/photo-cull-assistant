from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from ai_cull_assistant import preview
from ai_cull_assistant.models import PhotoAsset


@pytest.mark.parametrize('flip,turns', [(0,0),(3,2),(5,1),(6,3)])
def test_raw_thumbnail_flip_preserves_pixel_coordinates(flip, turns):
    pixels = np.arange(18, dtype=np.uint8).reshape(2,3,3)
    actual = preview._orient_raw_thumbnail(Image.fromarray(pixels), flip)
    assert np.array_equal(np.asarray(actual), np.rot90(pixels, turns))


def test_embedded_exif_orientation_is_not_applied_twice():
    pixels = np.arange(18, dtype=np.uint8).reshape(2,3,3)
    image = Image.fromarray(pixels)
    image.getexif()[274] = 8
    actual = preview._orient_raw_thumbnail(image, 5)
    assert np.array_equal(np.asarray(actual), np.rot90(pixels))
    assert actual.getexif().get(274) is None


def test_postprocessed_fallback_is_already_oriented(tmp_path, monkeypatch):
    class Raw:
        sizes = SimpleNamespace(flip=5)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_thumb(self): raise ValueError('no embedded preview')
        def postprocess(self, **kw): return np.zeros((3,2,3), dtype=np.uint8)
    monkeypatch.setattr(preview, 'rawpy', SimpleNamespace(imread=lambda p: Raw()))
    assert preview._load_raw_preview(tmp_path/'a.RW2').size == (2,3)


def test_missing_legacy_preview_retains_old_coordinate_system(tmp_path, monkeypatch):
    source = tmp_path/'a.RW2'
    source.write_bytes(b'raw')
    legacy = tmp_path/'cache/v04/a.jpg'
    asset = PhotoAsset('a', source, source, source, None, datetime.now(), '.rw2', preview_path=legacy)
    requested = []
    monkeypatch.setattr(preview, '_load_raw_preview', lambda p, **kw:
                        requested.append(kw['apply_orientation']) or Image.new('RGB',(30,20)))
    restored = preview.ensure_preview(asset)
    assert restored == legacy and requested == [False]
    new = preview.build_preview(asset, tmp_path/'new')
    assert new.parent.name == 'v05' and requested == [False,True]


def test_explicit_preview_directory_rehomes_stale_existing_preview(tmp_path):
    source = tmp_path / 'photos' / 'a.jpg'
    source.parent.mkdir()
    Image.new('RGB', (20, 30), 'white').save(source)
    stale = tmp_path / 'old-workspace' / 'previews' / 'v04' / 'a.jpg'
    stale.parent.mkdir(parents=True)
    Image.new('RGB', (10, 15), 'black').save(stale)
    stale_bytes = stale.read_bytes()
    asset = PhotoAsset('a', source, source, None, source, datetime.now(), '.jpg', preview_path=stale)

    current = tmp_path / 'new-workspace' / 'previews'
    rebuilt = preview.ensure_preview(asset, current)

    assert rebuilt == current / 'v04' / 'a.jpg'
    assert rebuilt.is_file() and asset.preview_path == rebuilt
    assert stale.read_bytes() == stale_bytes
