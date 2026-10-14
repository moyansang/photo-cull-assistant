from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import numpy as np
import pytest
from PIL import Image, ImageCms

from ai_cull_assistant import image_decode as decode, preview
from ai_cull_assistant.models import PhotoAsset, RAW_EXTENSIONS
from ai_cull_assistant.shared_decode import decoded_image, full_image, shared_decode


def asset(path, jpg=None):
    raw = path if path.suffix.lower() in RAW_EXTENSIONS else None
    return PhotoAsset(path.stem, jpg or path, path, raw, jpg,
                      datetime(2026, 10, 13), path.suffix.lower())


@pytest.fixture(autouse=True)
def empty_cache():
    decode.clear_decode_cache()
    yield
    decode.clear_decode_cache()


@pytest.fixture
def fake_raw(monkeypatch):
    calls = dict(open=0, thumb=0, full=[], active=0, peak=0)
    settings = dict(size=(1200, 800), broken=False, bitmap=False, fail_full=False,
                    flip=0, orientation=None, delay=0)
    class Raw:
        sizes = SimpleNamespace(width=2400, height=1600, flip=0)
        def __enter__(self):
            calls['active'] += 1
            calls['peak'] = max(calls['peak'], calls['active'])
            self.sizes.flip = settings['flip']
            return self
        def __exit__(self, *args):
            calls['active'] -= 1
        def extract_thumb(self):
            calls['thumb'] += 1
            if settings['broken']:
                raise RuntimeError('no thumbnail')
            image = Image.new('RGB', settings['size'], '#923412')
            time.sleep(settings['delay'])
            if settings['bitmap']:
                return SimpleNamespace(format='bitmap', data=np.asarray(image))
            output = BytesIO()
            exif = Image.Exif()
            if settings['orientation']:
                exif[274] = settings['orientation']
            image.save(output, 'JPEG', exif=exif)
            return SimpleNamespace(format='jpeg', data=output.getvalue())
        def postprocess(self, **kwargs):
            calls['full'].append(kwargs)
            if settings['fail_full']:
                raise RuntimeError('unsupported compression')
            return np.zeros((1600, 2400, 3), dtype='uint8')
    def imread(path):
        calls['open'] += 1
        return Raw()
    backend = SimpleNamespace(imread=imread, ThumbFormat=SimpleNamespace(JPEG='jpeg', BITMAP='bitmap'),
                              ColorSpace=SimpleNamespace(sRGB='srgb'))
    monkeypatch.setattr(decode, 'rawpy', backend)
    monkeypatch.setattr(preview, 'rawpy', backend)
    return calls, settings


def raw_asset(tmp_path, suffix='.rw2'):
    path = tmp_path / ('photo' + suffix)
    path.write_bytes(b'raw fixture: backend simulated')
    return asset(path)


def test_good_paired_jpg_is_full_size_and_never_opens_raw(tmp_path, fake_raw):
    photo = raw_asset(tmp_path)
    photo.jpg_path = tmp_path / 'photo.JPG'
    Image.new('RGB', (1800, 1200)).save(photo.jpg_path)
    with decode.load_analysis_image(photo) as image:
        assert image.size == (1800, 1200)
        assert image.info['decode_evidence']['source_kind'] == 'paired_jpeg'
    assert fake_raw[0]['open'] == 0


def test_bad_paired_jpg_falls_back_to_embedded_preview(tmp_path, fake_raw):
    photo = raw_asset(tmp_path)
    photo.jpg_path = tmp_path / 'photo.jpg'
    photo.jpg_path.write_bytes(b'truncated jpeg')
    with decode.load_analysis_image(photo) as image:
        assert image.size == (1200, 800)
        assert image.info['decode_evidence']['source_kind'] == 'raw_embedded_jpeg'
        assert any('paired_jpeg_unreadable' in warning for warning in image.info['decode_evidence']['warnings'])
    assert fake_raw[0]['thumb'] == 1 and not fake_raw[0]['full']


@pytest.mark.parametrize('suffix', sorted(RAW_EXTENSIONS))
def test_raw_extensions_follow_identical_embedded_first_policy(tmp_path, fake_raw, suffix):
    # A protocol test for supported extension routing, not a camera fixture test.
    with decode.load_analysis_image(raw_asset(tmp_path, suffix)) as image:
        assert image.info['decode_evidence']['source_kind'] == 'raw_embedded_jpeg'
    assert fake_raw[0]['thumb'] == 1 and not fake_raw[0]['full']


