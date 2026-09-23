import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from ai_cull_assistant import group_face_assist, person_match
from ai_cull_assistant.group_face_assist import AssistImage, AssistTarget


REFERENCE_BOX = (.40, .08, .20, .18)


def _appearance_scene() -> np.ndarray:
    image = np.full((320, 320, 3), 110, dtype=np.uint8)
    # Manually selected head plus a strongly coloured shirt below it.
    image[26:84, 128:192] = (185, 135, 105)
    image[84:290, 82:238] = (25, 75, 215)
    # Fine stripes supply texture without pretending to simulate real photos.
    image[100:285:10, 82:238] = (40, 100, 235)
    return image


def test_apparel_colour_ranks_matching_detector_candidate_above_distractor(monkeypatch):
    reference_image = _appearance_scene()
    reference = person_match.build_reference(reference_image, REFERENCE_BOX)
    target = np.full((320, 640, 3), 110, dtype=np.uint8)
    # Both detected heads have the same coarse skin colour.  Clothing differs.
    target[26:84, 96:160] = (185, 135, 105)
    target[26:84, 480:544] = (185, 135, 105)
    target[84:290, 50:206] = (25, 75, 215)
    target[100:285:10, 50:206] = (40, 100, 235)
    target[84:290, 434:590] = (215, 55, 35)

    matching = person_match._Anchor((.15, .08, .10, .18), 'face', .93)
    distractor = person_match._Anchor((.75, .08, .10, .18), 'face', .96)
    monkeypatch.setattr(person_match, '_detector_anchors', lambda *_args: [distractor, matching])
    monkeypatch.setattr(person_match, '_template_anchors', lambda *_args: [])

    ranked = person_match.rank_candidates(target, reference, threading.Event())

    assert len(ranked) == 2
    assert ranked[0].box == matching.box
    assert ranked[0].appearance_score > ranked[1].appearance_score
    # This checks the scoring mechanism only; it is not a real-photo accuracy claim.


def test_processing_is_bounded_and_normalized_geometry_survives_resize(monkeypatch):
    reference = person_match.build_reference(_appearance_scene(), REFERENCE_BOX)
    large = np.full((1800, 3000, 3), 100, dtype=np.uint8)
    observed = []

    def detector_anchors(image, _confidence):
        observed.append(image.shape[:2])
        return [person_match._Anchor((.625, .20, .12, .18), 'head', .9)]

    monkeypatch.setattr(person_match, '_detector_anchors', detector_anchors)
    monkeypatch.setattr(person_match, '_template_anchors', lambda *_args: [])
    monkeypatch.setattr(person_match, '_appearance_similarity', lambda *_args: .8)

    ranked = person_match.rank_candidates(large, reference, threading.Event())

    assert max(observed[0]) <= person_match.MAX_WORKING_EDGE
    assert observed[0][0] * observed[0][1] <= person_match.MAX_WORKING_PIXELS
    assert ranked[0].box == pytest.approx((.625, .20, .12, .18))


def test_person_assist_continues_after_missing_preview_and_never_marks_reliable(monkeypatch, tmp_path):
    reference_path = tmp_path / 'reference.png'
    target_path = tmp_path / 'target.png'
    Image.fromarray(_appearance_scene()).save(reference_path)
    Image.fromarray(_appearance_scene()).save(target_path)
    reference = AssistImage('reference', 'reference', str(reference_path))
    targets = [
        AssistTarget('missing', 'missing', str(tmp_path / 'gone.png'), cross_group=True),
        AssistTarget('target', 'target', str(target_path), cross_group=True),
    ]
    candidate = person_match.PersonCandidate((.2, .1, .15, .2), .81, .94, 'face', .8)
    monkeypatch.setattr(person_match, 'rank_candidates', lambda *_args, **_kwargs: [candidate])
    progress = []

    proposals = group_face_assist.propose_person_faces(
        reference,
        REFERENCE_BOX,
        targets,
        threading.Event(),
        on_progress=lambda done, total, proposal: progress.append((done, total, proposal.target_key)),
    )

    assert [proposal.level for proposal in proposals] == ['missing', 'review']
    assert proposals[1].box == candidate.box
    assert '不代表身份识别' in proposals[1].reason
    assert progress == [(1, 2, 'missing'), (2, 2, 'target')]


def test_person_assist_honours_cancellation_during_target(monkeypatch, tmp_path):
    reference_path = tmp_path / 'reference.png'
    target_path = tmp_path / 'target.png'
    Image.fromarray(_appearance_scene()).save(reference_path)
    Image.fromarray(_appearance_scene()).save(target_path)
    stop = threading.Event()
    progress = []

    def cancel(_image, _reference, stop_event, **_kwargs):
        stop_event.set()
        return [person_match.PersonCandidate((.2, .2, .2, .2), .9, .9, 'face', .9)]

    monkeypatch.setattr(person_match, 'rank_candidates', cancel)
    proposals = group_face_assist.propose_person_faces(
        AssistImage('reference', 'reference', str(reference_path)),
        REFERENCE_BOX,
        [AssistTarget('target', 'target', str(target_path), cross_group=True)],
        stop,
        on_progress=lambda *_args: progress.append(True),
    )

    assert proposals == []
    assert progress == []


def test_legacy_cross_group_full_frame_template_scan_is_bounded(monkeypatch, tmp_path):
    path = tmp_path / 'large.png'
    Image.fromarray(np.full((1500, 2400, 3), 100, dtype=np.uint8)).save(path)
    observed = []

    def peaks(gray, *_args, **_kwargs):
        observed.append(gray.shape)
        return []

    monkeypatch.setattr(group_face_assist, '_match_peaks', peaks)
    result = group_face_assist._propose_for_target(
        np.ones((30, 30), dtype=np.uint8),
        np.ones((30, 30), dtype=np.float32),
        AssistTarget('large', 'large', str(path), cross_group=True),
        (.3, .2, .1, .1),
        'reference',
        threading.Event(),
        .8,
    )

    assert max(observed[0]) <= group_face_assist.MAX_FULL_IMAGE_EDGE
    assert result.level == 'missing'


def test_workspace_target_collection_does_not_offer_existing_boxes():
    def asset(stem, group_id):
        return SimpleNamespace(
            stem=stem,
            group_id=group_id,
            preview_path=Path(f'/fake/{stem}.jpg'),
            primary_path=Path(f'/fake/{stem}.raw'),
            subject_features=None,
        )

    reference = asset('reference', 1)
    existing = asset('existing', 2)
    missed = asset('missed', 3)
    targets = group_face_assist.collect_targets(
        reference,
        [reference, existing, missed],
        {'existing': {'manual_face': [.2, .2, .2, .2]}},
        lambda item: item.stem,
        allow_cross_group=True,
    )

    assert [target.key for target in targets] == ['missed']
    assert targets[0].cross_group
