from threading import Event
from types import SimpleNamespace

from PIL import Image

from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.focus_prefetch import FocusPrefetch


def test_background_prepares_only_following_evidence_and_stops(tmp_path, monkeypatch):
    import ai_cull_assistant.ai_focus as focus
    import ai_cull_assistant.focus_image_cache as cache
    import ai_cull_assistant.sheet_thumbnail as sheet
    import ai_cull_assistant.preview as preview
    import ai_cull_assistant.ai_api as api
    entered, release, stop = Event(), Event(), Event()
    prepared, uploads, thumbs = [], [], []
    a = SimpleNamespace(primary_path=tmp_path/'a.jpg', clarity_evidence={})
    b = SimpleNamespace(primary_path=tmp_path/'b.jpg', clarity_evidence={})
    monkeypatch.setattr(preview, 'ensure_preview', lambda *_: None)
    monkeypatch.setattr(sheet, 'prepare_thumbnails', lambda asset: thumbs.append(asset.primary_path.name))
    monkeypatch.setattr(focus, '_subject_faces', lambda *_: [(None, (.1,.1,.2,.2))])
    monkeypatch.setattr(api, 'call_model', lambda *_: (_ for _ in ()).throw(AssertionError('no API in preparation')))
    monkeypatch.setattr(api, '_image_content', lambda paths, **kw: uploads.append(paths))
    def prepare(asset, *_args, **_kw):
        prepared.append(asset.primary_path.name)
        entered.set()
        assert release.wait(5)
        return [tmp_path/'detail.png'], True
    monkeypatch.setattr(cache, 'cached_focus_images', prepare)
    worker = FocusPrefetch(stop, tmp_path, tmp_path, CropSettings())
    try:
        worker.schedule(a, b)
        assert entered.wait(3)  # caller remains free to wait for the API
        assert prepared == ['b.jpg']
        stop.set()
        release.set()
        worker.wait()
        assert uploads == []
        worker.schedule(a, b)
        assert prepared == ['b.jpg']
        assert thumbs == ['a.jpg', 'b.jpg']
    finally:
        release.set()
        worker.close()


def test_optional_worker_failure_does_not_fail_review(tmp_path, monkeypatch):
    worker = FocusPrefetch(Event(), tmp_path, tmp_path, CropSettings())
    monkeypatch.setattr(worker, '_prepare', lambda *_: (_ for _ in ()).throw(OSError('disk unavailable')))
    try:
        worker.schedule(None, None)
        worker.wait()
        assert worker.future is None
    finally:
        worker.close()


def test_sheet_thumbnail_reused_and_invalidated(tmp_path, monkeypatch):
    from ai_cull_assistant.sheet_thumbnail import thumbnail
    from PIL import ImageOps
    path = tmp_path/'photo.jpg'
    Image.new('RGB', (80,100), 'red').save(path)
    asset = SimpleNamespace(preview_path=path)
    original = ImageOps.contain
    calls = []
    monkeypatch.setattr(ImageOps, 'contain', lambda *a, **kw: (calls.append(True), original(*a, **kw))[1])
    with Image.open(path) as image:
        first = thumbnail(asset, image, (40,40))
        second = thumbnail(asset, image, (40,40))
        assert first.tobytes() == second.tobytes() and len(calls) == 1
        thumbnail(asset, image, (30,30)).close()
    Image.new('RGB', (90,110), 'blue').save(path)
    with Image.open(path) as image:
        third = thumbnail(asset, image, (40,40))
        assert third.tobytes() != first.tobytes() and len(calls) == 3
    first.close(); second.close(); third.close()


def test_upload_encoding_reused_bounded_and_content_sensitive(monkeypatch):
    from ai_cull_assistant import upload_image_cache as cache
    cache.clear()
    real = cache.base64.b64encode
    calls = []
    monkeypatch.setattr(cache.base64, 'b64encode', lambda data: (calls.append(data), real(data))[1])
    monkeypatch.setattr(cache, 'MAX_BYTES', 16)
    try:
        warmed = cache.encode(b'photo', warm=True)
        assert cache.encode(b'photo') == warmed and len(calls) == 1
        assert cache.encode(b'other') != warmed
        for value in range(10):
            cache.encode(bytes([value])*5, warm=True)
        assert sum(map(len, cache._items.values())) <= 16
    finally:
        cache.clear()
