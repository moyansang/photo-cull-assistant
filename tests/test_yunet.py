import hashlib

import numpy as np
import pytest

from ai_cull_assistant import yunet


def row():
    return np.array([30, 20, 40, 50, 40, 38, 58, 38, 49, 49, 42, 59, 56, 59, .95], dtype=np.float32)


def test_bundled_official_model_and_real_inference():
    assert hashlib.sha256(yunet.MODEL_PATH.read_bytes()).hexdigest() == '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'
    assert yunet.detect(np.zeros((320, 320, 3), np.uint8)) == []


@pytest.mark.parametrize('change', ['confidence', 'nan', 'tiny', 'outside', 'collapsed', 'nose'])
def test_unreliable_candidates_rejected(change):
    candidate = row()
    if change == 'confidence': candidate[14] = .89
    if change == 'nan': candidate[4] = np.nan
    if change == 'tiny': candidate[2] = 4
    if change == 'outside': candidate[4] = 95
    if change == 'collapsed': candidate[4:14] = 45
    if change == 'nose': candidate[9] = 70
    assert not yunet.valid_detection(candidate, 100, 100)


def test_valid_landmarks_and_head_crop_leave_hair_space():
    candidate = row()
    assert yunet.valid_detection(candidate, 100, 100)
    face = yunet.FaceDetection(tuple(candidate[:4]), tuple(map(tuple, candidate[4:14].reshape(5, 2))), .95)
    x, y, w, h = yunet.head_box(face, 100, 100)
    assert 0 <= x < x+w <= 1
    assert 0 <= y < y+h <= 1
    assert y * 100 < candidate[1]
    for px, py in face.landmarks:
        assert x <= px / 100 <= x+w
        assert y <= py / 100 <= y+h


def test_lower_confidence_keeps_geometry_checks():
    candidate = row()
    candidate[14] = .83
    assert not yunet.valid_detection(candidate, 100, 100, .9)
    assert yunet.valid_detection(candidate, 100, 100, .8)
    candidate[4:14] = 45
    assert not yunet.valid_detection(candidate, 100, 100, .8)


def test_scaled_coordinates_map_back_to_original(monkeypatch):
    class Model:
        def setScoreThreshold(self, score): assert score == .9
        def setInputSize(self, size): assert size == (1600, 800)
        def detect(self, image): return 1, np.array([row()])
    monkeypatch.setattr(yunet, 'detector', lambda: Model())
    found = yunet.detect(np.zeros((1600, 3200, 3), np.uint8))[0]
    assert found.box == (60, 40, 80, 100)
    assert found.landmarks[0] == (80, 76)
