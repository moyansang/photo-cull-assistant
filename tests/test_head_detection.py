import numpy as np
import pytest
import json

from ai_cull_assistant import head_detection


def body_detection(score=.9):
    # head xyxy, hip/full-body, shoulder/upper-body, score
    return np.array([70, 40, 170, 150, 120, 280, 120, 10,
                     120, 150, 120, 25, score], dtype=np.float32)


def test_body_anchor_geometry_returns_head_without_claiming_a_face():
    box = head_detection._body_anchor_box(body_detection(), (320, 240))
    assert box is not None
    x, y, width, height = box
    assert x < 120 < x + width
    assert y < 80 < y + height
    assert y + height < 160
    json.dumps(box)


def test_body_anchor_geometry_rejects_weak_or_incoherent_person():
    assert head_detection._body_anchor_box(body_detection(.5), (320, 240)) is None
    person = body_detection()
    person[8:10] = (230, 300)
    assert head_detection._body_anchor_box(person, (320, 240)) is None


def test_body_fallback_returns_distinct_head_evidence(monkeypatch):
    class Detector:
        def infer(self, _image):
            return np.stack([body_detection()])

    monkeypatch.setattr("ai_cull_assistant.body_focus._get_models", lambda: (Detector(), object()))
    found = head_detection.detect_heads(np.zeros((320, 240, 3), np.uint8))
    assert len(found) == 1
    assert found[0].source == "body_anchor_head"
    assert found[0].normalized_box(240, 320)[2] < 1


def test_existing_reliable_face_suppresses_overlapping_body_head(monkeypatch):
    candidate = head_detection.HeadDetection((75, 40, 90, 105), .8, "body_anchor_head")
    monkeypatch.setattr(head_detection, "_body_anchor_heads", lambda *_args, **_kwargs: [candidate])
    result = head_detection.detect_heads(
        np.zeros((240, 240, 3), np.uint8),
        reliable_face_boxes=[(90, 60, 55, 65)],
    )
    assert result == []


def test_invalid_or_tiny_images_do_not_run_detectors(monkeypatch):
    monkeypatch.setattr(head_detection, "_body_anchor_heads", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))
    assert head_detection.detect_heads(np.array([])) == []
    assert head_detection.detect_heads(np.zeros((20, 100, 3), np.uint8)) == []


def test_head_roi_retry_maps_only_real_landmark_detection(monkeypatch):
    from ai_cull_assistant.yunet import FaceDetection

    detected = FaceDetection(
        (160, 120, 160, 200),
        ((200, 180), (280, 180), (240, 220), (210, 270), (270, 270)),
        .91,
    )
    monkeypatch.setattr("ai_cull_assistant.yunet.detect", lambda *_args: [detected])
    head = head_detection.HeadDetection((100, 80, 100, 120), .9, "body_anchor_head")
    found = head_detection.refine_face_in_head(np.zeros((320, 320, 3), np.uint8), head)
    assert found is not None
    assert found.score == .91
    assert all(np.isfinite(found.box))


@pytest.mark.parametrize("canvas,head_box,direction", [
    ((360, 320), (100, 80, 80, 90), "up"),
    ((1400, 1200), (250, 180, 600, 700), "down"),
])
def test_head_roi_retry_uses_actual_resize_ratios(monkeypatch, canvas, head_box, direction):
    from ai_cull_assistant.yunet import FaceDetection

    seen = []

    def fake_detect(image, _threshold):
        seen.append(image.shape[:2])
        height, width = image.shape[:2]
        return [FaceDetection(
            (.3 * width, .3 * height, .35 * width, .4 * height),
            ((.38 * width, .42 * height), (.55 * width, .42 * height),
             (.47 * width, .52 * height), (.4 * width, .63 * height),
             (.54 * width, .63 * height)),
            .92,
        )]

    monkeypatch.setattr("ai_cull_assistant.yunet.detect", fake_detect)
    head = head_detection.HeadDetection(head_box, .9, "body_anchor_head")
    found = head_detection.refine_face_in_head(np.zeros((*canvas, 3), np.uint8), head)
    assert found is not None
    source_roi_long_edge = round(max(head_box[2] * 1.60, head_box[3] * 1.60))
    if direction == "up":
        assert max(seen[0]) > source_roi_long_edge
    else:
        assert max(seen[0]) < source_roi_long_edge
    x, y, width, height = found.box
    hx, hy, hw, hh = head_box
    assert hx <= x + width / 2 <= hx + hw
    assert hy <= y + height / 2 <= hy + hh
