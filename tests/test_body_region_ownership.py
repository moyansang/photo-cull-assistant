"""Ownership guards use synthetic geometry, not real-photo accuracy claims."""
import copy
import json

import numpy as np
from PIL import Image
import pytest

from ai_cull_assistant import body_focus as body


def person(head=(80, 40, 130, 100), hip=(110, 250), full=(110, 20)):
    return np.array([*head, *hip, *full, 110, 100, 110, 40, .99], dtype=np.float32)


def measured(monkeypatch, state='clear', detections=None):
    selected = person()
    if detections is None:
        detections = np.stack([selected])
    monkeypatch.setattr(body, '_region_metrics', lambda *a: {
        'state': state, 'reason': 'synthetic_detail', 'texture_sufficiency': 'sufficient'})
    return body._measured_region(np.zeros((500, 400, 3), np.uint8),
                                 {'mask': np.full((500, 400), 255, np.uint8)},
                                 {'name': 'torso', 'family': 'torso', 'box': (70, 100, 170, 220)},
                                 selected, detections)


@pytest.mark.parametrize('state', ['clear', 'severe_blur', 'uncertain'])
def test_neighbor_envelope_vetoes_all_quality_states(monkeypatch, state):
    # Neighbor head does not overlap torso, but their body envelope does.
    other = person(head=(220, 20, 260, 70), hip=(220, 210), full=(220, 40))
    region = measured(monkeypatch, state, np.stack([person(), other]))
    assert region['ownership']['status'] == 'ambiguous'
    assert region['evidence']['state'] == 'insufficient'
    assert region['evidence']['measurement_state'] == state
    assert region['visibility'] == region['occlusion'] == 'unknown'
    assert body._aggregate_regions([region])[0] == 'uncertain'
    assert body.body_review_boxes({'review_kind': 'motion_suspected', 'regions': [region]}) == []


def test_binary_mask_does_not_claim_semantic_ownership(monkeypatch):
    region = measured(monkeypatch)
    assert region['ownership']['status'] == 'geometrically_supported'
    assert region['ownership']['basis'] == 'geometric_association'
    assert region['ownership']['semantic_ownership_verified'] is False
    assert region['ownership']['mask_kind'] == 'binary_foreground'
    assert region['measurement_image_size'] == [400, 500]
    assert region['coordinate_image_size'] == [400, 500]
    assert region['measurement_box'] == region['box']
    assert region['measurement_origin_px'] == [0, 0]
    json.dumps(region)


def test_unknown_competitor_geometry_is_not_clear(monkeypatch):
    other = person(head=(220, 20, 260, 70), hip=(220, 210), full=(220, 210))
    region = measured(monkeypatch, detections=np.stack([person(), other]))
    assert region['ownership']['status'] == 'unverified'
    assert region['evidence']['state'] == 'insufficient'


def test_unsafe_region_cannot_be_removed_to_obtain_clear(monkeypatch):
    torso = measured(monkeypatch)
    arm = copy.deepcopy(torso)
    arm['family'] = 'left_arm'
    unsafe = copy.deepcopy(torso)
    unsafe['family'] = 'right_arm'
    unsafe['ownership']['status'] = 'ambiguous'
    # Even a malformed caller retaining raw clear detail cannot bypass ownership.
    assert body._aggregate_regions([torso, arm])[0] == 'clear'
    assert body._aggregate_regions([torso, arm, unsafe])[0] == 'uncertain'
    unsafe.pop('ownership')
    assert body._aggregate_regions([torso, arm, unsafe])[0] == 'uncertain'


def test_reliable_multiple_family_severe_still_rejects(monkeypatch):
    torso = measured(monkeypatch, 'severe_blur')
    arm = copy.deepcopy(torso)
    arm['family'] = 'left_arm'
    unsafe = copy.deepcopy(torso)
    unsafe['ownership']['status'] = 'ambiguous'
    assert body._aggregate_regions([torso])[0] == 'uncertain'
    assert body._aggregate_regions([torso, arm, unsafe])[0] == 'severe_blur'
    arm['ownership']['status'] = 'ambiguous'
    assert body._aggregate_regions([torso, arm])[0] == 'uncertain'


def test_low_texture_remains_insufficient():
    image = np.full((180, 180, 3), 128, np.uint8)
    evidence = body._region_metrics(image, np.full((180, 180), 255, np.uint8), (0, 0, 180, 180))
    assert evidence['state'] == 'insufficient'
    assert evidence['texture_sufficiency'] == 'insufficient'