@pytest.mark.parametrize('failure', ['missing', 'too_small', 'region_too_small', 'detail_insufficient'])
def test_only_missing_or_insufficient_evidence_escalates(tmp_path, fake_raw, failure):
    photo = raw_asset(tmp_path)
    kwargs = {}
    if failure == 'missing':
        fake_raw[1]['broken'] = True
    elif failure == 'too_small':
        fake_raw[1]['size'] = (200, 100)
    elif failure == 'region_too_small':
        kwargs['evidence_regions'] = [(0.1, 0.1, .08, .08)]
    else:
        kwargs['insufficient_evidence'] = True
    with decode.load_analysis_image(photo, **kwargs) as image:
        assert image.size == (2400, 1600)
        assert image.info['decode_evidence']['source_kind'] == 'raw_full_decode'
    assert len(fake_raw[0]['full']) == 1
    assert fake_raw[0]['full'][0]['half_size'] is False
    assert fake_raw[0]['full'][0]['output_color'] == 'srgb'


def test_raw_upgrade_failure_keeps_preview_with_review_evidence(tmp_path, fake_raw):
    fake_raw[1].update(size=(100, 100), fail_full=True)
    with decode.load_analysis_image(raw_asset(tmp_path)) as image:
        evidence = image.info['decode_evidence']
        assert evidence['source_kind'] == 'raw_embedded_jpeg'
        assert evidence['evidence_sufficient'] is False
        assert any('full_raw_unavailable' in warning for warning in evidence['warnings'])


def test_jpg_insufficient_region_can_upgrade_but_group_score_is_not_an_api(tmp_path, fake_raw):
    photo = raw_asset(tmp_path)
    photo.jpg_path = tmp_path / 'paired.jpg'
    Image.new('RGB', (600, 400)).save(photo.jpg_path)
    with decode.load_analysis_image(photo, evidence_regions=[(.1,.1,.2,.2)]) as image:
        assert image.info['decode_evidence']['source_kind'] == 'raw_full_decode'
    with pytest.raises(TypeError):
        decode.load_analysis_image(photo, close_group_scores=True)


def test_viewing_small_raw_preview_does_not_trigger_analysis(tmp_path, fake_raw):
    fake_raw[1]['size'] = (100, 60)
    photo = raw_asset(tmp_path)
    image, kind = decode.load_display_image(photo)
    with image:
        assert image.size == (100, 60) and kind == 'raw_embedded_jpeg'
    assert not fake_raw[0]['full']
    image, kind = decode.load_display_image(photo, full_raw=True)
    with image:
        assert kind == 'raw_full_decode'
    assert len(fake_raw[0]['full']) == 1


def test_embedded_bitmap_orientation_and_no_double_rotation(tmp_path, fake_raw):
    fake_raw[1].update(size=(1200, 800), bitmap=True, flip=5)
    with decode.load_analysis_image(raw_asset(tmp_path)) as image:
        assert image.size == (800, 1200)
        assert image.info['decode_evidence']['source_kind'] == 'raw_embedded_bitmap'
    decode.clear_decode_cache()
    fake_raw[1].update(bitmap=False, orientation=6)
    with decode.load_analysis_image(raw_asset(tmp_path)) as image:
        assert image.size == (800, 1200)


def test_shared_preview_native_dimensions_and_nested_scope_reuse(tmp_path, fake_raw, monkeypatch):
    photo = raw_asset(tmp_path)
    monkeypatch.setattr('ai_cull_assistant.face_focus.load_full_image', decode.load_analysis_image)
    with shared_decode(photo):
        result = preview.build_preview(photo, tmp_path / 'cache')
        assert result.is_file()
        native = decoded_image(photo)
        with shared_decode(photo):
            with full_image(photo, evidence_regions=[(0, 0, 1, 1)]) as borrowed:
                assert borrowed is native and borrowed.size == (1200, 800)
        assert decoded_image(photo) is native
    assert fake_raw[0]['thumb'] == 1
    with pytest.raises(ValueError):
        native.getpixel((0, 0))


