"""Actual decoder-call hooks; RAW backend here is explicitly synthetic."""
from types import SimpleNamespace

import numpy as np
from PIL import Image

from ai_cull_assistant import image_decode as decode, measurement_receipts as mr


def observe(monkeypatch):
    calls = []
    monkeypatch.setattr(mr, 'recording_active', lambda: True)
    monkeypatch.setattr(mr, 'observe_decode', lambda path, config: calls.append((path, config)))
    decode.clear_decode_cache()
    return calls


def test_disabled_observer_does_not_evaluate_configuration(monkeypatch):
    monkeypatch.setattr(mr, 'recording_active', lambda: False)
    def forbidden():
        raise AssertionError('disabled recording evaluated decoder configuration')
    decode._observe_decode(None, forbidden)


def test_standard_actual_call_then_explicit_decode_cache_gap(tmp_path, monkeypatch):
    calls = observe(monkeypatch)
    path = tmp_path / 'synthetic.png'
    Image.new('RGB', (20, 30), 'blue').save(path)
    with decode.load_standard_image(path) as first:
        original = first.tobytes(), dict(first.info)
    with decode.load_standard_image(path) as second:
        assert (second.tobytes(), second.info) == original
    assert calls[0][1]['kind'] == 'actual_decoder_call'
    assert calls[0][1]['orientation'] == 'exif_transpose'
    assert calls[1][1]['kind'] == 'decode_cache_reuse'
    assert calls[1][1]['status'] == 'unverifiable_cached_decode'
    decode.clear_decode_cache()


def test_synthetic_raw_records_exact_postprocess_kwargs(tmp_path, monkeypatch):
    calls = observe(monkeypatch)
    actual = []
    class Raw:
        sizes = SimpleNamespace(width=20, height=30)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def postprocess(self, **kwargs):
            actual.append(kwargs)
            return np.zeros((30, 20, 3), dtype='uint8')
    backend = SimpleNamespace(imread=lambda path: Raw(), ColorSpace=SimpleNamespace(sRGB='synthetic_srgb'),
                              __version__='SYNTHETIC-STUB', libraw_version=(0, 0, 0))
    path = tmp_path / 'synthetic.raw'
    path.write_bytes(b'not a real RAW: explicit injected dependency stub')
    with decode.load_raw_image(path, full_raw=True, backend=backend) as image:
        assert image.size == (20, 30)
    config = calls[0][1]
    assert config['postprocess'] == actual[0]
    assert config['injected_backend'] is True
    assert config['backend_version'] == 'SYNTHETIC-STUB'
    assert config['orientation'] == 'LibRaw-postprocess-only'


def test_observer_metadata_failure_does_not_fail_decode(monkeypatch):
    calls = observe(monkeypatch)
    def invalid_metadata():
        raise TypeError('synthetic unavailable metadata')
    decode._observe_decode(None, invalid_metadata)
    assert calls[0][1] == {'kind': 'configuration_unverifiable', 'status': 'unverifiable'}
