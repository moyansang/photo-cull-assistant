"""Opt-in real native codec fixtures; no camera-coverage claim from mocks.

Set PHOTO_CULL_CODEC_FIXTURES to a directory containing the named upstream
samples. Sources, hashes, licenses and remaining camera gaps are documented in
docs/research-decode.md. No network download happens as part of pytest.
"""
from datetime import datetime
import os
from pathlib import Path

import pytest
from PIL import Image

from ai_cull_assistant.image_decode import (DecodeError, codec_capabilities,
    load_standard_image, load_analysis_image, load_display_image)
from ai_cull_assistant.models import PhotoAsset


def fixture(name):
    folder = os.environ.get('PHOTO_CULL_CODEC_FIXTURES')
    if not folder:
        pytest.skip('Real codec fixtures not configured (PHOTO_CULL_CODEC_FIXTURES)')
    path = Path(folder) / name
    if not path.is_file():
        pytest.skip(f'Real codec fixture unavailable: {name}')
    return path


@pytest.mark.parametrize('name', [
    'RGB_8__128x128.heif', 'RGB_10__128x128.heif', 'RGB_12__128x128.heif',
    'RGBA_8__128x128.heif', 'RGBA_12__128x128.heif', 'L_12__128x128.heif',
])
def test_real_heif_bit_depth_and_alpha(name):
    path = fixture(name)
    if not codec_capabilities()['.heif']['available']:
        pytest.skip('HEIF native codec not installed')
    with load_standard_image(path) as image:
        assert image.size == (128,128) and image.mode == 'RGB'
        assert image.info['decode_evidence']['full_resolution']


def test_real_heic_portrait_orientation():
    path = fixture('arrow.heic')
    if not codec_capabilities()['.heic']['available']:
        pytest.skip('HEIC native codec not installed')
    with load_standard_image(path) as image:
        assert image.size == (3024,4032)
        assert image.getexif().get(274) in (None, 1)


@pytest.mark.parametrize('name', ['corrupted.heic', 'empty.heic'])
def test_real_corrupt_heic_is_decode_error(name):
    path = fixture(name)
    if not codec_capabilities()['.heic']['available']:
        pytest.skip('HEIC native codec not installed')
    with pytest.raises(DecodeError):
        load_standard_image(path)


@pytest.mark.parametrize('name', ['RAW_CANON_40D_SRAW_V103.CR2', 'iss030e122639.NEF'])
def test_real_raw_embedded_preview_and_full_decode(name):
    path = fixture(name)
    if not codec_capabilities()[path.suffix.lower()]['available']:
        pytest.skip('LibRaw native codec not installed')
    photo = PhotoAsset(path.stem, path, path, path, None, datetime(2026,10,13), path.suffix.lower())
    image, source = load_display_image(photo)
    with image:
        assert source in ('raw_embedded_jpeg', 'raw_embedded_bitmap', 'raw_full_decode')
        preview_size = image.size
        assert image.mode == 'RGB'
    image, source = load_display_image(photo, full_raw=True)
    with image:
        assert source == 'raw_full_decode'
        assert image.width >= min(preview_size) and image.height >= min(preview_size)
        assert image.info['decode_evidence']['full_resolution']
