from pathlib import Path
import threading
from PIL import Image
from ai_cull_assistant import yunet
from ai_cull_assistant.screening import assess_subject_blur


def test_detector_and_preview_support_chinese_paths(tmp_path, monkeypatch):
    folder = tmp_path / '选片助手与照片'
    folder.mkdir()
    model = folder / yunet.MODEL_PATH.name
    model.write_bytes(yunet.MODEL_PATH.read_bytes())
    monkeypatch.setattr(yunet, 'MODEL_PATH', model)
    monkeypatch.setattr(yunet, '_local', threading.local())
    assert yunet.detector() is not None
    photo = folder / '照片.jpg'
    Image.new('RGB', (160,160), 'white').save(photo)
    assert assess_subject_blur(photo).reason == 'no_reliable_face'
