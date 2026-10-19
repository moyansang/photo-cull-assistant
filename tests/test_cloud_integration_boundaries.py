"""Acceptance boundaries: retain local sampling and never use AF to pick people.

Golden measurements were computed on synthetic stripe/mask inputs by the
pre-integration 8a70f58e body metric. They are not natural-photo accuracy labels.
"""
import inspect
import sys
from types import SimpleNamespace
import numpy as np
import pytest
from ai_cull_assistant import body_focus, face_focus, af_metadata, primary_quality
from test_automatic_primary_quality import automatic, detector_evidence, scan

BASELINE = [{'expected': {'coarse_laplacian_normalized': 4.915029785479419e-05,
               'contrast': 0.9215686284005642,
               'edge_anisotropy': 1744049.6012568474,
               'edge_curvature': 0.021611617878079414,
               'laplacian_normalized': 0.0001778170990291983,
               'reason': 'weak_detail_at_both_scales',
               'state': 'severe_blur',
               'texture_fraction': 0.30327868852459017},
  'periods': 2},
 {'expected': {'coarse_laplacian_normalized': 0.00033392454497516155,
               'contrast': 0.9254901967942715,
               'edge_anisotropy': 6414298.713207245,
               'edge_curvature': 0.019078869372606277,
               'laplacian_normalized': 0.0007250244379974902,
               'reason': 'weak_detail_at_both_scales',
               'state': 'severe_blur',
               'texture_fraction': 0.3073770491803279},
  'periods': 4},
 {'expected': {'coarse_laplacian_normalized': 0.0031714565120637417,
               'contrast': 0.9254901967942715,
               'edge_anisotropy': 25839093.327522278,
               'edge_curvature': 0.02451935224235058,
               'laplacian_normalized': 0.005004939157515764,
               'reason': 'borderline_body_detail',
               'state': 'uncertain',
               'texture_fraction': 0.3237704918032787},
  'periods': 8},
 {'expected': {'coarse_laplacian_normalized': 0.03110279142856598,
               'contrast': 0.9411764722317457,
               'edge_anisotropy': 95151942.96836853,
               'edge_curvature': 0.04861019179224968,
               'laplacian_normalized': 0.048908162862062454,
               'reason': 'borderline_body_detail',
               'state': 'uncertain',
               'texture_fraction': 0.30327868852459017},
  'periods': 16},
 {'expected': {'coarse_laplacian_normalized': 0.13913889229297638,
               'contrast': 0.9411764722317457,
               'edge_anisotropy': 270413804.05426025,
               'edge_curvature': 0.17516833543777466,
               'laplacian_normalized': 0.5328466892242432,
               'reason': 'strong_consistent_detail',
               'state': 'clear',
               'texture_fraction': 0.5},
  'periods': 32}]

@pytest.mark.parametrize('case', BASELINE)
def test_retained_body_sampling_matches_local_baseline(case):
    x = np.arange(256, dtype=np.float64)
    plane = np.tile((127.5 + 120 * np.sin(x * 2 * np.pi * case['periods'] / 256)).astype(np.uint8), (256, 1))
    image = np.repeat(plane[:, :, None], 3, axis=2)
    mask = np.full((256, 256), 255, np.uint8)
    mask[:20] = 0
    mask[:, 160:166] = 0
    actual = body_focus._region_metrics(image, mask, (0, 0, 256, 256))
    for key, expected in case['expected'].items():
        assert actual[key] == (pytest.approx(expected, rel=2e-5, abs=1e-8) if isinstance(expected, float) else expected)

@pytest.mark.parametrize('count, recommendation', [(1, 'keep'), (2, 'review'), (None, 'review')])
def test_camera_metadata_never_selects_a_person_during_local_scan(automatic, monkeypatch, count, recommendation):
    asset, settings = automatic
    def forbidden(*args, **kwargs):
        pytest.fail('deferred AF selection or metadata read reached local quality scan')
    monkeypatch.setattr(af_metadata, 'read_af_metadata', forbidden)
    monkeypatch.setitem(sys.modules, 'ai_cull_assistant.af_subject', SimpleNamespace(resolve=forbidden, measure=forbidden))
    evidence = detector_evidence(settings, candidate_count=count)
    # Even a serialized cloud automatic-AF claim is not authority on this branch.
    evidence['af_subject_selection'] = {'status': 'selected', 'reliable': True, 'human_confirmed': False}
    result = scan(monkeypatch, asset, settings, evidence)
    assert result.focus_evidence['recommendation'] == recommendation
    selection = primary_quality.current_selection(asset, settings)
    if count == 1:
        assert selection['origin'] == 'automatic' and selection['human_confirmed'] is False
        assert selection['version'] == 'automatic-primary-selection-v1'
    else:
        assert selection is None
    assert settings.photos == {}

def test_deferred_sampling_entrypoints_are_absent():
    assert body_focus.VERSION == 'body-focus-v2'
    assert 'experimental_source_sampling' not in inspect.signature(face_focus.assess_asset_focus).parameters
    assert 'experimental_full_source' not in inspect.signature(face_focus.assess_asset_focus).parameters
