from datetime import datetime
from types import SimpleNamespace

from PIL import Image
import pytest

from ai_cull_assistant import ai_focus
from ai_cull_assistant.ai_api import ApiError
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from quality_cases import confirmed_settings, protocol_reply
import json


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
    settings=confirmed_settings(photo,CropSettings(),faces=[[.2,.2,.5,.5]])
    monkeypatch.setattr(ai_focus, "detail_features", lambda *_args: SimpleNamespace(face=(.2, .2, .5, .5)))
    calls = []

    def review(profile, prompt, images):
        calls.append((profile, prompt, images))
        return {**protocol_reply(prompt,'severe'), 'usage':{'total_tokens':17}}

    monkeypatch.setattr(ai_focus, "call_model", review)
    secret_profile = {**PROFILE, "key": "must-never-be-cached"}

    result = ai_focus.review_focus(photo, settings, secret_profile, tmp_path / "cache")
    again = ai_focus.review_focus(photo, settings, secret_profile, tmp_path / "cache")

    assert result == again
    assert result["status"] == "blur" and result["source"] == "api"
    assert any('total_tokens' in path.read_text('utf-8') for path in (tmp_path/'focus-evidence').rglob('*.json'))
    assert len(calls) == 1
    assert "must-never-be-cached" not in "".join(path.read_text("utf-8") for path in (tmp_path / "cache").rglob("*.json"))


@pytest.mark.parametrize(
    "change, message",
    [
        ('fence', "严格 JSON"), ('identity', "不匹配"), ('severity', "枚举"),
        ('extra', "字段无效"), ('duplicate', "严格 JSON"),
    ],
)
def test_review_focus_rejects_nonconforming_response_without_cache(tmp_path, monkeypatch, change, message):
    photo = asset(tmp_path, (1000, 1000))
    Image.new("RGB", (100, 100), "gray").save(photo.preview_path)
    monkeypatch.setattr(ai_focus, "detail_features", lambda *_args: SimpleNamespace(face=(.2, .2, .5, .5)))
    settings=confirmed_settings(photo,CropSettings(),faces=[[.2,.2,.5,.5]])
    def invalid(_profile,prompt,_images):
        value=json.loads(protocol_reply(prompt)['text'])
        if change=='identity': value['photo_identity']='other'
        elif change=='severity': value['parts'][0]['severity']='soft'
        elif change=='extra': value['extra']=1
        text=json.dumps(value)
        if change=='fence': text='```json\n'+text+'\n```'
        elif change=='duplicate': text=text[:-1]+',"reason":"duplicate"}'
        return {'text':text}
    monkeypatch.setattr(ai_focus, "call_model", invalid)

    with pytest.raises(ApiError, match=message):
        ai_focus.review_focus(photo, settings, PROFILE, tmp_path / "cache")
    from ai_cull_assistant.focus_protocol import PROMPT_VERSION
    assert not list((tmp_path / 'cache' / PROMPT_VERSION).glob('*.json'))


