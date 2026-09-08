from pathlib import Path

import cv2
from PIL import Image

from ai_cull_assistant.screening import _face_cascade, assess_subject_blur


def test_detector_and_preview_support_chinese_paths(tmp_path, monkeypatch):
    folder = tmp_path / "选片助手与照片"
    folder.mkdir()
    model = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    (folder / model.name).write_bytes(model.read_bytes())
    monkeypatch.setattr(cv2.data, "haarcascades", str(folder))
    _face_cascade.cache_clear()
    try:
        detector = _face_cascade()
        assert detector is not None and not detector.empty()
        photo = folder / "照片.jpg"
        Image.new("RGB", (160, 160), "white").save(photo)
        result = assess_subject_blur(photo)
        assert result.reason == "no_reliable_face"
    finally:
        _face_cascade.cache_clear()
