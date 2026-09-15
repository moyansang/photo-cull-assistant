from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock, patch

from PIL import Image
import pytest

import hybrid
from ai_cull_assistant.screening import ScreeningResult
from ai_cull_assistant.shared_decode import shared_decode, full_image


def clear():
    region = dict(fine={'laplacian_normalized': .03, 'edge_curvature': .35,
                       'gradient_p90_normalized': .6, 'contrast': .4,
                       'orientation_coherence': .1},
                  coarse={'laplacian_normalized': .005},
                  native_fine={'edge_curvature': .35}, fine_to_coarse_ratio=4.)
    return ScreeningResult(False, 'subject_not_obviously_blurred', True,
        focus_evidence={'state': 'clear', 'landmarks_used': True,
                        'native_eye_distance': 150.,
                        'regions': {'eye_band': deepcopy(region), 'face_core': deepcopy(region)}})


def test_strong_clear_does_not_decode_cpu():
    gpu = clear(); cpu = Mock(side_effect=AssertionError('unexpected CPU'))
    assert hybrid.choose_result(gpu, hybrid.review_reasons(gpu), cpu).reason == gpu.reason
    cpu.assert_not_called()


@pytest.mark.parametrize('value', [.0059, .0064, .0072])
def test_core_near_gate_requires_cpu(value):
    gpu = clear(); gpu.focus_evidence['regions']['face_core']['fine']['laplacian_normalized'] = value
    assert 'core_laplacian' in hybrid.review_reasons(gpu)


@pytest.mark.parametrize('state,rejected,reason', [
    ('uncertain', False, 'face_focus_uncertain'),
    ('severe_blur', True, 'obvious_subject_blur'),
    ('clear', False, 'subject_not_obviously_blurred')])
def test_cpu_result_is_preserved(state, rejected, reason):
    cpu = ScreeningResult(rejected, reason, True, focus_evidence={'state': state})
    result = hybrid.choose_result(clear(), ['core_laplacian'], lambda: cpu)
    assert (result.reason, result.rejected) == (reason, rejected)
    assert result.focus_evidence['state'] == state


@pytest.mark.parametrize('cpu', [Mock(side_effect=OSError('decode failed')),
    lambda: ScreeningResult(False, 'source_unreadable_for_focus', True)])
def test_cpu_failure_never_falls_back_to_gpu_clear(cpu):
    result = hybrid.choose_result(clear(), ['core_laplacian'], cpu)
    assert result.reason == 'face_focus_uncertain'
    assert not result.rejected
    assert 'cpu_error' in result.focus_evidence['hybrid']


def test_missing_nan_and_version_evidence_fail_closed():
    gpu=clear();gpu.focus_evidence['regions']['eye_band']['fine']['contrast']=float('nan')
    assert hybrid.review_reasons(gpu)==['invalid_evidence']
    assert hybrid.review_reasons(replace(gpu,focus_evidence={'state':'clear'}))
    with patch.object(hybrid,'VERSION','future-version'):
        assert hybrid.review_reasons(clear())==['focus_version_changed']


def test_cpu_nested_scope_does_not_reuse_gpu_image_or_cache():
    asset=object();calls=[]
    def assess(a, **kwargs):
        assert kwargs['cache_dir'] is None
        with full_image(a) as im:
            calls.append(im.getpixel((0,0)))
        result=clear()
        if calls[-1] == (0,255,0):
            result.focus_evidence['regions']['face_core']['fine']['laplacian_normalized']=.0064
        else:
            result.reason='face_focus_uncertain';result.focus_evidence={'state':'uncertain'}
        return result
    with patch.object(hybrid,'assess_asset_focus',side_effect=assess), \
         patch.object(hybrid,'load_full_image',side_effect=lambda _:Image.new('RGB',(2,2),'red')), \
         patch('ai_cull_assistant.face_focus.load_full_image',side_effect=lambda _:Image.new('RGB',(2,2),'lime')):
        with shared_decode(asset):
            result=hybrid.assess_hybrid(asset,cache_dir='must-not-use')
            with full_image(asset) as im:
                assert im.getpixel((0,0)) == (0,255,0)
    assert calls == [(0,255,0),(255,0,0)]
    assert result.reason=='face_focus_uncertain'
