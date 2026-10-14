import io
import json
from pathlib import Path
import urllib.error
import urllib.request

import pytest

from ai_cull_assistant import ai_api


PROFILE = {
    "id": "test-profile",
    "name": "测试服务",
    "base_url": "https://gateway.example/api/v3",
    "model": "vision-model",
    "timeout": 120,
}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def response(text="ok", usage=None):
    value = {"choices": [{"message": {"content": text}}], "usage": usage or {}}
    return Response(json.dumps(value).encode())


def test_profile_round_trip_never_serialises_key(tmp_path, monkeypatch):
    stored = []
    monkeypatch.setattr(ai_api, "_write_secret", lambda profile_id, key: stored.append((profile_id, key)))
    saved = ai_api.save_profile(tmp_path, {k: v for k, v in PROFILE.items() if k != "id"}, "top-secret")

    assert stored == [(saved["id"], "top-secret")]
    assert ai_api.load_profiles(tmp_path) == [saved]
    raw = (tmp_path / ai_api.PROFILE_FILE).read_text(encoding="utf-8")
    assert "top-secret" not in raw
    assert "key" not in json.loads(raw)["profiles"][0]


def test_profile_update_preserves_id_and_enforces_unique_names(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_api, "_write_secret", lambda *_args: None)
    first = ai_api.save_profile(tmp_path, PROFILE)
    updated = ai_api.save_profile(tmp_path, {**first, "timeout": 30})
    assert updated["id"] == first["id"]
    assert ai_api.load_profiles(tmp_path)[0]["timeout"] == 30
    with pytest.raises(ValueError, match="已存在"):
        ai_api.save_profile(tmp_path, {**PROFILE, "id": "another", "name": "测试服务"})


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "ftp://example.com/v1",
        "https://user:password@example.com/v1",
        "https://example.com/v1?key=secret",
    ],
)
def test_profile_rejects_unsafe_urls(tmp_path, url):
    with pytest.raises(ValueError):
        ai_api.save_profile(tmp_path, {**PROFILE, "base_url": url})


def test_local_http_and_complete_endpoint_are_supported(tmp_path):
    local = ai_api.save_profile(tmp_path, {**PROFILE, "base_url": "http://localhost:11434/v1"})
    complete = ai_api.save_profile(
        tmp_path,
        {**PROFILE, "id": "complete", "name": "完整端点", "base_url": "https://example.com/openai/chat/completions"},
    )
    assert ai_api._completion_url(local["base_url"]) == "http://localhost:11434/v1/chat/completions"
    assert ai_api._completion_url(complete["base_url"]) == complete["base_url"]


def test_call_model_sends_multimodal_chat_request_once(tmp_path, monkeypatch):
    image = tmp_path / "tiny.png"
    image.write_bytes(b"png-data")
    calls = []
    monkeypatch.setattr(ai_api, "get_secret", lambda _profile_id: "top-secret")

    def open_request(request, timeout):
        calls.append((request, timeout))
        return response("visible", {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5})

    monkeypatch.setattr(ai_api, "_open_request", open_request)
    result = ai_api.call_model(PROFILE, "look", [image])

    assert result == {
        "text": "visible",
        "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
    }
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == "https://gateway.example/api/v3/chat/completions"
    assert timeout == 120
    assert request.get_header("Authorization") == "Bearer top-secret"
    body = json.loads(request.data)
    assert body["model"] == "vision-model"
    assert body["messages"][0]["content"][0] == {"type": "text", "text": "look"}
    image_url = body["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")


def test_response_redacts_key_and_only_returns_numeric_usage(tmp_path, monkeypatch):
    image = tmp_path / "tiny.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(ai_api, "get_secret", lambda _profile_id: "top-secret")
    value = {
        "choices": [{"message": {"content": "echo top-secret"}}],
        "usage": {
            "prompt_tokens": 3,
            "prompt_tokens_details": {"cached_tokens": 2, "note": "top-secret"},
            "provider_message": "top-secret",
        },
    }
    monkeypatch.setattr(ai_api, "_open_request", lambda *_args: Response(json.dumps(value).encode()))
    result = ai_api.call_model(PROFILE, "look", [image])
    assert result == {
        "text": "echo [REDACTED]",
        "usage": {"prompt_tokens": 3, "prompt_tokens_details": {"cached_tokens": 2}},
    }


def test_http_error_does_not_expose_remote_body_or_key(tmp_path, monkeypatch):
    image = tmp_path / "tiny.jpg"
    image.write_bytes(b"jpg")
    calls = []
    monkeypatch.setattr(ai_api, "get_secret", lambda _profile_id: "never-show-this")

    def fail(request, timeout):
        calls.append((request, timeout))
        raise urllib.error.HTTPError(request.full_url, 401, "remote said never-show-this", {}, io.BytesIO(b"body secret"))

    monkeypatch.setattr(ai_api, "_open_request", fail)
    with pytest.raises(ai_api.ApiError) as captured:
        ai_api.call_model(PROFILE, "look", [image])
    assert str(captured.value) == "API 请求失败（HTTP 401）。"
    assert len(calls) == 1


def test_explicit_image_and_response_bounds(tmp_path, monkeypatch):
    image = tmp_path / "large.webp"
    image.write_bytes(b"123")
    monkeypatch.setattr(ai_api, "get_secret", lambda _profile_id: "key")
    monkeypatch.setattr(ai_api, "MAX_IMAGE_BYTES", 2)
    with pytest.raises(ValueError, match="超过"):
        ai_api.call_model(PROFILE, "look", [image])

    image.write_bytes(b"12")
    monkeypatch.setattr(ai_api, "MAX_RESPONSE_BYTES", 10)
    monkeypatch.setattr(ai_api, "_open_request", lambda *_args: Response(b"x" * 11))
    with pytest.raises(ai_api.ApiError, match="响应超过"):
        ai_api.call_model(PROFILE, "look", [image])

    monkeypatch.setattr(ai_api, "MAX_REQUEST_BYTES", 10)
    with pytest.raises(ValueError, match="请求内容"):
        ai_api.call_model(PROFILE, "a prompt larger than ten bytes", [])


def test_redirect_handler_blocks_cross_origin_before_forwarding_request():
    handler = ai_api._SameOriginRedirectHandler()
    request = urllib.request.Request("https://one.example/v1/chat/completions", headers={"Authorization": "Bearer key"})
    with pytest.raises(urllib.error.HTTPError, match="cross-origin"):
        handler.redirect_request(request, None, 307, "redirect", {}, "https://two.example/v1/chat/completions")
