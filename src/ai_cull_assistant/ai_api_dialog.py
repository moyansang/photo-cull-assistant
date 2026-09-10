"""Tkinter editor for OpenAI-compatible API profiles."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .ai_api import DEFAULT_TIMEOUT, call_model, load_profiles, save_profile


_TEST_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "/x8AAusB9Wl2nkwAAAAASUVORK5CYII="
)


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
        self.geometry("650x430")
        self.resizable(False, False)
        self.transient(parent)
        self.settings_dir = Path(settings_dir)
        self.on_saved = on_saved
        self.profiles = load_profiles(self.settings_dir)
        self.current_id: str | None = None
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._closed = False

        self.profile_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.base_url_var = tk.StringVar(value="https://api.openai.com/v1")
        self.model_var = tk.StringVar()
        self.timeout_var = tk.StringVar(value=str(DEFAULT_TIMEOUT))
        self.max_tokens_var = tk.StringVar()
        self.key_var = tk.StringVar()
        self.status_var = tk.StringVar(value="API 密钥只保存在 Windows 凭据管理器中。")

        self._build_ui()
        self._refresh_profile_choices()
        if self.profiles:
            self.profile_var.set(self.profiles[0]["name"])
            self._select_profile()
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
        ttk.Button(body, text="新建", command=self._new_profile).grid(row=0, column=2, padx=(8, 0), pady=5)

        fields = (
            ("配置名称", self.name_var),
            ("API 地址", self.base_url_var),
            ("模型名称", self.model_var),
            ("超时（秒）", self.timeout_var),
            ("最大输出 token", self.max_tokens_var),
        )
        for row, (label, variable) in enumerate(fields, start=1):
            ttk.Label(body, text=label, width=16).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(body, textvariable=variable).grid(row=row, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(body, text="API 密钥", width=16).grid(row=6, column=0, sticky="w", pady=5)
        ttk.Entry(body, textvariable=self.key_var, show="*").grid(row=6, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(body, text="留空会保留已保存的密钥；配置文件中不会写入密钥。", foreground="#666666").grid(
            row=7, column=1, columnspan=2, sticky="w"
        )
        ttk.Label(body, text="协议：带图片的 Chat Completions。可填接口前缀或完整 /chat/completions 地址。", foreground="#666666").grid(
            row=8, column=1, columnspan=2, sticky="w", pady=(2, 10)
        )

        ttk.Separator(body).grid(row=9, column=0, columnspan=3, sticky="ew", pady=(2, 10))
        ttk.Label(body, textvariable=self.status_var, wraplength=600).grid(row=10, column=0, columnspan=3, sticky="w")

        actions = ttk.Frame(body)
        actions.grid(row=11, column=0, columnspan=3, sticky="e", pady=(18, 0))
        self.test_button = ttk.Button(actions, text="测试连接", command=lambda: self._start("test"))
        self.test_button.pack(side="left", padx=5)
        self.save_button = ttk.Button(actions, text="保存", command=lambda: self._start("save"))
        self.save_button.pack(side="left", padx=5)
        ttk.Button(actions, text="关闭", command=self.destroy).pack(side="left", padx=(5, 0))

    def _refresh_profile_choices(self) -> None:
        self.profile_box.configure(values=[profile["name"] for profile in self.profiles])

    def _select_profile(self, _event=None) -> None:
        selected = next((item for item in self.profiles if item["name"] == self.profile_var.get()), None)
        if selected is None:
            return
        self.current_id = selected["id"]
        self.name_var.set(selected["name"])
        self.base_url_var.set(selected["base_url"])
        self.model_var.set(selected["model"])
        self.timeout_var.set(str(selected["timeout"]))
        self.max_tokens_var.set(str(selected.get("max_tokens", "")))
        self.key_var.set("")
        self.status_var.set("已载入配置；密钥留空会继续使用 Windows 中保存的值。")

    def _new_profile(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self.current_id = None
        self.profile_var.set("")
        self.name_var.set("")
        self.base_url_var.set("https://api.openai.com/v1")
        self.model_var.set("")
        self.timeout_var.set(str(DEFAULT_TIMEOUT))
        self.max_tokens_var.set("")
        self.key_var.set("")
        self.status_var.set("正在新建配置。")

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
        entered_key = self.key_var.get().strip()
        return profile, entered_key or None

    def _start(self, operation: str) -> None:
        if self._worker and self._worker.is_alive():
            return
        profile, key = self._form_values()
        self.save_button.configure(state="disabled")
        self.test_button.configure(state="disabled")
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
        try:
            saved = save_profile(self.settings_dir, profile, key)
            descriptor, filename = tempfile.mkstemp(prefix="photo-cull-api-test-", suffix=".png")
            os.close(descriptor)
            test_path = Path(filename)
            test_path.write_bytes(_TEST_PNG)
            result = call_model(saved, "这是连接测试。请简短确认你能看到一张图片。", [test_path])
        except Exception as exc:
            self._events.put(("error", str(exc)))
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
        self.save_button.configure(state="normal")
        self.test_button.configure(state="normal")
        if event == "error":
            self.status_var.set("操作失败。")
            messagebox.showerror("API 配置", str(value), parent=self)
        elif event == "saved":
            self._saved(value)  # type: ignore[arg-type]
            self.status_var.set("配置已保存。")
        else:
            saved, result = value  # type: ignore[misc]
            self._saved(saved)
            text = str(result.get("text", "")).strip()
            self.status_var.set("连接测试成功。")
            messagebox.showinfo("连接成功", text[:500] or "API 已返回响应。", parent=self)
        self.after(100, self._poll_events)

    def _saved(self, profile: dict) -> None:
        self.profiles = load_profiles(self.settings_dir)
        self.current_id = profile["id"]
        self.profile_var.set(profile["name"])
        self.key_var.set("")
        self._refresh_profile_choices()
        if self.on_saved is not None:
            self.on_saved()

    def destroy(self) -> None:
        self._closed = True
        super().destroy()
