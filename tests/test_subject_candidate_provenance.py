"""Synthetic provenance contracts; these tests do not establish photo accuracy."""
from dataclasses import asdict, replace
import json

from PIL import Image
import pytest

from ai_cull_assistant import subject
from ai_cull_assistant.head_detection import HeadDetection
from ai_cull_assistant.yunet import FaceDetection


def _face(box=(50, 20, 80, 60), score=.95):
    return FaceDetection(box, ((70, 42), (110, 42), (90, 56), (75, 68), (105, 68)), score)


def _features(tmp_path, monkeypatch, candidates, size=(200, 100)):
    path = tmp_path / 'synthetic.png'
    Image.new('RGB', size, 'gray').save(path)
    monkeypatch.setattr(subject, 'detect', lambda *args: candidates)
    subject._cached_features.cache_clear()
    return subject.features(path, .8)


def test_all_candidates_recorded_without_changing_existing_selection(tmp_path, monkeypatch):
    first, second = _face(), _face((5, 5, 15, 15), .82)
    found = _features(tmp_path, monkeypatch, [first, second])
    assert found.face == (.25, .2, .4, .6)
    assert found.candidate_count == 2
    assert found.candidate_confidence == .8
    provenance = found.candidate_provenance
    assert provenance['diagnostic_version'] == subject.CANDIDATE_PROVENANCE_VERSION
    assert provenance['coordinate_space'] == 'normalized_exif_oriented'
    assert provenance['initial_candidates'] == (
        {'box': (.25, .2, .4, .6), 'score': .95, 'landmarks_available': True, 'source': 'initial_yunet'},
        {'box': (.025, .05, .075, .15), 'score': .82, 'landmarks_available': True, 'source': 'initial_yunet'},
    )
    assert provenance['chosen_initial_index'] == 0
    assert provenance['chosen_source'] == 'initial_yunet'
    assert provenance['chosen_candidate'] == provenance['initial_candidates'][0]
    image = Image.new('RGB', (200, 100), 'gray')
    assert found.whole == subject.image_hash(image)
    assert found.center == subject.image_hash(image.crop((40, 10, 160, 95)))
    assert found.body == subject.image_hash(image.crop((0, 2, 200, 100)))
    assert found.head == subject.head_box(first, 200, 100)
    assert found.landmarks == ((.35, .42), (.55, .42), (.45, .56), (.375, .68), (.525, .68))


@pytest.mark.parametrize('initial_count', [0, 1])
def test_head_retry_cannot_inherit_initial_single_candidate_proof(tmp_path, monkeypatch, initial_count):
    original = _face((10, 10, 19, 20), .82)
    rescued = _face((160, 110, 100, 120), .96)
    head = HeadDetection((150, 100, 150, 180), .96, 'body_anchor_head')
    monkeypatch.setattr(subject, '_head_candidates', lambda image: [head])
    monkeypatch.setattr(subject, '_refine_head_face', lambda *args: rescued)
    found = _features(tmp_path, monkeypatch, [original] * initial_count, size=(400, 500))
    assert found.face == (.4, .22, .25, .24)
    assert found.candidate_count == initial_count
    provenance = found.candidate_provenance
    assert len(provenance['initial_candidates']) == initial_count
    assert provenance['chosen_initial_index'] is None
    assert provenance['chosen_source'] == 'head_face_retry'
    assert provenance['chosen_candidate']['box'] == found.face
    assert provenance['chosen_candidate']['score'] == .96


def test_identical_rescue_geometry_is_not_initial_detection_identity():
    initial, rescue = _face(), _face()
    assert initial == rescue and initial is not rescue
    provenance = subject._candidate_provenance([initial], rescue, 200, 100)
    assert provenance['chosen_initial_index'] is None
    assert provenance['chosen_source'] == 'head_face_retry'


def test_no_detection_records_observed_empty_list_without_fabricated_face(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, '_head_candidates', lambda image: [])
    found = _features(tmp_path, monkeypatch, [])
    assert found.face is None
    assert found.candidate_provenance['initial_candidates'] == ()
    assert found.candidate_provenance['chosen_candidate'] is None
    assert found.candidate_provenance['chosen_source'] is None


def test_ambiguous_initial_candidates_preserve_existing_no_choice(tmp_path, monkeypatch):
    first, second = _face(), _face()
    monkeypatch.setattr(subject, '_head_candidates', lambda image: [])
    found = _features(tmp_path, monkeypatch, [first, second])
    assert found.face is None
    assert len(found.candidate_provenance['initial_candidates']) == 2
    assert found.candidate_provenance['chosen_candidate'] is None


@pytest.mark.parametrize('landmarks', [(), ((1, 2),), ((float('nan'), 2),) * 5])
def test_unavailable_landmarks_are_not_claimed(landmarks):
    candidate = FaceDetection((10, 10, 20, 20), landmarks, .9)
    record = subject._candidate_record(candidate, 100, 100, 'initial_yunet')
    assert record['landmarks_available'] is False
    assert not any(key in record for key in ('visible', 'occluded', 'background', 'body_association'))


def test_legacy_default_and_json_round_trip_preserve_grouping_equality(tmp_path, monkeypatch):
    legacy = subject.SubjectFeatures('whole', 'center', None, None)
    assert legacy.candidate_provenance is None
    found = _features(tmp_path, monkeypatch, [_face()])
    raw = json.loads(json.dumps(asdict(found)))
    restored = subject.SubjectFeatures(**raw)
    assert restored.candidate_provenance == found.candidate_provenance
    raw['candidate_provenance']['initial_candidates'][0]['score'] = 0
    assert restored.candidate_provenance['initial_candidates'][0]['score'] == .95
    # Metadata must not redefine descriptor equality or hashing used downstream.
    stripped = replace(found, candidate_provenance=None)
    assert found == stripped
    assert hash(found) == hash(stripped)