@pytest.mark.parametrize('neighbor', [False, True])
def test_manual_crop_provenance_and_full_image_competition(monkeypatch, neighbor):
    region = measured(monkeypatch)
    # Actual measurement was in a 400x500 crop, shown in an 800x1000 image.
    selected = person(head=(180, 240, 230, 300), hip=(210, 450), full=(210, 220))
    others = [selected]
    if neighbor:
        others.append(person(head=(450, 200, 500, 260), hip=(380, 400), full=(380, 100)))
    class Detector:
        def infer(self, _image):
            assert _image.shape[:2] == (1000, 800)
            return np.stack(others)
    monkeypatch.setattr(body, '_get_models', lambda: (Detector(), None))
    candidate = {'box': (0, 0, 1, 1), 'head_box': (.2, .08, .125, .12),
                 'state': 'clear', 'reasons': [], 'confidence': .99, 'regions': [region]}
    monkeypatch.setattr(body, 'detect_body_candidates', lambda crop: [candidate])
    result = body.assess_body_region(Image.new('RGB', (800, 1000)), (.125, .2, .5, .5))
    actual = result['regions'][0]
    assert actual['measurement_image_size'] == [400, 500]
    assert actual['coordinate_image_size'] == [800, 1000]
    assert actual['measurement_origin_px'] == [100, 200]
    assert actual['measurement_box'] == [70, 100, 170, 220]
    assert actual['box'] == [170, 300, 270, 420]
    assert actual['full_image_ownership']['status'] == ('ambiguous' if neighbor else 'geometrically_supported')
    if neighbor:
        assert actual['evidence']['state'] == 'insufficient'
    json.dumps(result)


def test_full_assessment_cannot_use_neighbor_pixels(monkeypatch):
    selected = person()
    other = person(head=(220, 20, 260, 70), hip=(220, 210), full=(220, 40))
    class Detector:
        def infer(self, _image):
            return np.stack([selected, other])
    class Pose:
        def infer(self, _image, _person):
            return {'landmarks': np.zeros((33, 5)), 'mask': np.ones((500, 400), np.uint8), 'confidence': .99}
    monkeypatch.setattr(body, '_get_models', lambda: (Detector(), Pose()))
    monkeypatch.setattr(body, '_pose_regions', lambda *a: [
        {'name': 'torso', 'family': 'torso', 'box': (70, 100, 170, 220)},
        {'name': 'left_upper_arm', 'family': 'left_arm', 'box': (70, 100, 170, 220)}])
    monkeypatch.setattr(body, '_region_metrics', lambda *a: {
        'state': 'severe_blur', 'reason': 'synthetic_detail', 'texture_sufficiency': 'sufficient'})
    result = body.assess_body_focus(Image.new('RGB', (400, 500)), (.2, .08, .125, .12))
    assert result['version'] == body.VERSION
    assert result['state'] == 'uncertain'
    assert result['review_kind'] == 'unsupported'
    assert all(r['evidence']['state'] == 'insufficient' for r in result['regions'])


def test_measurement_source_size_remains_actual_analysis_size(monkeypatch):
    # A fallback image is not relabeled as the unavailable 4000x6000 original.
    selected = person()
    monkeypatch.setattr(body, '_region_metrics', lambda *a: {
        'state': 'uncertain', 'reason': 'synthetic', 'texture_sufficiency': 'sufficient'})
    region = body._measured_region(np.zeros((1600, 1067, 3), np.uint8),
                                   {'mask': np.ones((1600, 1067), np.uint8)},
                                   {'name': 'torso', 'family': 'torso', 'box': (200, 300, 400, 600)},
                                   selected, np.stack([selected]))
    assert region['measurement_image_size'] == region['coordinate_image_size'] == [1067, 1600]
    assert region['coordinate_space'] == 'exif_transposed_image_pixels_xyxy'
    assert region['box'] == region['measurement_box'] == [200, 300, 400, 600]


@pytest.mark.parametrize('ownership', [True, 'verified', ['geometrically_supported'], {'status': 'verified'}, None])
def test_malformed_or_unsupported_ownership_fails_closed(monkeypatch, ownership):
    region = measured(monkeypatch)
    region['ownership'] = ownership
    assert body._region_is_usable(region) is False
    state, reasons = body._aggregate_regions([region])
    assert state == 'uncertain'
    assert 'body_region_ownership_unresolved' in reasons


def test_spatially_separate_neighbor_does_not_block_supported_region(monkeypatch):
    other = person(head=(300, 40, 340, 90), hip=(320, 180), full=(320, 110))
    torso = measured(monkeypatch, detections=np.stack([person(), other]))
    arm = copy.deepcopy(torso)
    arm['name'], arm['family'] = 'left_upper_arm', 'left_arm'
    assert torso['ownership']['status'] == 'geometrically_supported'
    assert len(torso['ownership']['competing_envelopes']) == 1
    assert body._aggregate_regions([torso, arm])[0] == 'clear'
