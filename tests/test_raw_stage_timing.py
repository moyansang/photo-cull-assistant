from types import SimpleNamespace

import numpy as np
import pytest
import rawpy

from ai_cull_assistant.face_focus import load_full_image
from ai_cull_assistant.scan_diagnostics import collect_diagnostics


@pytest.mark.parametrize('fail', [False, True])
def test_lazy_unpack_separated_and_raw_closed(monkeypatch, fail):
    events = []
    class Raw:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            events.append('close')
        @property
        def raw_image(self):
            events.append('unpack')
            return np.zeros((4, 4), np.uint16)
        def postprocess(self, **kwargs):
            assert events == ['open', 'unpack']
            assert kwargs == dict(use_camera_wb=True, half_size=False, no_auto_bright=True, output_bps=8)
            events.append('postprocess')
            if fail:
                raise ValueError('failed')
            return np.zeros((4, 4, 3), np.uint8)
    def opened(path):
        events.append('open')
        return Raw()
    monkeypatch.setattr(rawpy, 'imread', opened)
    from pathlib import Path
    asset = SimpleNamespace(raw_path=Path('test.RW2'))
    with collect_diagnostics() as record:
        if fail:
            with pytest.raises(ValueError):
                load_full_image(asset)
        else:
            with load_full_image(asset) as image:
                assert image.size == (4, 4)
    assert events == ['open', 'unpack', 'postprocess', 'close']
    ops = record['operations']
    for name in ('raw_full_decode', 'raw_full_open', 'raw_full_unpack', 'raw_full_postprocess'):
        assert ops[name]['count'] == 1
    assert ops['raw_full_decode']['seconds'] >= sum(ops[n]['seconds'] for n in (
        'raw_full_open', 'raw_full_unpack', 'raw_full_postprocess'))