def test_shared_raw_escalation_is_reused_and_never_downgraded(tmp_path, fake_raw, monkeypatch):
    photo = raw_asset(tmp_path)
    monkeypatch.setattr('ai_cull_assistant.face_focus.load_full_image', decode.load_analysis_image)
    with shared_decode(photo):
        with full_image(photo) as image:
            assert image.size == (1200,800)
        with full_image(photo, insufficient_evidence=True) as image:
            assert image.size == (2400,1600)
        with full_image(photo, insufficient_evidence=True) as reused:
            assert reused is image
    assert len(fake_raw[0]['full']) == 1


def test_cache_returns_owned_copies_and_invalidation_on_source_change(tmp_path):
    path = tmp_path / 'a.bmp'
    Image.new('RGB', (20,30), 'red').save(path)
    with decode.load_standard_image(path) as image:
        image.putpixel((0,0), (0,0,0))
    with decode.load_standard_image(path) as image:
        assert image.getpixel((0,0)) == (255,0,0)
    Image.new('RGB', (21,30), 'blue').save(path)
    with decode.load_standard_image(path) as image:
        assert image.size == (21,30) and image.getpixel((0,0)) == (0,0,255)


def test_cache_byte_limit_evicts_and_does_not_close_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(decode, 'CACHE_BYTES', 2000)
    a = tmp_path / 'a.bmp'; b = tmp_path / 'b.bmp'
    Image.new('RGB', (20,20), 'red').save(a)
    Image.new('RGB', (20,20), 'blue').save(b)
    with decode.load_standard_image(a) as original:
        with decode.load_standard_image(b):
            assert decode.decode_cache_info()['bytes'] <= 2000
            assert decode.decode_cache_info()['entries'] == 1
        assert original.getpixel((0,0)) == (255,0,0)


def test_native_decode_concurrency_is_bounded_and_same_source_single_flight(tmp_path, fake_raw):
    fake_raw[1]['delay'] = .01
    photo = raw_asset(tmp_path)
    def run(_):
        with decode.load_analysis_image(photo) as image:
            return image.size
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(run, range(8))) == [(1200,800)] * 8
    assert fake_raw[0]['peak'] == 1 and fake_raw[0]['thumb'] == 1


def test_missing_raw_dependency_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(decode, 'rawpy', None)
    with pytest.raises(decode.CodecUnavailable, match='RAW 解码器不可用'):
        decode.load_analysis_image(raw_asset(tmp_path))


def test_resource_limit_is_explicit_error_not_quality(tmp_path, monkeypatch):
    monkeypatch.setattr(decode, 'MAX_IMAGE_PIXELS', 10)
    path = tmp_path / 'a.bmp'
    Image.new('RGB',(10,10)).save(path)
    with pytest.raises(decode.DecodeError, match='内存保护'):
        decode.load_standard_image(path)


def test_exif_orientation_icc_and_transparency(tmp_path):
    path = tmp_path / 'a.png'
    image = Image.new('RGBA',(20,30),(255,0,0,0))
    exif = Image.Exif(); exif[274] = 6
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    image.save(path, exif=exif, icc_profile=profile)
    with decode.load_standard_image(path) as rgb:
        assert rgb.size == (30,20) and rgb.mode == 'RGB'
        assert rgb.getpixel((0,0)) == (255,255,255)
        assert rgb.info['decode_evidence']['color_space'] == 'sRGB'


def test_corrupt_icc_is_disclosed(tmp_path):
    path = tmp_path / 'a.png'
    Image.new('RGB',(20,30)).save(path, icc_profile=b'bad ICC')
    with decode.load_standard_image(path) as rgb:
        assert rgb.info['decode_evidence']['color_space'] == 'unverified'
        assert any('icc_conversion_unavailable' in warning for warning in rgb.info['decode_evidence']['warnings'])


def test_16bit_tiff_preserves_tonal_range(tmp_path):
    path = tmp_path / 'a.tiff'
    Image.fromarray(np.array([[0,32768,65535]], dtype='uint16')).save(path)
    with decode.load_standard_image(path) as rgb:
        assert np.asarray(rgb).tolist() == [[ [0,0,0], [127,127,127], [255,255,255] ]]


def test_avif_real_roundtrip_if_available(tmp_path):
    if not decode.codec_capabilities()['.avif']['available']:
        pytest.skip('Pillow AVIF decoder unavailable in this test runtime')
    path = tmp_path / 'sample.avif'
    Image.new('RGB', (120,80), 'blue').save(path, 'AVIF')
    with decode.load_analysis_image(asset(path)) as image:
        assert image.size == (120,80) and image.mode == 'RGB'


