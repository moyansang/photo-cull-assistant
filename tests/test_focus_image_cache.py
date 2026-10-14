from datetime import datetime
from types import SimpleNamespace

from PIL import Image
import pytest

from ai_cull_assistant import ai_focus
from ai_cull_assistant.ai_api import ApiError
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.focus_image_cache import cached_focus_images, clear_focus_images


def setup(tmp_path, monkeypatch):
    path = tmp_path/'source.png'
    Image.new('RGB', (800, 1000), 'gray').save(path)
    asset = PhotoAsset('source', path, path, None, path, datetime.now(), '.png', preview_path=path)
    subject = SimpleNamespace(face=(.2,.2,.3,.3), landmarks=None)
    monkeypatch.setattr(ai_focus, 'detail_features', lambda *args: subject)
    return asset, subject, tmp_path/'workspace/cache/analysis'


def test_api_failure_retry_and_different_provider_reuse_native_images(tmp_path, monkeypatch):
    asset, subject, cache = setup(tmp_path, monkeypatch)
    decodes = []
    monkeypatch.setattr(ai_focus, 'load_full_image', lambda asset: decodes.append(True) or Image.open(asset.primary_path))
    profile = dict(id='test', model='vision', base_url='https://example.test')
    def fail(*args): raise ApiError('timeout')
    monkeypatch.setattr(ai_focus, 'call_model', fail)
    for model in ('vision', 'other-vision'):
        profile['model'] = model
        with pytest.raises(ApiError):
            ai_focus.review_focus(asset, CropSettings(), profile, cache)
    assert len(decodes) == 1
    # Manual face edits invalidate just this geometry.
    cached_focus_images(asset, CropSettings(), cache, (.1,.1,.2,.2), subject=subject)
    assert len(decodes) == 2


def test_scan_warms_pending_images_without_second_decode(tmp_path, monkeypatch):
    from ai_cull_assistant import face_focus
    from ai_cull_assistant.shared_decode import full_image
    from ai_cull_assistant.screening import screen_assets, ScreeningResult
    asset, subject, cache = setup(tmp_path, monkeypatch)
    decodes = []
    monkeypatch.setattr(face_focus, 'load_full_image', lambda asset: decodes.append(True) or Image.open(asset.primary_path))
    def assess(asset, **kwargs):
        with full_image(asset) as image:
            assert image.size == (800, 1000)
        return ScreeningResult(False, 'face_focus_uncertain', True)
    monkeypatch.setattr(face_focus, 'assess_asset_focus', assess)
    screen_assets([asset], crop_settings=CropSettings(), cache_dir=cache)
    assert len(decodes) == 1
    monkeypatch.setattr(ai_focus, 'load_full_image', lambda asset: pytest.fail('unexpected second decode'))
    paths, reliable = cached_focus_images(asset, CropSettings(), cache, subject.face, subject=subject)
    assert reliable and all(p.is_file() for p in paths)
    clear_focus_images(cache)
    assert all(not p.exists() for p in paths)


def test_corrupt_detail_image_is_regenerated(tmp_path, monkeypatch):
    asset, subject, cache = setup(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(ai_focus, 'load_full_image', lambda asset: calls.append(True) or Image.open(asset.primary_path))
    paths, _ = cached_focus_images(asset, CropSettings(), cache, subject.face, subject=subject)
    paths[-1].write_bytes(b'bad')
    repaired, _ = cached_focus_images(asset, CropSettings(), cache, subject.face, subject=subject)
    assert len(calls) == 2
    with Image.open(repaired[-1]) as image: image.verify()
