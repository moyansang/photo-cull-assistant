from datetime import datetime
from types import SimpleNamespace

from PIL import Image
import pytest

from ai_cull_assistant import ai_focus
from ai_cull_assistant.ai_api import ApiError
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset


PROFILE = {
    "id": "focus-profile",
    "name": "Focus",
    "base_url": "https://example.test/v1",
    "model": "vision-model",
    "timeout": 30,
}


@pytest.fixture(autouse=True)
def pillow_native_loader(monkeypatch):
    # The production loader adds RAW support; these tests use ordinary PNG files.
    monkeypatch.setattr(
        ai_focus,
        "load_full_image",
        lambda photo: Image.open(photo.raw_path or photo.primary_path),
    )


def asset(tmp_path, size=(2400, 1600)):
    source = tmp_path / "source.png"
    Image.new("RGB", size, "gray").save(source)
    preview = tmp_path / "preview.jpg"
    Image.new("RGB", (300, 200), "gray").save(preview)
    return PhotoAsset("source", source, source, None, source, datetime.now(), ".png", preview_path=preview)


def test_prepare_focus_images_uses_overview_and_native_tiles(tmp_path, monkeypatch):
    photo = asset(tmp_path)
    monkeypatch.setattr(ai_focus, "detail_features", lambda *_args: SimpleNamespace(face=(.1, .1, .8, .8)))

    images = ai_focus.prepare_focus_images(photo, CropSettings(), tmp_path / "evidence")

    assert images[0].name == "overview.png"
    assert all(Image.open(path).width <= 1024 and Image.open(path).height <= 1024 for path in images)
    assert Image.open(images[0]).size == (1024, 683)
    # The padded native face region spans the source and is tiled, not resized.
    assert len(images) == 7
    assert sum(Image.open(path).width * Image.open(path).height for path in images[1:]) == 2400 * 1600


def test_review_focus_validates_and_caches_success_without_profile_secret(tmp_path, monkeypatch):
    photo = asset(tmp_path, (1000, 1000))
    Image.new("RGB", (100, 100), "gray").save(photo.preview_path)
    monkeypatch.setattr(ai_focus, "detail_features", lambda *_args: SimpleNamespace(face=(.2, .2, .5, .5)))
    calls = []

    def review(profile, prompt, images):
        calls.append((profile, prompt, images))
        return {
            "text": '{"photo_identity":"source","status":"blur","reason":"眼睛细节明显失焦"}',
            "usage": {"total_tokens": 17},
        }

    monkeypatch.setattr(ai_focus, "call_model", review)
    secret_profile = {**PROFILE, "key": "must-never-be-cached"}

    result = ai_focus.review_focus(photo, CropSettings(), secret_profile, tmp_path / "cache")
    again = ai_focus.review_focus(photo, CropSettings(), secret_profile, tmp_path / "cache")

    assert result == again
    assert result["status"] == "blur" and result["source"] == "api"
    assert result["usage"] == {"total_tokens": 17}
    assert len(calls) == 1
    assert "must-never-be-cached" not in "".join(path.read_text("utf-8") for path in (tmp_path / "cache").rglob("*.json"))


@pytest.mark.parametrize(
    "text, message",
    [
        ('```json\n{"photo_identity":"source","status":"clear","reason":"清楚"}\n```', "严格 JSON"),
        ('{"photo_identity":"other","status":"clear","reason":"清楚"}', "照片标识"),
        ('{"photo_identity":"source","status":"soft","reason":"清楚"}', "无效状态"),
        ('{"photo_identity":"source","status":"clear","reason":"清楚","extra":1}', "字段无效"),
        ('{"photo_identity":"source","status":"clear","status":"blur","reason":"清楚"}', "严格 JSON"),
    ],
)
def test_review_focus_rejects_nonconforming_response_without_cache(tmp_path, monkeypatch, text, message):
    photo = asset(tmp_path, (1000, 1000))
    Image.new("RGB", (100, 100), "gray").save(photo.preview_path)
    monkeypatch.setattr(ai_focus, "detail_features", lambda *_args: SimpleNamespace(face=(.2, .2, .5, .5)))
    monkeypatch.setattr(ai_focus, "call_model", lambda *_args: {"text": text})

    with pytest.raises(ApiError, match=message):
        ai_focus.review_focus(photo, CropSettings(), PROFILE, tmp_path / "cache")
    assert not list((tmp_path / "cache").rglob("*.json"))


def test_no_face_sends_center_evidence_and_cannot_be_called_clear(tmp_path, monkeypatch):
    photo = asset(tmp_path, (1000, 800))
    monkeypatch.setattr(ai_focus, "detail_features", lambda *_args: None)
    seen = {}

    def review(_profile, prompt, images):
        seen.update(prompt=prompt, images=images)
        return {"text": '{"photo_identity":"source","status":"clear","reason":"背景纹理清楚"}'}

    monkeypatch.setattr(ai_focus, "call_model", review)
    result = ai_focus.review_focus(photo, CropSettings(), PROFILE, tmp_path / "cache")

    assert result["status"] == "uncertain"
    assert "未能可靠定位主要人脸" in seen["prompt"]
    assert seen["images"][1].name == "center_native_01.png"


def test_cache_includes_source_face_and_profile_model(tmp_path, monkeypatch):
    photo = asset(tmp_path, (1000, 1000))
    Image.new("RGB", (100, 100), "gray").save(photo.preview_path)
    face = [(.2, .2, .5, .5)]
    monkeypatch.setattr(ai_focus, "detail_features", lambda *_args: SimpleNamespace(face=face[0]))
    calls = []

    def review(_profile, _prompt, _images):
        calls.append(1)
        return {"text": '{"photo_identity":"source","status":"uncertain","reason":"细节不足"}'}

    monkeypatch.setattr(ai_focus, "call_model", review)
    ai_focus.review_focus(photo, CropSettings(), PROFILE, tmp_path / "cache")
    face[0] = (.1, .1, .5, .5)
    ai_focus.review_focus(photo, CropSettings(), PROFILE, tmp_path / "cache")
    ai_focus.review_focus(photo, CropSettings(), {**PROFILE, "model": "other-model"}, tmp_path / "cache")
    ai_focus.review_focus(photo, CropSettings(), {**PROFILE, "base_url": "https://other.test/v1"}, tmp_path / "cache")
    ai_focus.review_focus(photo, CropSettings(), {**PROFILE, "temperature": .2}, tmp_path / "cache")
    Image.new("RGB", (1000, 1000), "white").save(photo.primary_path)
    ai_focus.review_focus(photo, CropSettings(), PROFILE, tmp_path / "cache")

    assert len(calls) == 6


def test_api_error_does_not_poison_cache(tmp_path, monkeypatch):
    photo = asset(tmp_path, (1000, 1000))
    Image.new("RGB", (100, 100), "gray").save(photo.preview_path)
    monkeypatch.setattr(ai_focus, "detail_features", lambda *_args: SimpleNamespace(face=(.2, .2, .5, .5)))
    calls = []

    def review(*_args):
        calls.append(1)
        if len(calls) == 1:
            raise ApiError("offline")
        return {"text": '{"photo_identity":"source","status":"clear","reason":"眼睛细节清楚"}'}

    monkeypatch.setattr(ai_focus, "call_model", review)
    with pytest.raises(ApiError, match="offline"):
        ai_focus.review_focus(photo, CropSettings(), PROFILE, tmp_path / "cache")
    result = ai_focus.review_focus(photo, CropSettings(), PROFILE, tmp_path / "cache")

    assert result["status"] == "clear" and len(calls) == 2
