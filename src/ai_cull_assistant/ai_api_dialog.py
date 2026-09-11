"""Tkinter editor for OpenAI-compatible API profiles."""
from __future__ import annotations

import io
from PIL import Image, ImageDraw
import os
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .ai_api import DEFAULT_TIMEOUT, call_model, load_profiles, save_profile
from .ai_presets import PRESETS, matching_preset, preset_profile
from .settings import read_values, save_values


def _test_image():
    image = Image.new("RGB", (512, 512), "white")
    ImageDraw.Draw(image).rectangle((128, 128, 384, 384), fill="blue")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()

_TEST_PNG = _test_image()
_CUSTOM_PRESET_ID = "custom"
_CUSTOM_PRESET_NAME = "自定义（OpenAI 兼容接口）"
_DEFAULT_PRESET_ID = "qwen-vl-plus"
_DIALOG_SETTINGS_KEY = "ai_api_dialog"


class ApiConfigDialog(tk.Toplevel):
    """Manage named API profiles and test one request without blocking Tk."""

    def __init__(
        self,
        parent: tk.Misc,
        settings_dir: Path,
        on_saved: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.title("AI API 配置")
        self.geometry("680x420")
        self.resizable(False, False)
        self.transient(parent)
        self.settings_dir = Path(settings_dir)
        self.on_saved = on_saved
        self.profiles = load_profiles(self.settings_dir)
        self.current_id: str | None = None
        self._active_preset_id: str | None = None
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._busy = False
        self._closed = False
        self._close_pending = False
        self._mutable_widgets: list[tuple[tk.Widget, str]] = []

        ui_settings = read_values(self.settings_dir).get(_DIALOG_SETTINGS_KEY, {})
        if not isinstance(ui_settings, dict):
            ui_settings = {}
        has_remembered_selection = "preset_id" in ui_settings or "profile_id" in ui_settings
        remembered_preset = str(ui_settings.get("preset_id", ""))
        if remembered_preset not in PRESETS and remembered_preset != _CUSTOM_PRESET_ID:
            remembered_preset = _DEFAULT_PRESET_ID

        self.profile_var = tk.StringVar()
        self.preset_var = tk.StringVar()
        self.advanced_var = tk.BooleanVar(value=bool(ui_settings.get("advanced", False)))
        self.name_var = tk.StringVar()
        self.base_url_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.timeout_var = tk.StringVar(value=str(DEFAULT_TIMEOUT))
        self.max_tokens_var = tk.StringVar()
        self.key_var = tk.StringVar()
        self.note_var = tk.StringVar()
        self.status_var = tk.StringVar(value="API 密钥只保存在 Windows 凭据管理器中。")

        self._preset_names = {preset_id: str(value["name"]) for preset_id, value in PRESETS.items()}
        self._preset_names[_CUSTOM_PRESET_ID] = _CUSTOM_PRESET_NAME
        self._preset_ids_by_name = {name: preset_id for preset_id, name in self._preset_names.items()}

        self._build_ui()
        self._refresh_profile_choices()
        selected = (
            self._initial_profile(str(ui_settings.get("profile_id", "")), remembered_preset)
            if has_remembered_selection
            else self.profiles[0] if self.profiles else None
        )
        if selected is not None:
            self.profile_var.set(selected["name"])
            self._load_profile(selected)
        else:
            self._apply_new_preset(remembered_preset)
        self._sync_advanced()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(100, self._poll_events)
        self.grab_set()

    def _build_ui(self) -> None:
        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="已保存配置", width=16).grid(row=0, column=0, sticky="w", pady=5)
        self.profile_box = ttk.Combobox(body, textvariable=self.profile_var, state="readonly", width=42)
        self.profile_box.grid(row=0, column=1, sticky="ew", pady=5)
        self.profile_box.bind("<<ComboboxSelected>>", self._select_profile)
        self.new_button = ttk.Button(body, text="新建", command=self._new_profile)
        self.new_button.grid(row=0, column=2, padx=(8, 0), pady=5)

        ttk.Label(body, text="服务预设", width=16).grid(row=1, column=0, sticky="w", pady=5)
        self.preset_box = ttk.Combobox(
            body,
            textvariable=self.preset_var,
            values=list(self._preset_names.values()),
            state="readonly",
            width=42,
        )
        self.preset_box.grid(row=1, column=1, columnspan=2, sticky="ew", pady=5)
        self.preset_box.bind("<<ComboboxSelected>>", self._select_preset)

        ttk.Label(body, text="API 密钥", width=16).grid(row=2, column=0, sticky="w", pady=5)
        self.key_entry = ttk.Entry(body, textvariable=self.key_var, show="*")
        self.key_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(body, text="留空会保留当前配置的已保存密钥；配置文件中不会写入密钥。", foreground="#666666").grid(
            row=3, column=1, columnspan=2, sticky="w"
        )
        ttk.Label(body, textvariable=self.note_var, foreground="#666666", wraplength=570).grid(
            row=4, column=1, columnspan=2, sticky="w", pady=(4, 6)
        )

        self.advanced_button = ttk.Checkbutton(
            body,
            text="高级设置",
            variable=self.advanced_var,
            command=self._toggle_advanced,
        )
        self.advanced_button.grid(row=5, column=0, columnspan=3, sticky="w", pady=(2, 4))

        self.advanced_frame = ttk.Frame(body)
        self.advanced_frame.grid(row=6, column=0, columnspan=3, sticky="ew")
        self.advanced_frame.columnconfigure(1, weight=1)
        fields = (
            ("配置名称", self.name_var),
            ("API 地址", self.base_url_var),
            ("模型名称", self.model_var),
            ("超时（秒）", self.timeout_var),
            ("最大输出 token", self.max_tokens_var),
        )
        self.advanced_entries: list[ttk.Entry] = []
        for row, (label, variable) in enumerate(fields):
            ttk.Label(self.advanced_frame, text=label, width=16).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(self.advanced_frame, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            self.advanced_entries.append(entry)
        ttk.Label(
            self.advanced_frame,
            text="协议：带图片的 Chat Completions。API 地址可填接口前缀或完整端点。",
            foreground="#666666",
        ).grid(row=len(fields), column=1, sticky="w", pady=(2, 4))

        ttk.Separator(body).grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 10))
        ttk.Label(body, textvariable=self.status_var, wraplength=630).grid(row=8, column=0, columnspan=3, sticky="w")

        actions = ttk.Frame(body)
        actions.grid(row=9, column=0, columnspan=3, sticky="e", pady=(16, 0))
        self.test_button = ttk.Button(actions, text="测试连接", command=lambda: self._start("test"))
        self.test_button.pack(side="left", padx=5)
        self.save_button = ttk.Button(actions, text="保存", command=lambda: self._start("save"))
        self.save_button.pack(side="left", padx=5)
        self.close_button = ttk.Button(actions, text="关闭", command=self.destroy)
        self.close_button.pack(side="left", padx=(5, 0))

        self._mutable_widgets = [
            (self.profile_box, "readonly"),
            (self.new_button, "normal"),
            (self.preset_box, "readonly"),
            (self.key_entry, "normal"),
            (self.advanced_button, "normal"),
            *((entry, "normal") for entry in self.advanced_entries),
        ]

    def _initial_profile(self, remembered_profile_id: str, remembered_preset: str) -> dict | None:
        for profile in self.profiles:
            if profile["id"] == remembered_profile_id:
                return profile
        for profile in self.profiles:
            preset = matching_preset(profile)
            if preset is not None and profile.get("preset_id") == remembered_preset:
                return profile
        return None

    def _preset_id(self) -> str:
        return self._preset_ids_by_name.get(self.preset_var.get(), _CUSTOM_PRESET_ID)

    def _refresh_profile_choices(self) -> None:
        self.profile_box.configure(values=[profile["name"] for profile in self.profiles])

    def _set_fields(self, profile: dict) -> None:
        self.name_var.set(profile.get("name", ""))
        self.base_url_var.set(profile.get("base_url", ""))
        self.model_var.set(profile.get("model", ""))
        self.timeout_var.set(str(profile.get("timeout", DEFAULT_TIMEOUT)))
        self.max_tokens_var.set(str(profile.get("max_tokens", "")))

    def _load_profile(self, profile: dict) -> None:
        self.current_id = profile["id"]
        self._set_fields(profile)
        preset_id = str(profile.get("preset_id", "")) if matching_preset(profile) is not None else _CUSTOM_PRESET_ID
        self._active_preset_id = preset_id
        self.preset_var.set(self._preset_names[preset_id])
        self.note_var.set(self._preset_note(preset_id))
        self.key_var.set("")
        if preset_id == _CUSTOM_PRESET_ID:
            self.advanced_var.set(True)
        self._sync_advanced()
        self._remember_ui_state()

    def _select_profile(self, _event=None) -> None:
        if self._is_busy():
            return
        selected = next((item for item in self.profiles if item["name"] == self.profile_var.get()), None)
        if selected is None:
            return
        self._load_profile(selected)
        self.status_var.set("已载入配置；密钥留空会继续使用 Windows 中保存的值。")

    def _apply_new_preset(self, preset_id: str) -> None:
        if preset_id not in PRESETS and preset_id != _CUSTOM_PRESET_ID:
            preset_id = _DEFAULT_PRESET_ID
        self.current_id = None
        self.profile_var.set("")
        self._active_preset_id = preset_id
        self.preset_var.set(self._preset_names[preset_id])
        if preset_id == _CUSTOM_PRESET_ID:
            self._set_fields({"timeout": DEFAULT_TIMEOUT})
            self.advanced_var.set(True)
        else:
            self._set_fields(preset_profile(preset_id))
        self.note_var.set(self._preset_note(preset_id))
        self.key_var.set("")

    def _new_profile(self) -> None:
        if self._is_busy():
            return
        self._apply_new_preset(self._preset_id())
        self._sync_advanced()
        self._remember_ui_state()
        self.status_var.set("正在新建配置。")

    def _select_preset(self, _event=None) -> None:
        if self._is_busy():
            return
        preset_id = self._preset_id()
        if preset_id == self._active_preset_id:
            return
        self.key_var.set("")
        if preset_id == _CUSTOM_PRESET_ID:
            self._apply_new_preset(preset_id)
        else:
            saved = next(
                (
                    profile
                    for profile in self.profiles
                    if profile.get("preset_id") == preset_id and matching_preset(profile) is not None
                ),
                None,
            )
            if saved is None:
                self._apply_new_preset(preset_id)
            else:
                self.profile_var.set(saved["name"])
                self._load_profile(saved)
        self.note_var.set(self._preset_note(preset_id))
        self._sync_advanced()
        self._remember_ui_state()
        self.status_var.set("已切换服务预设；请填写该服务的 API 密钥。")

    def _preset_note(self, preset_id: str) -> str:
        if preset_id == _CUSTOM_PRESET_ID:
            return "自定义模式需要填写 API 地址和模型名称。"
        return str(PRESETS[preset_id].get("note", ""))

    def _toggle_advanced(self) -> None:
        if self._preset_id() == _CUSTOM_PRESET_ID:
            self.advanced_var.set(True)
        self._sync_advanced()
        self._remember_ui_state()

    def _sync_advanced(self) -> None:
        visible = self.advanced_var.get() or self._preset_id() == _CUSTOM_PRESET_ID
        if visible:
            self.advanced_frame.grid()
            self.geometry("680x590")
        else:
            self.advanced_frame.grid_remove()
            self.geometry("680x420")

    def _remember_ui_state(self) -> None:
        try:
            save_values(
                self.settings_dir,
                {
                    _DIALOG_SETTINGS_KEY: {
                        "preset_id": self._preset_id(),
                        "profile_id": self.current_id or "",
                        "advanced": self.advanced_var.get(),
                    }
                },
            )
        except OSError:
            pass

    def _form_values(self) -> tuple[dict[str, object], str | None]:
        profile: dict[str, object] = {
            "name": self.name_var.get(),
            "base_url": self.base_url_var.get(),
            "model": self.model_var.get(),
            "timeout": self.timeout_var.get(),
        }
        if self.current_id:
            profile["id"] = self.current_id
        if self.max_tokens_var.get().strip():
            profile["max_tokens"] = self.max_tokens_var.get()
        preset_id = self._preset_id()
        if preset_id in PRESETS:
            candidate = dict(profile, preset_id=preset_id)
            if matching_preset(candidate) is not None:
                profile["preset_id"] = preset_id
        entered_key = self.key_var.get().strip()
        return profile, entered_key or None

    def _is_busy(self) -> bool:
        return self._busy

    def _set_busy(self, busy: bool) -> None:
        for widget, idle_state in self._mutable_widgets:
            widget.configure(state="disabled" if busy else idle_state)
        self.save_button.configure(state="disabled" if busy else "normal")
        self.test_button.configure(state="disabled" if busy else "normal")

    def _start(self, operation: str) -> None:
        if self._is_busy():
            return
        profile, key = self._form_values()
        self._busy = True
        self._set_busy(True)
        self.status_var.set("正在保存…" if operation == "save" else "正在发送一张 1×1 测试图片…")
        target = self._save_worker if operation == "save" else self._test_worker
        self._worker = threading.Thread(target=target, args=(profile, key), daemon=True)
        self._worker.start()

    def _save_worker(self, profile: dict[str, object], key: str | None) -> None:
        try:
            saved = save_profile(self.settings_dir, profile, key)
        except Exception as exc:
            self._events.put(("error", str(exc)))
        else:
            self._events.put(("saved", saved))

    def _test_worker(self, profile: dict[str, object], key: str | None) -> None:
        test_path: Path | None = None
        saved: dict | None = None
        try:
            saved = save_profile(self.settings_dir, profile, key)
            descriptor, filename = tempfile.mkstemp(prefix="photo-cull-api-test-", suffix=".png")
            os.close(descriptor)
            test_path = Path(filename)
            test_path.write_bytes(_TEST_PNG)
            result = call_model(saved, "这是连接测试。请简短确认你能看到一张图片。", [test_path])
        except Exception as exc:
            if saved is None:
                self._events.put(("error", str(exc)))
            else:
                self._events.put(("test_error", (saved, str(exc))))
        else:
            self._events.put(("tested", (saved, result)))
        finally:
            if test_path is not None:
                try:
                    test_path.unlink()
                except OSError:
                    pass

    def _poll_events(self) -> None:
        if self._closed:
            return
        try:
            event, value = self._events.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_events)
            return
        self._worker = None
        self._busy = False
        self._set_busy(False)
        if event == "error":
            self.status_var.set("操作失败。")
            messagebox.showerror("API 配置", str(value), parent=self)
        elif event == "test_error":
            saved, error = value  # type: ignore[misc]
            self._saved(saved)
            self.status_var.set("配置已保存，但连接测试失败。")
            messagebox.showerror("API 配置", str(error), parent=self)
        elif event == "saved":
            self._saved(value)  # type: ignore[arg-type]
            self.status_var.set("配置已保存。")
        else:
            saved, result = value  # type: ignore[misc]
            self._saved(saved)
            text = str(result.get("text", "")).strip()
            self.status_var.set("连接测试成功。")
            messagebox.showinfo("连接成功", text[:500] or "API 已返回响应。", parent=self)
        if self._close_pending:
            self.destroy()
            return
        self.after(100, self._poll_events)

    def _saved(self, profile: dict) -> None:
        self.profiles = load_profiles(self.settings_dir)
        selected = next((item for item in self.profiles if item["id"] == profile["id"]), profile)
        self.profile_var.set(selected["name"])
        self._refresh_profile_choices()
        self._load_profile(selected)
        if self.on_saved is not None:
            self.on_saved()

    def destroy(self) -> None:
        if self._is_busy():
            self._close_pending = True
            self.status_var.set("操作正在进行，完成后将自动关闭窗口。")
            return
        if self._closed:
            return
        self._remember_ui_state()
        self._closed = True
        super().destroy()
