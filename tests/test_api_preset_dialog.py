import json
import tkinter as tk

import pytest

import ai_cull_assistant.ai_api_dialog as dialog_module
from ai_cull_assistant.ai_api_dialog import ApiConfigDialog
from ai_cull_assistant.ai_presets import PRESETS, preset_profile


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk is unavailable: {exc}")
    root.withdraw()
    yield root
    if root.winfo_exists():
        root.destroy()


def _profile(profile_id, preset_id):
    profile = preset_profile(preset_id)
    profile["id"] = profile_id
    return profile


def _write_profiles(settings_dir, profiles):
    (settings_dir / "ai-api-profiles.json").write_text(
        json.dumps({"version": 1, "profiles": profiles}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_new_dialog_defaults_to_qwen_preset_with_only_key_exposed(tk_root, tmp_path):
    dialog = ApiConfigDialog(tk_root, tmp_path)
    try:
        expected = PRESETS["qwen-vl-plus"]
        assert dialog.model_var.get() == expected["model"]
        assert dialog.base_url_var.get() == expected["base_url"]
        assert dialog.name_var.get() == expected["name"]
        assert dialog.timeout_var.get() == str(expected["timeout"])
        assert dialog.max_tokens_var.get() == str(expected["max_tokens"])
        assert dialog.current_id is None
        assert dialog.advanced_frame.winfo_manager() == ""
        values, key = dialog._form_values()
        assert values["preset_id"] == "qwen-vl-plus"
        assert key is None
    finally:
        dialog.destroy()


def test_custom_preset_is_editable_and_never_emits_preset_id(tk_root, tmp_path):
    dialog = ApiConfigDialog(tk_root, tmp_path)
    try:
        dialog.key_var.set("unsaved-secret")
        dialog.preset_var.set(dialog._preset_names["custom"])
        dialog._select_preset()

        assert dialog.current_id is None
        assert dialog.key_var.get() == ""
        assert dialog.base_url_var.get() == ""
        assert dialog.model_var.get() == ""
        assert dialog.advanced_var.get() is True
        assert dialog.advanced_frame.winfo_manager() == "grid"
        assert all(str(entry.cget("state")) == "normal" for entry in dialog.advanced_entries)
        values, _ = dialog._form_values()
        assert "preset_id" not in values
    finally:
        dialog.destroy()


def test_legacy_profile_loads_as_custom_without_changing_its_fields(tk_root, tmp_path):
    legacy = {
        "id": "legacy-profile",
        "name": "旧版配置",
        "base_url": PRESETS["qwen-vl-plus"]["base_url"],
        "model": PRESETS["qwen-vl-plus"]["model"],
        "timeout": 77,
        "max_tokens": 1234,
    }
    _write_profiles(tmp_path, [legacy])

    dialog = ApiConfigDialog(tk_root, tmp_path)
    try:
        assert dialog.current_id == "legacy-profile"
        assert dialog._preset_id() == "custom"
        assert dialog.name_var.get() == "旧版配置"
        assert dialog.timeout_var.get() == "77"
        assert dialog.max_tokens_var.get() == "1234"
        values, _ = dialog._form_values()
        assert values["id"] == "legacy-profile"
        assert "preset_id" not in values
    finally:
        dialog.destroy()


def test_switching_presets_clears_key_and_only_reuses_matching_profile(tk_root, tmp_path):
    qwen = _profile("qwen-saved", "qwen-vl-plus")
    openai = _profile("openai-saved", "openai-mini")
    _write_profiles(tmp_path, [qwen, openai])

    dialog = ApiConfigDialog(tk_root, tmp_path)
    try:
        assert dialog.current_id == "qwen-saved"
        dialog.key_var.set("must-not-cross-providers")
        dialog.preset_var.set(dialog._preset_names["openai-mini"])
        dialog._select_preset()
        assert dialog.current_id == "openai-saved"
        assert dialog.profile_var.get() == openai["name"]
        assert dialog.key_var.get() == ""

        dialog.key_var.set("another-unsaved-key")
        dialog.preset_var.set(dialog._preset_names["glm-vision-flash"])
        dialog._select_preset()
        assert dialog.current_id is None
        assert dialog.profile_var.get() == ""
        assert dialog.model_var.get() == PRESETS["glm-vision-flash"]["model"]
        assert dialog.key_var.get() == ""
        values, _ = dialog._form_values()
        assert "id" not in values
        assert values["preset_id"] == "glm-vision-flash"
    finally:
        dialog.destroy()


def test_endpoint_edit_stops_tagging_form_as_preset(tk_root, tmp_path):
    dialog = ApiConfigDialog(tk_root, tmp_path)
    try:
        dialog.base_url_var.set("https://example.test/v1")
        values, _ = dialog._form_values()
        assert "preset_id" not in values
    finally:
        dialog.destroy()


def test_failed_connection_after_save_keeps_new_profile_id(tk_root, tmp_path, monkeypatch):
    dialog = ApiConfigDialog(tk_root, tmp_path)
    saved = dict(dialog._form_values()[0], id="newly-saved-profile")
    monkeypatch.setattr(dialog_module, "save_profile", lambda *_args: saved)
    monkeypatch.setattr(dialog_module, "call_model", lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(dialog_module.messagebox, "showerror", lambda *_args, **_kwargs: None)
    try:
        dialog._busy = True
        dialog._test_worker(dialog._form_values()[0], "secret")
        dialog._poll_events()

        assert dialog.current_id == "newly-saved-profile"
        assert dialog._form_values()[0]["id"] == "newly-saved-profile"
        assert dialog.status_var.get() == "配置已保存，但连接测试失败。"
        assert dialog._is_busy() is False
    finally:
        dialog.destroy()


def test_restores_exact_custom_profile_and_unsaved_preset(tk_root, tmp_path):
    custom_one = {
        "id": "custom-one",
        "name": "自定义一",
        "base_url": "https://one.example/v1",
        "model": "vision-one",
        "timeout": 60,
    }
    custom_two = {
        "id": "custom-two",
        "name": "自定义二",
        "base_url": "https://two.example/v1",
        "model": "vision-two",
        "timeout": 90,
    }
    _write_profiles(tmp_path, [custom_one, custom_two])
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {"ai_api_dialog": {"preset_id": "custom", "profile_id": "custom-two", "advanced": True}}
        ),
        encoding="utf-8",
    )

    dialog = ApiConfigDialog(tk_root, tmp_path)
    try:
        assert dialog.current_id == "custom-two"
        assert dialog.name_var.get() == "自定义二"
        dialog.preset_var.set(dialog._preset_names["glm-vision-flash"])
        dialog._select_preset()
        assert dialog.current_id is None
    finally:
        dialog.destroy()

    reopened = ApiConfigDialog(tk_root, tmp_path)
    try:
        assert reopened.current_id is None
        assert reopened._preset_id() == "glm-vision-flash"
        assert reopened.model_var.get() == PRESETS["glm-vision-flash"]["model"]
    finally:
        reopened.destroy()


def test_connection_image_is_valid_rgb():
    import io
    from PIL import Image
    with Image.open(io.BytesIO(dialog_module._TEST_PNG)) as im:
        im.load()
        assert im.mode == 'RGB' and im.size == (512,512)
