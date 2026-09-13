import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from ai_cull_assistant import body_focus


class Detector:
    def __init__(self, detections):
        self.detections = detections

    def infer(self, _image):
        return self.detections


def test_model_loaders_use_bytes_for_chinese_installation_paths(tmp_path, monkeypatch):
    model = tmp_path / '中文模型.onnx'
    model.write_bytes(b'onnx-test-bytes')
    loaded = []
    monkeypatch.setattr(body_focus.cv2.dnn, 'readNetFromONNX',
                        lambda buffer: loaded.append(buffer.tobytes()) or object())
    body_focus._PersonDetector(model)
    body_focus._PoseEstimator(model)
    assert loaded == [b'onnx-test-bytes', b'onnx-test-bytes']


class Pose:
    def __init__(self, result=None):
        self.result = result

    def infer(self, _image, _person):
        return self.result


def detection(face=(80, 40, 130, 100), score=.9):
    # detector face box + hip/full-body/shoulder/upper-body points + score
    return np.asarray([*face, 110, 250, 110, 20, 110, 100, 110, 40, score], dtype=np.float32)


def pose_result(width=240, height=320):
    points = np.zeros((33, 5), dtype=np.float32)
    coords = {
        11: (80, 100), 12: (160, 100), 13: (55, 160), 14: (185, 160),
        15: (45, 230), 16: (195, 230), 23: (90, 205), 24: (150, 205),
        25: (85, 260), 26: (155, 260), 27: (80, 310), 28: (160, 310),
    }
    for index, xy in coords.items():
        points[index, :2] = xy
        points[index, 3:] = 0.99
    return {"landmarks": points, "mask": np.full((height, width), 255, np.uint8), "confidence": .99}


def test_anchor_generator_matches_model_shape():
    anchors = body_focus._ssd_anchors()
    assert anchors.shape == (2254, 2)
    assert np.all((anchors > 0) & (anchors < 1))


def test_headshot_without_visible_body_does_not_become_pending(monkeypatch):
    monkeypatch.setattr(body_focus, "_get_models", lambda: (Detector(np.empty((0, 13))), Pose()))
    result = body_focus.assess_body_focus(Image.new("RGB", (400, 500)), (.2, .1, .5, .36))
    assert result["state"] == "clear"
    assert result["review_kind"] == "none"
    assert result["reasons"] == ["body_not_visible_headshot"]


def test_full_body_detection_failure_is_uncertain(monkeypatch):
    monkeypatch.setattr(body_focus, "_get_models", lambda: (Detector(np.empty((0, 13))), Pose()))
    result = body_focus.assess_body_focus(Image.new("RGB", (400, 600)), (.4, .08, .1, .09))
    assert result["state"] == "uncertain"
    assert result["review_kind"] == "unsupported"
    assert result["reasons"] == ["selected_person_not_associated"]


def test_association_uses_selected_face_and_rejects_ambiguous_candidates():
    face = np.array([80, 40, 130, 100], dtype=np.float32)
    wrong = detection((250, 20, 300, 80), .99)
    right = detection((78, 38, 132, 102), .80)
    selected, diagnostics = body_focus._associate_person(np.stack([wrong, right]), face)
    assert selected is not None
    assert selected[-1] == pytest.approx(.80)
    assert diagnostics["face_iou"] > .8

    first = detection((78, 38, 132, 102), .80)
    second = detection((79, 39, 133, 103), .81)
    selected, diagnostics = body_focus._associate_person(np.stack([first, second]), face)
    assert selected is None
    assert diagnostics["ambiguous"] is True


def test_low_texture_and_directionality_cannot_auto_reject():
    plain = np.full((300, 300, 3), 128, dtype=np.uint8)
    full_mask = np.full((300, 300), 255, dtype=np.uint8)
    evidence = body_focus._region_metrics(plain, full_mask, (20, 20, 280, 280))
    assert evidence["state"] == "insufficient"

    stripes = np.zeros((300, 300, 3), dtype=np.uint8)
    stripes[:, ::24] = 255
    evidence = body_focus._region_metrics(stripes, full_mask, (20, 20, 280, 280))
    assert evidence["state"] != "severe_blur"


def test_automatic_rejection_requires_multiple_major_regions():
    def region(name, family, state):
        return {"name": name, "family": family, "evidence": {"state": state}}

    assert body_focus._aggregate_regions([region("torso", "torso", "severe_blur")])[0] == "uncertain"
    assert body_focus._aggregate_regions([
        region("torso", "torso", "severe_blur"),
        region("left_upper_arm", "left_arm", "severe_blur"),
    ])[0] == "severe_blur"
    assert body_focus._aggregate_regions([
        region("torso", "torso", "clear"),
        region("left_upper_arm", "left_arm", "clear"),
        region("right_upper_arm", "right_arm", "uncertain"),
    ])[0] == "uncertain"


def test_result_is_json_serializable_with_native_boxes(monkeypatch):
    person = detection()
    monkeypatch.setattr(body_focus, "_get_models", lambda: (Detector(np.stack([person])), Pose(pose_result())))
    yy, xx = np.indices((320, 240))
    pixels = ((xx // 4 + yy // 4) % 2 * 255).astype(np.uint8)
    image = Image.fromarray(np.repeat(pixels[:, :, None], 3, axis=2), "RGB")
    result = body_focus.assess_body_focus(image, (80 / 240, 40 / 320, 50 / 240, 60 / 320))
    json.dumps(result)
    assert result["regions"]
    assert all(isinstance(value, int) for region in result["regions"] for value in region["box"])


def test_review_boxes_only_expose_evidence_for_motion_review():
    evidence = {
        "review_kind": "motion_suspected",
        "regions": [
            {"family": "left_arm", "box": [10, 20, 30, 50], "evidence": {"state": "uncertain"}},
            {"family": "torso", "box": [5, 5, 100, 150], "evidence": {"state": "uncertain"}},
            {"family": "right_leg", "box": [0, 0, 200, 200], "evidence": {"state": "clear"}},
        ],
    }
    assert body_focus.body_review_boxes(evidence) == [(5, 5, 100, 150), (10, 20, 30, 50)]
    evidence["review_kind"] = "unsupported"
    assert body_focus.body_review_boxes(evidence) == []


def test_manifest_is_pinned_and_downloads_are_not_tracked():
    root = Path(__file__).parents[1]
    manifest = json.loads((root / "body_models" / "manifest.json").read_text("utf-8"))
    assert manifest["source_commit"] == "47534e27c9851bb1128ccc0102f1145e27f23f98"
    assert {entry["sha256"] for entry in manifest["models"]} == set(body_focus.MODEL_SHA256)
    assert not list((root / "body_models").glob("*.onnx"))


def test_real_models_cpu_smoke_when_build_cache_exists():
    try:
        detector_path, pose_path = body_focus.find_body_models()
    except body_focus.BodyModelUnavailable:
        pytest.skip("body models are an optional verified build-cache download")
    detector = body_focus._PersonDetector(detector_path)
    pose = body_focus._PoseEstimator(pose_path)
    assert detector.infer(np.zeros((256, 256, 3), np.uint8)).shape[1] == 13
    assert pose.net is not None
