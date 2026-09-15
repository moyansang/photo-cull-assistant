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
    from ai_cull_assistant.scan_diagnostics import collect_diagnostics
    class Raw:
        sizes = SimpleNamespace(flip=5)
        raw_image = np.zeros((2,3), dtype=np.uint16)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_thumb(self): raise ValueError('no embedded preview')
        def postprocess(self, **kw): return np.zeros((3,2,3), dtype=np.uint8)
    monkeypatch.setattr(preview, 'rawpy', SimpleNamespace(imread=lambda p: Raw()))
    with collect_diagnostics() as row:
        assert preview._load_raw_preview(tmp_path/'a.RW2').size == (2,3)
    for key in ('raw_preview_open', 'raw_preview_extract_thumb',
                'raw_preview_unpack', 'raw_preview_postprocess', 'raw_preview_half_decode'):
        assert row['operations'][key]['count'] == 1


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