def test_missing_optional_codec_fails_with_specific_message(tmp_path, monkeypatch):
    decode.register_image_codecs()
    monkeypatch.setitem(decode._codec_status, '.heic', dict(available=False, detail='HEIC decoder unavailable'))
    path = tmp_path / 'sample.heic'; path.write_bytes(b'not a fixture')
    with pytest.raises(decode.CodecUnavailable, match='HEIC decoder unavailable'):
        decode.load_standard_image(path)


def test_raw_preview_avoids_unpacking_sensor(tmp_path, fake_raw):
    calls = []
    base = type(decode.rawpy.imread('ignored'))
    class LazyRaw(base):
        def open_file(self, path):
            calls.append('header')
        def unpack(self):
            calls.append('unpack')
    decode.rawpy.RawPy = LazyRaw
    photo = raw_asset(tmp_path)
    image, _ = decode.load_display_image(photo)
    image.close()
    assert calls == ['header']
    image, _ = decode.load_display_image(photo, full_raw=True)
    image.close()
    assert calls == ['header', 'header', 'unpack']


def test_missing_thumbnail_full_fallback_reused_by_explicit_view(tmp_path, fake_raw):
    fake_raw[1]['broken'] = True
    photo = raw_asset(tmp_path)
    first, _ = decode.load_display_image(photo)
    second, kind = decode.load_display_image(photo, full_raw=True)
    with first, second:
        assert kind == 'raw_full_decode'
    assert len(fake_raw[0]['full']) == 1


def test_failed_upgrade_not_repeated_within_shared_scope(tmp_path, fake_raw, monkeypatch):
    fake_raw[1].update(size=(1200,800), fail_full=True)
    monkeypatch.setattr('ai_cull_assistant.face_focus.load_full_image', decode.load_analysis_image)
    photo = raw_asset(tmp_path)
    with shared_decode(photo):
        with full_image(photo):
            pass
        for _ in range(3):
            with full_image(photo, insufficient_evidence=True) as image:
                assert image.info['decode_evidence']['evidence_sufficient'] is False
    assert len(fake_raw[0]['full']) == 1


def test_nested_upgrade_keeps_borrowed_preview_alive(tmp_path, fake_raw, monkeypatch):
    monkeypatch.setattr('ai_cull_assistant.face_focus.load_full_image', decode.load_analysis_image)
    photo = raw_asset(tmp_path)
    with shared_decode(photo):
        with full_image(photo) as initial:
            with full_image(photo, insufficient_evidence=True) as native:
                assert native.size == (2400,1600)
                assert initial.getpixel((0,0)) is not None
            assert initial.getpixel((0,0)) is not None
        with pytest.raises(ValueError):
            initial.getpixel((0,0))


def test_real_rawpy_missing_thumbnail_exception_triggers_fallback(tmp_path, fake_raw):
    import rawpy
    original = decode.rawpy.imread
    def imread(path):
        instance = original(path)
        def absent():
            raise rawpy.LibRawNoThumbnailError('missing')
        instance.extract_thumb = absent
        return instance
    decode.rawpy.imread = imread
    image, kind = decode.load_display_image(raw_asset(tmp_path))
    with image:
        assert kind == 'raw_full_decode'
    assert len(fake_raw[0]['full']) == 1


def test_capture_time_reads_nested_exif_and_subseconds(tmp_path):
    from ai_cull_assistant.exif_utils import get_capture_time
    path = tmp_path / 'date.tiff'
    exif = Image.Exif()
    exif[34665] = {36867: '2024:01:02 03:04:05', 37521: '1234567'}
    Image.new('RGB', (16,16)).save(path, exif=exif.tobytes())
    assert get_capture_time(path) == datetime(2024,1,2,3,4,5,123456)


def test_full_cache_never_promotes_legacy_unoriented_thumbnail(tmp_path, fake_raw):
    fake_raw[1]['flip'] = 5
    photo = raw_asset(tmp_path)
    with decode.load_raw_image(photo.raw_path, apply_orientation=False) as legacy:
        assert legacy.size == (1200,800)
    with decode.load_raw_image(photo.raw_path, full_raw=True):
        pass
    with decode.load_raw_image(photo.raw_path, apply_orientation=False) as legacy:
        assert legacy.size == (1200,800)
        assert legacy.info['decode_evidence']['source_kind'] == 'raw_embedded_jpeg'
