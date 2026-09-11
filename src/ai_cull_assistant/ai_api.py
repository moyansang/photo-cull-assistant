"""Bounded OpenAI-compatible Chat Completions transport.

Profiles contain connection metadata only.  API keys are stored as generic
credentials in Windows Credential Manager and are never written to JSON.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .ai_presets import matching_preset


PROFILE_FILE = "ai-api-profiles.json"
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
MAX_IMAGES = 20
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 48 * 1024 * 1024
MAX_REQUEST_BYTES = 70 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_CREDENTIAL_PREFIX = "PhotoCullAssistant/AIAPI/"
_PROFILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_IMAGE_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_USAGE_FIELDS = {
    "completion_tokens",
    "prompt_tokens",
    "total_tokens",
}
_USAGE_DETAIL_FIELDS = {
    "accepted_prediction_tokens",
    "audio_tokens",
    "cached_tokens",
    "image_tokens",
    "reasoning_tokens",
    "rejected_prediction_tokens",
}


class ApiError(RuntimeError):
    """Safe, user-displayable API error."""


class CredentialError(ApiError):
    """Windows Credential Manager operation failed."""


def _profile_id(value: object) -> str:
    profile_id = str(value or "")
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError("配置 ID 无效。")
    return profile_id


def _is_localhost(hostname: str | None) -> bool:
    return (hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}


def _normalise_base_url(value: object) -> str:
    url = str(value or "").strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("API 地址无效。") from exc
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("API 地址无效。")
    if parsed.query or parsed.fragment:
        raise ValueError("API 地址不能包含查询参数或片段。")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and _is_localhost(parsed.hostname)):
        raise ValueError("API 地址必须使用 HTTPS；仅 localhost 可使用 HTTP。")
    # Accessing .port above rejects malformed and out-of-range ports.
    del port
    return url


def _normalise_profile(profile: Mapping[str, object], *, create_id: bool = False) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise ValueError("API 配置无效。")
    raw_id = profile.get("id")
    profile_id = uuid.uuid4().hex if create_id and not raw_id else _profile_id(raw_id)
    name = str(profile.get("name") or "").strip()
    model = str(profile.get("model") or "").strip()
    if not name:
        raise ValueError("请输入配置名称。")
    if len(name) > 80:
        raise ValueError("配置名称不能超过 80 个字符。")
    if not model:
        raise ValueError("请输入模型名称。")
    if len(model) > 200:
        raise ValueError("模型名称过长。")
    try:
        timeout = int(profile.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError) as exc:
        raise ValueError("超时必须是整数秒。") from exc
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"超时必须在 1–{MAX_TIMEOUT} 秒之间。")
    result: dict[str, Any] = {
        "id": profile_id,
        "name": name,
        "base_url": _normalise_base_url(profile.get("base_url")),
        "model": model,
        "timeout": timeout,
    }
    max_tokens = profile.get("max_tokens")
    if max_tokens not in (None, ""):
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError) as exc:
            raise ValueError("最大输出 token 数必须是整数。") from exc
        if not 1 <= max_tokens <= 1_000_000:
            raise ValueError("最大输出 token 数必须在 1–1000000 之间。")
        result["max_tokens"] = max_tokens
    if matching_preset(profile):
        result['preset_id'] = profile['preset_id']
    return result


def load_profiles(settings_dir: Path) -> list[dict[str, Any]]:
    """Load valid profiles, silently ignoring corrupt or unsupported entries."""
    path = Path(settings_dir) / PROFILE_FILE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw_profiles = document.get("profiles") if isinstance(document, dict) else None
    if not isinstance(raw_profiles, list):
        return []
    profiles: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for value in raw_profiles:
        try:
            profile = _normalise_profile(value)
        except (TypeError, ValueError):
            continue
        if profile["id"] not in seen_ids:
            profiles.append(profile)
            seen_ids.add(profile["id"])
    return profiles


def _write_profiles(settings_dir: Path, profiles: list[dict[str, Any]]) -> None:
    directory = Path(settings_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PROFILE_FILE
    temporary = path.with_suffix(path.suffix + ".tmp")
    document = {"version": 1, "profiles": profiles}
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def save_profile(
    settings_dir: Path,
    profile: Mapping[str, object],
    key: str | None = None,
) -> dict[str, Any]:
    """Create or replace a profile and optionally update its stored secret.

    ``key=None`` keeps the existing credential.  An empty key deletes it.
    """
    saved = _normalise_profile(profile, create_id=True)
    profiles = load_profiles(Path(settings_dir))
    for other in profiles:
        if other["id"] != saved["id"] and other["name"].casefold() == saved["name"].casefold():
            raise ValueError("配置名称已存在。")
    previous = next((p for p in profiles if p['id'] == saved['id']), None)
    if previous and _completion_url(previous['base_url']) != _completion_url(saved['base_url']):
        if key is None:
            raise CredentialError('修改 API 服务地址后，请重新输入对应服务的密钥。')
        # A fresh credential ID keeps a failed JSON write from pairing the new
        # service's key with the old service's still-persisted endpoint.
        saved['id'] = uuid.uuid4().hex
    preset = matching_preset(saved)
    if key is None and previous is None and preset:
        # Only reuse credentials from a verified preset on the same service/region.
        for other in profiles:
            other_preset = matching_preset(other)
            if other_preset and other_preset['credential_group'] == preset['credential_group']:
                key = get_secret(other['id'])
                if key:
                    break
    replaced = False
    for index, other in enumerate(profiles):
        if other["id"] == (previous["id"] if previous else saved["id"]):
            profiles[index] = saved
            replaced = True
            break
    if not replaced:
        profiles.append(saved)

    # Store the credential first so JSON never points at a newly saved profile
    # whose supplied key was lost.  Neither operation serialises the key.
    if key is not None:
        if not isinstance(key, str):
            raise ValueError("API 密钥无效。")
        if key:
            _write_secret(saved["id"], key)
        else:
            _delete_secret(saved["id"])
    _write_profiles(Path(settings_dir), profiles)
    return saved


def _credential_api():
    if os.name != "nt":
        raise CredentialError("API 密钥存储仅支持 Windows Credential Manager。")

    class Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    return Credential, advapi32


def _credential_target(profile_id: str) -> str:
    return _CREDENTIAL_PREFIX + _profile_id(profile_id)


def _write_secret(profile_id: str, key: str) -> None:
    Credential, advapi32 = _credential_api()
    blob = key.encode("utf-16-le")
    if not blob or len(blob) > 5120:
        raise CredentialError("API 密钥长度无效。")
    buffer = ctypes.create_string_buffer(blob)
    credential = Credential()
    credential.Type = 1  # CRED_TYPE_GENERIC
    credential.TargetName = _credential_target(profile_id)
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE (current Windows user)
    credential.UserName = "PhotoCullAssistant"
    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        raise CredentialError(f"无法保存 API 密钥（Windows 错误 {ctypes.get_last_error()}）。")


def get_secret(profile_id: str) -> str | None:
    """Read a profile API key from Windows Credential Manager."""
    Credential, advapi32 = _credential_api()
    pointer = ctypes.POINTER(Credential)()
    if not advapi32.CredReadW(_credential_target(profile_id), 1, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == 1168:  # ERROR_NOT_FOUND
            return None
        raise CredentialError(f"无法读取 API 密钥（Windows 错误 {error}）。")
    try:
        credential = pointer.contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return blob.decode("utf-16-le")
    except (UnicodeError, ValueError) as exc:
        raise CredentialError("保存的 API 密钥无法读取。") from exc
    finally:
        advapi32.CredFree(pointer)


def _delete_secret(profile_id: str) -> None:
    _, advapi32 = _credential_api()
    if not advapi32.CredDeleteW(_credential_target(profile_id), 1, 0):
        error = ctypes.get_last_error()
        if error != 1168:
            raise CredentialError(f"无法删除 API 密钥（Windows 错误 {error}）。")


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80 if parsed.scheme.lower() == "http" else None
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if _origin(req.full_url) != _origin(newurl):
            raise urllib.error.HTTPError(req.full_url, code, "cross-origin redirect blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_request(request: urllib.request.Request, timeout: int):
    opener = urllib.request.build_opener(_SameOriginRedirectHandler())
    return opener.open(request, timeout=timeout)


def _completion_url(base_url: str) -> str:
    """Append the Chat Completions path unless the full endpoint was supplied."""
    return base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"


def _image_content(image_paths: list[Path]) -> list[dict[str, Any]]:
    if len(image_paths) > MAX_IMAGES:
        raise ValueError(f"一次最多发送 {MAX_IMAGES} 张图片。")
    content: list[dict[str, Any]] = []
    total = 0
    for raw_path in image_paths:
        path = Path(raw_path)
        mime = _IMAGE_TYPES.get(path.suffix.lower())
        if mime is None:
            raise ValueError(f"不支持的图片格式：{path.suffix or path.name}")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise ValueError(f"无法读取图片：{path.name}") from exc
        if size > MAX_IMAGE_BYTES:
            raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MiB：{path.name}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"无法读取图片：{path.name}") from exc
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MiB：{path.name}")
        total += len(data)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError(f"图片总大小不能超过 {MAX_TOTAL_IMAGE_BYTES // (1024 * 1024)} MiB。")
        encoded = base64.b64encode(data).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    return content


def _response_text(document: Mapping[str, Any]) -> str:
    try:
        message = document["choices"][0]["message"]
        value = message.get("content")
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ApiError("API 返回了无法识别的响应。") from exc
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [part.get("text") for part in value if isinstance(part, dict) and isinstance(part.get("text"), str)]
        if parts:
            return "\n".join(parts)
    refusal = message.get("refusal") if isinstance(message, dict) else None
    if isinstance(refusal, str):
        return refusal
    raise ApiError("API 响应中没有文本结果。")


def _numeric_usage(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _safe_usage(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, Any] = {}
    for name in _USAGE_FIELDS:
        if name in value and (item := _numeric_usage(value[name])) is not None:
            clean[name] = item
    for name in ("prompt_tokens_details", "completion_tokens_details"):
        source = value.get(name)
        if not isinstance(source, dict):
            continue
        details = {
            field: item
            for field in _USAGE_DETAIL_FIELDS
            if field in source and (item := _numeric_usage(source[field])) is not None
        }
        if details:
            clean[name] = details
    return clean


def call_model(
    profile: Mapping[str, object],
    prompt: str,
    image_paths: list[Path],
) -> dict[str, Any]:
    """Send exactly one multimodal Chat Completions request."""
    saved = _normalise_profile(profile)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("提示词不能为空。")
    if not isinstance(image_paths, list):
        raise ValueError("图片路径必须是列表。")
    key = get_secret(saved["id"])
    if not key:
        raise CredentialError("该配置尚未保存 API 密钥。")

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.extend(_image_content(image_paths))
    payload: dict[str, Any] = {
        "model": saved["model"],
        "messages": [{"role": "user", "content": content}],
    }
    preset = matching_preset(saved)
    if preset:
        payload.update(preset.get('request_options', {}))
    if "max_tokens" in saved:
        parameter = preset.get('token_parameter', 'max_tokens') if preset else 'max_tokens'
        payload[parameter] = saved['max_tokens']
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_limit = min(MAX_REQUEST_BYTES, preset.get('max_request_bytes', MAX_REQUEST_BYTES)) if preset else MAX_REQUEST_BYTES
    if len(body) > request_limit:
        raise ValueError(f"API 请求内容不能超过 {request_limit // (1024 * 1024)} MiB，请拆小本批重试。")
    request = urllib.request.Request(
        _completion_url(saved["base_url"]),
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "PhotoCullAssistant/ai-api",
        },
    )
    try:
        with _open_request(request, saved["timeout"]) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code if isinstance(exc.code, int) else 0
        detail = ""
        if status == 400:
            try:
                error_document = json.loads(exc.read(16384).decode("utf-8"))
                error = error_document.get("error", {})
                message = error.get("message", "") if isinstance(error, dict) else error
                if isinstance(message, str):
                    import re
                    message = message.replace(key, "[REDACTED]")
                    message = re.sub(r"data:image/[^\s]+", "[图片数据]", message)
                    message = re.sub(r"[A-Za-z0-9+/=_-]{80,}", "[长数据已隐藏]", message)
                    detail = "\n服务端原因：" + message[:800] if message else ""
            except (ValueError, OSError, AttributeError):
                pass
        raise ApiError(f"API 请求失败（HTTP {status}）。" + detail) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ApiError("无法连接 API 服务。") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ApiError(f"API 响应超过 {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB 限制。")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise ApiError("API 返回了无效的 JSON。") from None
    if not isinstance(document, dict):
        raise ApiError("API 返回了无法识别的响应。")
    text = _response_text(document).replace(key, "[REDACTED]")
    return {"text": text, "usage": _safe_usage(document.get("usage"))}
