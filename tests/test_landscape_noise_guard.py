"""Development regressions for a conservative, uncalibrated noise-support gate."""
from contextlib import contextmanager
from types import SimpleNamespace
import json

import cv2
import numpy as np
from PIL import Image, ImageFilter
import pytest

from ai_cull_assistant import analysis_modes as modes, body_focus


def _noisy(pixels, sigma, seed, *, grayscale=False):
    shape = (*pixels.shape[:2], 1) if grayscale else pixels.shape
    noise = np.random.default_rng(seed).normal(0, sigma, shape)
    return np.clip(pixels.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def _sparse_checker(cell=4, low=0, high=255):
    yy, xx = np.indices((512, 512))
    pixels = np.full((512, 512, 3), 128, np.uint8)
    board = (low + (xx // cell + yy // cell) % 2 * (high - low)).astype(np.uint8)
    pixels[:128, :256] = board[:128, :256, None]
    return pixels


def test_noise_gain_matches_production_fine_detail_operator():
    # Exercise the actual production filter, not a second hand-copied pipeline.
    # The constant background has zero contrast; recover the pre-normalized
    # impulse energy using the production denominator and impulse amplitude.
    side = 64
    bgr = np.full((side, side, 3), 128, np.uint8)
    bgr[side // 2, side // 2] = 255
    metrics = body_focus._region_metrics(bgr, np.full((side, side), 255, np.uint8),
                                         (0, 0, side, side))
    amplitude = (255 - 128) / 255.0
    gain = (metrics['laplacian_normalized'] * max(metrics['contrast'] ** 2, 0.0025)
            * side ** 2 / amplitude ** 2)
    assert modes._LANDSCAPE_FINE_NOISE_GAIN == pytest.approx(gain, rel=1e-6)


@pytest.mark.parametrize('seed', [123, 456, 789])
@pytest.mark.parametrize('sigma', [16, 32, 64])
@pytest.mark.parametrize('grayscale', [False, True])
def test_random_noise_cannot_supply_clear_support(seed, sigma, grayscale):
    pixels = _noisy(np.full((512, 512, 3), 128, np.uint8), sigma, seed, grayscale=grayscale)
    evidence = modes.landscape_evidence(Image.fromarray(pixels))
    assert evidence['clear_regions'] >= 2  # The original rule would keep this.
    assert evidence['supported_clear_regions'] == 0
    assert evidence['recommendation'] == 'review'
    assert evidence['state'] == 'uncertain'
    assert evidence['reasons'] == ['landscape_detail_may_be_noise_dominated']
    assert not evidence['visible_boxes'] and not evidence['subjects']
    json.dumps(evidence, allow_nan=False)


@pytest.mark.parametrize('seed', [123, 456, 789])
@pytest.mark.parametrize('sigma', [32, 48, 64])
def test_noise_dominated_blurred_structure_returns_to_review(seed, sigma):
    yy, xx = np.indices((512, 512))
    board = ((xx // 16 + yy // 16) % 2 * 255).astype(np.uint8)
    sharp = Image.fromarray(np.repeat(board[:, :, None], 3, axis=2))
    blurred = sharp.filter(ImageFilter.GaussianBlur(3))
    assert modes.landscape_evidence(blurred)['recommendation'] == 'review'
    result = modes.landscape_evidence(Image.fromarray(_noisy(np.asarray(blurred), sigma, seed)))
    assert result['clear_regions'] >= 2
    assert result['supported_clear_regions'] < 2
    assert result['recommendation'] == 'review'


@pytest.mark.parametrize('cell', [3, 4, 8, 16])
@pytest.mark.parametrize('seed', [123, 456])
@pytest.mark.parametrize('sigma', [0, 16])
def test_two_sparse_sharp_tiles_survive(cell, seed, sigma):
    pixels = _noisy(_sparse_checker(cell), sigma, seed)
    evidence = modes.landscape_evidence(Image.fromarray(pixels))
    assert evidence['recommendation'] == 'keep'
    assert evidence['supported_clear_regions'] == 2
    assert evidence['version'] == modes.LANDSCAPE_VERSION
    assert 'rating' not in evidence and 'stars' not in evidence


@pytest.mark.parametrize('cell', [3, 4, 8, 16])
def test_sparse_low_contrast_detail_is_not_penalized_for_small_coverage(cell):
    image = Image.fromarray(_sparse_checker(cell, 108, 148))
    evidence = modes.landscape_evidence(image)
    assert evidence['recommendation'] == 'keep'
    assert evidence['supported_clear_regions'] == 2


@pytest.mark.parametrize('size', [(1, 1), (31, 31), (127, 200), (128, 128), (512, 512)])
def test_small_and_low_texture_images_remain_review_without_support_checks(size, monkeypatch):
    monkeypatch.setattr(modes, '_landscape_detail_support', lambda *args: pytest.fail('non-clear tiles cannot be promoted'))
    result = modes.landscape_evidence(Image.new('RGB', size, (120, 150, 200)))
    assert result['recommendation'] == 'review'
    assert result['reliability'] == 'insufficient'
    assert result['clear_regions'] == result['supported_clear_regions'] == 0
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('field,value', [('contrast', float('nan')), ('laplacian_normalized', float('nan')),
                                        ('laplacian_normalized', float('inf')), ('laplacian_normalized', 0.0)])
def test_nonfinite_or_missing_power_does_not_support_keep(field, value):
    measured = {'contrast': 0.5, 'laplacian_normalized': 0.01}
    measured[field] = value
    result = modes._landscape_detail_support(np.full((32, 32, 3), 128, np.uint8), measured)
    assert result == {'state': 'uncertain', 'reason': 'noise_support_unavailable'}
    json.dumps(result, allow_nan=False)


def test_support_checks_stay_at_native_bounded_tile_size(monkeypatch):
    yy, xx = np.indices((1700, 2000))
    board = ((xx // 4 + yy // 4) % 2 * 255).astype(np.uint8)
    image = Image.fromarray(np.repeat(board[:, :, None], 3, axis=2))
    before = image.tobytes()
    observed = []
    original = modes._landscape_detail_support

    def check(bgr, measured):
        observed.append(bgr.shape[:2])
        return original(bgr, measured)

    monkeypatch.setattr(modes, '_landscape_detail_support', check)
    result = modes.landscape_evidence(image)
    assert observed == [(384, 384)] * 16
    assert result['recommendation'] == 'keep'
    assert image.tobytes() == before
    assert result['source_size'] == [2000, 1700]


def test_landscape_version_is_separate_and_does_not_change_portrait_version(monkeypatch):
    @contextmanager
    def image_for_asset(_asset):
        yield Image.new('RGB', (128, 128), 'gray')

    monkeypatch.setattr('ai_cull_assistant.shared_decode.full_image', image_for_asset)
    result = modes.assess_asset_mode(SimpleNamespace(person_review_pending=True), mode='landscape')
    assert result.analysis_version == result.focus_evidence['version'] == modes.LANDSCAPE_VERSION
    assert modes.VERSION == 'manual-modes-v1'
    assert modes.LANDSCAPE_VERSION != modes.VERSION
    assert not result.rejected


@pytest.mark.parametrize('shape', [(0, 0, 3), (1, 1, 3), (4, 8, 3), (31, 40, 3), (32, 32)])
def test_helper_small_or_invalid_arrays_fail_closed_without_warnings(shape):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        result = modes._landscape_detail_support(np.zeros(shape, np.uint8),
                                                {'contrast': 0.5, 'laplacian_normalized': 0.01})
    assert result == {'state': 'uncertain', 'reason': 'noise_support_unavailable'}