def test_no_face_sends_center_evidence_and_cannot_be_called_clear(tmp_path, monkeypatch):
    photo = asset(tmp_path, (1000, 800))
    monkeypatch.setattr(ai_focus, "detail_features", lambda *_args: None)
    seen = {}

    def review(_profile, prompt, images):
        seen.update(prompt=prompt, images=images)
        return {"text": '{"photo_identity":"source","status":"clear","reason":"背景纹理清楚"}'}

    monkeypatch.setattr(ai_focus, "call_model", review)
    with pytest.raises(ValueError,match='确认主要主体'):
        ai_focus.review_focus(photo, CropSettings(), PROFILE, tmp_path / "cache")
    assert seen == {}
    images=ai_focus.prepare_focus_images(photo,CropSettings(),tmp_path/'native-only')
    assert images[1].name=='center_native_01.png'


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
    settings=confirmed_settings(photo,CropSettings(),faces=[list(face[0])])
    ai_focus.review_focus(photo, settings, PROFILE, tmp_path / "cache")
    face[0] = (.1, .1, .5, .5)
    confirmed_settings(photo,settings,faces=[list(face[0])])
    ai_focus.review_focus(photo, settings, PROFILE, tmp_path / "cache")
    ai_focus.review_focus(photo, settings, {**PROFILE, "model": "other-model"}, tmp_path / "cache")
    ai_focus.review_focus(photo, settings, {**PROFILE, "base_url": "https://other.test/v1"}, tmp_path / "cache")
    ai_focus.review_focus(photo, settings, {**PROFILE, "temperature": .2}, tmp_path / "cache")
    Image.new("RGB", (1000, 1000), "white").save(photo.primary_path)
    with pytest.raises(ValueError,match='确认主要主体'):
        ai_focus.review_focus(photo, settings, PROFILE, tmp_path / "cache")
    confirmed_settings(photo,settings)
    ai_focus.review_focus(photo, settings, PROFILE, tmp_path / "cache")

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
        return protocol_reply(_args[1])

    monkeypatch.setattr(ai_focus, "call_model", review)
    settings=confirmed_settings(photo,CropSettings(),faces=[[.2,.2,.5,.5]])
    with pytest.raises(ApiError, match="offline"):
        ai_focus.review_focus(photo, settings, PROFILE, tmp_path / "cache")
    result = ai_focus.review_focus(photo, settings, PROFILE, tmp_path / "cache")

    assert result["status"] == "uncertain" and len(calls) == 2  # body has not been assessed


def test_multiple_selected_people_are_reviewed_and_aggregated(tmp_path, monkeypatch):
    photo = asset(tmp_path, (1000, 1000))
    Image.new("RGB", (100, 100), "gray").save(photo.preview_path)
    key = str(photo.primary_path.resolve()).casefold()
    settings = CropSettings(photos={key: {'selected_faces': [[.1,.1,.3,.3], [.6,.1,.3,.3]]}})
    confirmed_settings(photo,settings)
    subjects = [
        SimpleNamespace(face=(.1,.1,.3,.3), landmarks=None),
        SimpleNamespace(face=(.6,.1,.3,.3), landmarks=None),
    ]
    monkeypatch.setattr('ai_cull_assistant.subject.detail_features_list', lambda *a, **k: subjects)
    prompts = []

    def review(_profile, prompt, _images):
        prompts.append(prompt)
        return protocol_reply(prompt,'clear' if len(prompts)==1 else 'severe')

    monkeypatch.setattr(ai_focus, 'call_model', review)
    result = ai_focus.review_focus(photo, settings, PROFILE, tmp_path / 'cache')

    assert result['status'] == 'blur'
    assert [item['parts'][0]['severity'] for item in result['participants']] == ['clear', 'severe']
    assert all(item['parts'][1]['severity']=='uncertain' for item in result['participants'])
    assert len(prompts) == 2


def test_multiple_clear_face_reviews_preserve_separate_local_body_evidence(tmp_path, monkeypatch):
    photo = asset(tmp_path, (1000, 1000))
    Image.new("RGB", (100, 100), "gray").save(photo.preview_path)
    photo.clarity_evidence = {'body': {'state': 'uncertain'}}
    key = str(photo.primary_path.resolve()).casefold()
    settings = CropSettings(photos={key: {'selected_faces': [[.1,.1,.3,.3], [.6,.1,.3,.3]]}})
    confirmed_settings(photo,settings)
    subjects = [SimpleNamespace(face=tuple(box), landmarks=None) for box in settings.photos[key]['selected_faces']]
    monkeypatch.setattr('ai_cull_assistant.subject.detail_features_list', lambda *a, **k: subjects)
    calls = []
    def review(_profile, prompt, _images):
        calls.append(1)
        return protocol_reply(prompt)
    monkeypatch.setattr(ai_focus, 'call_model', review)

    result = ai_focus.review_focus(photo, settings, PROFILE, tmp_path / 'cache')
    assert result['status'] == 'uncertain'
    assert photo.clarity_evidence['body']['state']=='uncertain'
    assert all(item['subject_id'].startswith('face:') for item in result['participants'])
    assert all(item['parts'][1]['severity']=='uncertain' for item in result['participants'])
