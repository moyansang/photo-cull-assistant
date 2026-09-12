from __future__ import annotations

import threading
import queue
import copy
import json
import zipfile
from pathlib import Path
from types import MappingProxyType
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .group_editor import GroupEditor
from .version import VERSION
from .update_ui import UpdateController
import sys
from .settings import application_dir, load_paths, read_values, save_values
from .project_storage import (
    load_workspace_preferences,
    prepare_runtime_settings,
    save_workspace_preferences,
    workspace_for,
)
from .crop_settings import CropSettings
from .crop_dialog import CropDialog
from .shared_api import (
    NO_API_LABEL,
    NO_API_PROFILE_ID,
    profile_options,
    save_selected_profile_id,
    selected_api_profile,
    selected_profile_id,
)
from .window_layout import fit_window, scrollable_body
from .workspace_layout import workspace_path
from dataclasses import asdict
from .workflow import (
    ScanResult,
    persist_manual_groups,
)


GROUPING_LABELS = {"严格": "strict", "标准": "standard", "宽松": "loose"}


class App(tk.Tk):
    def __init__(self, settings_dir: Path | None = None) -> None:
        super().__init__()
        self.title(f"AI 选片助手 v{VERSION}")
        self.review_project = None
        self.scan_result: ScanResult | None = None
        self.settings_dir = Path(settings_dir) if settings_dir is not None else prepare_runtime_settings()
        self.saved_paths = load_paths(self.settings_dir)
        global_values = read_values(self.settings_dir)
        global_options = global_values.get("options", {})
        if not isinstance(global_options, dict):
            global_options = {}
        input_dir = self.saved_paths["input"].strip()
        workspace = self.saved_paths["workspace"].strip()
        workspace_values = {}
        self._workspace_blocked = False
        self._workspace_error = ""
        if input_dir:
            try:
                selected = workspace_for(
                    self.settings_dir,
                    input_dir,
                    preferred=workspace or None,
                )
                workspace = str(selected)
                self.saved_paths["workspace"] = workspace
                workspace_values = load_workspace_preferences(self.settings_dir, selected, input_dir)
            except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                self._workspace_blocked = True
                self._workspace_error = str(exc)
        self.crop_settings = CropSettings.from_dict(workspace_values.get("face_crop", {}))
        self.saved_options = workspace_values.get("options", {})
        if not isinstance(self.saved_options, dict):
            self.saved_options = {}
        self.saved_options = dict(self.saved_options)
        self.saved_options["no_auto_updates"] = global_options.get("no_auto_updates") is True
        self._active_input = input_dir
        self._active_workspace = workspace
        self._workspace_user_custom = False
        self._workspace_cleared = False
        self._suppress_settings_trace = False
        self._loaded_grouping = GROUPING_LABELS.get(self.saved_options.get("grouping"), "standard")
        self._processing_busy = False
        self._processing_job = None
        self._processing_mode = "scan"
        self._api_config_window = None
        self._scan_progress = 0
        self._update_progress_active = False
        self._stop_event = threading.Event()
        self._process_events = queue.Queue()
        self._log_lock = threading.Lock()
        self._log_epoch = 0
        self._close_after_stop = False
        self._settings_pending = None
        self._build_ui()
        self._api_selection_changed()
        fit_window(self, (980, 780), minimum_size=(640, 520))
        if not self._workspace_blocked:
            self._restore_session()
        self.input_var.trace_add("write", self._input_path_changed)
        self.workspace_var.trace_add("write", self._workspace_path_changed)
        for variable in (self.preset_var, self.per_page_var, self.columns_var, self.screening_var):
            variable.trace_add("write", self._workspace_setting_changed)
        self.no_updates_var.trace_add("write", self._schedule_settings_save)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.updates = UpdateController(self)
        self._restore_processing_job()
        self.after(100, self._poll_processing)
        if self._workspace_blocked:
            self.after(50, self._show_workspace_error)
        if settings_dir is None and getattr(sys, 'frozen', False) and '--self-test' not in sys.argv and not self.no_updates_var.get():
            self.after(1500, lambda: self.updates.check() if not self.no_updates_var.get() and not self._processing_busy else None)

    def _save_session(self, fresh=False):
        if self.scan_result and not self._workspace_blocked:
            try:
                from .session_store import save_session
                save_session(self.scan_result, fresh=fresh)
            except Exception as exc:
                self._log(f"分析记录保存失败：{exc}")

    def _restore_session(self):
        if self._workspace_blocked or not self.input_var.get().strip() or not self.workspace_var.get().strip():
            return
        workspace = Path(self.workspace_var.get())
        logfile = workspace_path(workspace, 'session.log')
        if logfile.exists():
            try:
                self.log_text.configure(state="normal")
                self.log_text.insert("end", logfile.read_text('utf-8'))
                self.log_text.configure(state="disabled")
            except OSError:
                pass
        try:
            from .session_store import load_session
            self.scan_result = load_session(workspace, self.input_var.get())
            if self.scan_result:
                self._log(f"已恢复上次分析：{len(self.scan_result.assets)} 张照片，请按推荐下一步继续。新增照片请重新扫描。")
        except Exception as exc:
            self._log(f"上次分析未恢复：{exc}。请检查照片路径或重新扫描；已有 AI 任务仍保留。")

    def _option_int(self, key, default, minimum, maximum):
        try:
            return max(minimum, min(maximum, int(self.saved_options.get(key, default))))
        except (ValueError, TypeError):
            return default

    def _schedule_settings_save(self, *_):
        if self._suppress_settings_trace or self._processing_busy:
            return
        if self._settings_pending:
            self.after_cancel(self._settings_pending)
        self._settings_pending = self.after(500, self._save_preferences)

    def _workspace_setting_changed(self, *_):
        if not self._suppress_settings_trace:
            self._workspace_cleared = False
        self._schedule_settings_save()

    def _input_path_changed(self, *_):
        self._schedule_settings_save()

    def _workspace_path_changed(self, *_):
        if not self._suppress_settings_trace:
            self._workspace_user_custom = True
        self._schedule_settings_save()

    def _workspace_options(self):
        options = dict(self.saved_options)
        options.pop("no_auto_updates", None)
        options['grouping'] = self.preset_var.get()
        options['screening'] = self.screening_var.get()
        for key, variable, low, high in [('per_page', self.per_page_var, 8, 60), ('columns', self.columns_var, 2, 6)]:
            try:
                value = variable.get()
                if low <= value <= high:
                    options[key] = value
            except tk.TclError:
                pass
        return options

    def _save_active_workspace_preferences(self):
        if (self._workspace_blocked or self._workspace_cleared
                or not self._active_input or not self._active_workspace):
            return
        save_workspace_preferences(
            Path(self._active_workspace),
            asdict(self.crop_settings),
            self._workspace_options(),
        )

    def _activate_workspace(self, input_dir: str, workspace: Path) -> None:
        previous_workspace = self._active_workspace
        previous_blocked = self._workspace_blocked
        self._save_active_workspace_preferences()
        values = load_workspace_preferences(self.settings_dir, workspace, input_dir)
        options = values.get("options", {})
        if not isinstance(options, dict):
            options = {}
        self._active_input = input_dir
        self._active_workspace = str(workspace)
        self._workspace_blocked = False
        self._workspace_error = ""
        self._workspace_cleared = False
        self.crop_settings = CropSettings.from_dict(values.get("face_crop", {}))
        self.saved_options = dict(options)
        self.saved_options["no_auto_updates"] = self.no_updates_var.get()
        self._loaded_grouping = GROUPING_LABELS.get(self.saved_options.get("grouping"), "standard")
        self._suppress_settings_trace = True
        try:
            self.workspace_var.set(str(workspace))
            grouping = self.saved_options.get("grouping")
            self.preset_var.set(grouping if grouping in GROUPING_LABELS else "标准")
            self.per_page_var.set(self._option_int("per_page", 16, 8, 60))
            self.columns_var.set(self._option_int("columns", 4, 2, 6))
            screening = self.saved_options.get("screening", True)
            self.screening_var.set(screening if isinstance(screening, bool) else True)
        finally:
            self._suppress_settings_trace = False
        self.scan_result = None
        self.review_project = None
        self._processing_job = None
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._restore_session()
        self._restore_processing_job()
        if (previous_workspace and not previous_blocked
                and Path(previous_workspace).resolve() != workspace.resolve()):
            self._compact_completed_workspace(previous_workspace)

    def _sync_selected_workspace(self) -> bool:
        if self._processing_busy:
            return False
        input_dir = self.input_var.get().strip()
        requested_workspace = self.workspace_var.get().strip()
        if not input_dir:
            if self._active_input:
                self._save_active_workspace_preferences()
            self._active_input = ""
            self._active_workspace = ""
            self.scan_result = None
            self.review_project = None
            self._processing_job = None
            self._suppress_settings_trace = True
            try:
                self.workspace_var.set("")
            finally:
                self._suppress_settings_trace = False
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
            self._restore_processing_job()
            self._workspace_user_custom = False
            return True
        changed_input = input_dir != self._active_input
        changed_workspace = requested_workspace != self._active_workspace
        if not changed_input and not changed_workspace:
            self._workspace_user_custom = False
            return not self._workspace_blocked
        if changed_input and not Path(input_dir).is_dir():
            return False
        preferred = requested_workspace if self._workspace_user_custom and requested_workspace else None
        try:
            selected = workspace_for(self.settings_dir, input_dir, preferred=preferred)
            self._activate_workspace(input_dir, selected)
        except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            self._suppress_settings_trace = True
            try:
                self.input_var.set(self._active_input)
                self.workspace_var.set(self._active_workspace)
            finally:
                self._suppress_settings_trace = False
            self._workspace_user_custom = False
            self._log(f"工作区切换失败：{exc}")
            return False
        self._workspace_user_custom = False
        return True

    def _save_preferences(self):
        self._settings_pending = None
        try:
            if not self._sync_selected_workspace():
                return
            self._save_active_workspace_preferences()
            self.saved_options = self._workspace_options()
            self.saved_options["no_auto_updates"] = self.no_updates_var.get()
            save_values(
                self.settings_dir,
                dict(
                    input=self.input_var.get().strip(),
                    workspace=self.workspace_var.get().strip(),
                    options={"no_auto_updates": self.no_updates_var.get()},
                ),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._log(f"设置保存失败：{exc}")

    def _close(self) -> None:
        for child in self.winfo_children():
            if getattr(child, '_api_active', False):
                child._close()
                return
        if self._settings_pending:
            self.after_cancel(self._settings_pending)
        if self._processing_busy:
            self._close_after_stop = True
            self._stop_processing()
            return
        self._save_preferences()
        self._save_session()
        if self._active_workspace and not self._workspace_cleared and not self._workspace_blocked:
            self._compact_completed_workspace(self._active_workspace)
        for timer in self.tk.splitlist(self.tk.call('after', 'info')):
            self.after_cancel(timer)
        self.destroy()

    def _compact_completed_workspace(self, workspace):
        """Archive only finished exports; never rerun analysis to save space."""
        from .workspace_archive import compact_workspace
        old_label = self.progress_label.get()
        old_percent = self.progress_var.get()
        def progress(value):
            self._display_progress("整理工作区进度：", value)
            self.update_idletasks()
        try:
            compact_workspace(workspace, progress=progress)
        except (OSError, ValueError, RuntimeError) as exc:
            # Do not append to a possibly archived log after partial compaction.
            messagebox.showwarning("工作区整理未完成", f"已保留可恢复数据。\n{exc}", parent=self)
        finally:
            self._display_progress(old_label, old_percent)

    def _show_workspace_error(self):
        if not self._workspace_blocked or not self.winfo_exists():
            return
        messagebox.showerror(
            "工作区无法恢复",
            "当前工作区的数据没有被覆盖。请选择其他工作区，或确认不再需要其中内容后清空工作区。\n\n"
            + self._workspace_error,
            parent=self,
        )

    def _build_ui(self) -> None:
        frame = scrollable_body(self, padding=12)

        self.input_var = tk.StringVar(value=self.saved_paths["input"])
        self.workspace_var = tk.StringVar(value=self.saved_paths["workspace"])
        self.preset_var = tk.StringVar(value=self.saved_options.get("grouping") if self.saved_options.get("grouping") in GROUPING_LABELS else "标准")
        self.per_page_var = tk.IntVar(value=self._option_int("per_page", 16, 8, 60))
        self.columns_var = tk.IntVar(value=self._option_int("columns", 4, 2, 6))
        self.screening_var = tk.BooleanVar(value=self.saved_options.get("screening", True) if isinstance(self.saved_options.get("screening", True), bool) else True)

        self.no_updates_var = tk.BooleanVar(value=self.saved_options.get('no_auto_updates') is True)
        row = 0
        self._path_row(frame, row, "照片文件夹", self.input_var, self._choose_input)
        row += 1
        self._path_row(frame, row, "工作区", self.workspace_var, self._choose_workspace)
        row += 1

        ttk.Label(frame, text="分组灵敏度").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Combobox(frame, textvariable=self.preset_var, values=list(GROUPING_LABELS), state="readonly", width=12).grid(row=row, column=1, sticky="w")
        ttk.Label(frame, text="每页照片数").grid(row=row, column=2, sticky="e")
        ttk.Spinbox(frame, from_=8, to=60, textvariable=self.per_page_var, width=8).grid(row=row, column=3, sticky="w")
        ttk.Label(frame, text="列数").grid(row=row, column=4, sticky="e")
        ttk.Spinbox(frame, from_=2, to=6, textvariable=self.columns_var, width=8).grid(row=row, column=5, sticky="w")
        row += 1

        ttk.Checkbutton(
            frame,
            text="人物主体明显虚焦/严重抖动 → 建议弃置（导入 LR 后生效）",
            variable=self.screening_var,
        ).grid(row=row, column=0, columnspan=6, sticky="w", pady=(2, 8))
        row += 1

        ttk.Label(frame, text="AI 服务").grid(row=row, column=0, sticky="w", pady=6)
        self.api_profile_var = tk.StringVar(value=NO_API_LABEL)
        self.api_profile_combo = ttk.Combobox(
            frame,
            textvariable=self.api_profile_var,
            values=(NO_API_LABEL,),
            state="readonly",
            width=36,
        )
        self.api_profile_combo.grid(row=row, column=1, columnspan=3, sticky="ew", padx=(0, 8))
        self.api_profile_combo.bind("<<ComboboxSelected>>", self._api_selection_changed)
        ttk.Button(frame, text="配置 API", command=self._open_api_config).grid(
            row=row, column=4, columnspan=2, sticky="w"
        )
        self._api_profile_ids = {}
        self._refresh_api_profiles()
        row += 1

        first = ttk.Frame(frame)
        first.grid(row=row, column=0, columnspan=6, sticky="w", pady=(10, 4))
        self.scan_button = ttk.Button(first, text="扫描图片", command=self._run_scan_thread)
        self.scan_button.pack(side="left", padx=(0, 8))
        self.group_button = ttk.Button(first, text="编辑选片组", command=self._open_group_editor)
        self.group_button.pack(side="left", padx=(0, 8))
        self.crop_button = ttk.Button(first, text="检测/调整人脸框", command=self._open_crop_settings)
        self.crop_button.pack(side="left")
        row += 1
        second = ttk.Frame(frame)
        second.grid(row=row, column=0, columnspan=6, sticky="w", pady=4)
        self.focus_button = ttk.Button(second, text="AI 复核", command=self._run_focus_review)
        self.focus_button.pack(side="left", padx=(0, 8))
        self.sheets_button = ttk.Button(second, text="生成联系表", command=self._generate_contact_sheets)
        self.sheets_button.pack(side="left", padx=(0, 8))
        self.review_button = ttk.Button(second, text="AI 选片与导出", command=self._open_ai_review)
        self.review_button.pack(side="left")
        row += 1
        controls = ttk.Frame(frame)
        controls.grid(row=row, column=0, columnspan=6, sticky="w", pady=4)
        self.stop_button = ttk.Button(controls, text="停止处理", command=self._stop_processing, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))
        self.continue_button = ttk.Button(controls, text="继续处理", command=self._continue_processing, state="disabled")
        self.continue_button.pack(side="left", padx=(0, 8))
        self.contact_button = ttk.Button(controls, text="打开联系表目录", command=self._open_contact_dir)
        self.contact_button.pack(side="left", padx=(0, 8))
        self.clear_log_button = ttk.Button(controls, text="清空工作区", command=self._clear_workspace)
        self.clear_log_button.pack(side="left")
        row += 1
        lr_frame = ttk.Frame(frame)
        lr_frame.grid(row=row, column=0, columnspan=6, sticky="w", pady=(0, 10))
        ttk.Button(lr_frame, text="LR 插件", command=self._open_lr_plugin).pack(side="left")
        self.update_button = ttk.Button(lr_frame, text="检查更新", command=lambda: self.updates.check(True))
        self.update_button.pack(side="left", padx=12)
        ttk.Checkbutton(lr_frame, text="不再自动检查更新", variable=self.no_updates_var, command=self._save_preferences).pack(side="left")
        row += 1
        self.next_step_var = tk.StringVar(value="推荐下一步：扫描图片")
        ttk.Label(frame, textvariable=self.next_step_var).grid(row=row, column=0, columnspan=6, sticky="w", pady=(4, 6))
        row += 1
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_label = tk.StringVar(value="扫描图片进度：")
        self.progress_text = tk.StringVar(value="0%")
        ttk.Label(frame, textvariable=self.progress_label).grid(row=row, column=0, sticky="w")
        ttk.Progressbar(frame, variable=self.progress_var, maximum=100).grid(row=row, column=1, columnspan=4, sticky="ew", padx=(0, 8))
        ttk.Label(frame, textvariable=self.progress_text).grid(row=row, column=5, sticky="e")
        row += 1
        ttk.Label(frame, text="日志：").grid(row=row, column=0, columnspan=6, sticky="w", pady=(4, 0))
        row += 1
        self.log_text = tk.Text(frame, height=12, wrap="word", state="disabled")
        self.log_text.grid(row=row, column=0, columnspan=6, sticky="nsew")

        for col in range(6):
            frame.columnconfigure(col, weight=1)
        frame.rowconfigure(row, weight=1)

    def _path_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, columnspan=4, sticky="ew", padx=(0, 8))
        ttk.Button(parent, text="选择", command=command).grid(row=row, column=5, sticky="e")

    def _choose_input(self) -> None:
        path = filedialog.askdirectory(title="选择照片文件夹")
        if path:
            self.input_var.set(path)

    def _choose_workspace(self) -> None:
        path = filedialog.askdirectory(title="选择工作区")
        if path:
            self.workspace_var.set(path)

    def _processing_options(self):
        return dict(grouping_preset=GROUPING_LABELS[self.preset_var.get()], photos_per_page=self.per_page_var.get(), columns=self.columns_var.get(), technical_screening=self.screening_var.get())

    def _refresh_api_profiles(self, preferred_id=None) -> None:
        current_id = self._api_profile_ids.get(self.api_profile_var.get()) if hasattr(self, "_api_profile_ids") else None
        chosen_id = preferred_id or current_id or selected_profile_id(self.settings_dir)
        choices = profile_options(self.settings_dir)
        self._api_profile_ids = {label: profile_id for label, profile_id in choices}
        labels = [label for label, _profile_id in choices]
        selected_label = next((label for label, profile_id in choices if profile_id == chosen_id), NO_API_LABEL)
        self.api_profile_combo.configure(values=labels)
        self.api_profile_var.set(selected_label)

    def _api_selection_changed(self, _event=None) -> None:
        profile_id = self._api_profile_ids.get(self.api_profile_var.get(), NO_API_PROFILE_ID)
        try:
            save_selected_profile_id(self.settings_dir, profile_id)
        except OSError as exc:
            self._log(f"API 选择保存失败：{exc}")

    def _api_profiles_saved(self) -> None:
        self._refresh_api_profiles(selected_profile_id(self.settings_dir))
        self._api_selection_changed()

    def _selected_api_profile(self) -> dict | None:
        profile_id = self._api_profile_ids.get(self.api_profile_var.get(), NO_API_PROFILE_ID)
        return selected_api_profile(self.settings_dir, profile_id)

    def _open_api_config(self) -> None:
        if self._processing_busy or self.updates.busy:
            messagebox.showinfo("提示", "请等待当前处理结束。", parent=self)
            return
        try:
            existing = self._api_config_window
            if existing is not None and existing.winfo_exists():
                existing.reveal()
                return
            from .ai_api_dialog import ApiConfigDialog
            self._api_config_window = ApiConfigDialog(
                self, self.settings_dir, on_saved=self._api_profiles_saved
            )
        except Exception as exc:
            messagebox.showerror("配置 API", str(exc), parent=self)

    def _restore_processing_job(self):
        if self._workspace_blocked or not self.input_var.get().strip() or not self.workspace_var.get().strip():
            self._processing_job = None
            self.continue_button.configure(state="disabled")
            self.next_step_var.set(
                "推荐下一步：清空工作区或选择其他工作区"
                if self._workspace_blocked else "推荐下一步：扫描图片"
            )
            return
        try:
            from .processing_job import load_job
            self._processing_job = load_job(self.workspace_var.get(), self.input_var.get())
        except Exception as exc:
            self._processing_job = None
            self._log(str(exc))
        self.continue_button.configure(state="normal" if self._processing_job else "disabled")
        if self._processing_job:
            self._processing_mode = self._job_mode(self._processing_job)
            self._set_progress(self._processing_job.percent)
        self.next_step_var.set("推荐下一步：" + (
            "继续处理" if self._processing_job else self._next_step_after_restore()
        ))

    @staticmethod
    def _job_mode(job) -> str:
        mode = getattr(job, "mode", None) or getattr(job, "kind", None) or "scan"
        return "rescan" if mode == "regenerate" else str(mode)

    def _stage_path(self) -> Path | None:
        if self._workspace_blocked:
            return None
        value = self.workspace_var.get().strip()
        return Path(value) / "workflow-stage.json" if value else None

    def _set_sheets_ready(self, ready: bool) -> None:
        path = self._stage_path()
        if path is None:
            return
        try:
            from .ai_project import atomic_json
            atomic_json(path, {"version": 1, "contact_sheets_ready": bool(ready)})
        except OSError as exc:
            self._log(f"流程状态保存失败：{exc}")

    def _sheets_ready(self) -> bool:
        if not self.scan_result:
            return False
        path = self._stage_path()
        if path and path.is_file():
            try:
                return json.loads(path.read_text("utf-8")).get("contact_sheets_ready") is True
            except (OSError, ValueError, TypeError):
                return False
        # Workspaces created by older versions have no stage file.  Their saved
        # pages remain usable until a staged operation marks them stale.
        return bool(self.scan_result.main_pages or self.scan_result.rejected_pages)

    def _next_step_after_restore(self) -> str:
        if not self.scan_result:
            return "扫描图片"
        return "AI 选片与导出" if self._sheets_ready() else "检测/调整人脸框"

    def _mode_label(self, mode: str | None = None) -> str:
        return {
            "scan": "扫描图片",
            "rescan": "重新扫描修改过的图片",
            "focus": "AI 复核",
            "sheets": "生成联系表",
        }.get(mode or self._processing_mode, "处理")

    def _set_progress(self, value):
        value = max(0, min(100, int(value)))
        self._scan_progress = value
        if self._update_progress_active:
            return
        self._display_progress(f"{self._mode_label()}进度：", value)

    def _display_progress(self, label, value):
        self.progress_var.set(value)
        self.progress_label.set(label)
        self.progress_text.set(f"{value}%")

    def _show_update_progress(self, value):
        self._update_progress_active = True
        self._display_progress("更新进度：", max(0, min(100, int(value))))

    def _restore_scan_progress(self):
        self._update_progress_active = False
        self._display_progress(f"{self._mode_label()}进度：", self._scan_progress)

    def _set_update_busy(self, busy):
        controls = (self.scan_button, self.continue_button, self.update_button, self.clear_log_button)
        if busy:
            self._update_disabled_widgets = [(widget, str(widget.cget("state"))) for widget in controls]
            for widget, _ in self._update_disabled_widgets:
                widget.configure(state="disabled")
        else:
            for widget, state in getattr(self, "_update_disabled_widgets", []):
                if widget.winfo_exists():
                    widget.configure(state=state)
            self.continue_button.configure(state="normal" if self._processing_job else "disabled")

    def _set_processing_busy(self, busy):
        self._processing_busy = busy
        if busy:
            self._disabled_widgets = []
            def walk(parent):
                for widget in parent.winfo_children():
                    if widget is self.stop_button:
                        continue
                    if isinstance(widget, (ttk.Button, ttk.Entry, ttk.Combobox, ttk.Spinbox, ttk.Checkbutton)):
                        self._disabled_widgets.append((widget, str(widget.cget("state"))))
                        widget.configure(state="disabled")
                    walk(widget)
            walk(self)
        else:
            for widget, state in getattr(self, "_disabled_widgets", []):
                if widget.winfo_exists():widget.configure(state=state)
            self.continue_button.configure(state="normal" if self._processing_job else "disabled")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _start_processing(self, resume=False, mode="scan", regroup=False, regenerate=False):
        if self._processing_busy or self.updates.busy:
            return
        if not self._sync_selected_workspace():
            return
        if not self.input_var.get().strip():
            messagebox.showinfo("处理照片", "请先选择照片文件夹。", parent=self)
            return
        try:
            options = self._processing_options()
            if not 8 <= options['photos_per_page'] <= 60 or not 2 <= options['columns'] <= 6:
                raise ValueError("每页照片数应为 8～60，列数应为 2～6")
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("设置无效", str(exc), parent=self)
            return
        input_dir, workspace = self.input_var.get(), self.workspace_var.get()
        crops = copy.deepcopy(self.crop_settings)
        if regenerate:  # compatibility with callers from pre-1.2 tests/plugins
            mode = "rescan"
        if resume:
            try:
                from .processing_job import load_job
                interrupted = load_job(workspace, input_dir)
            except Exception as exc:
                messagebox.showerror("继续处理", str(exc), parent=self)
                return
            if interrupted is None:
                messagebox.showinfo("继续处理", "没有可继续的中断任务。", parent=self)
                return
            mode = self._job_mode(interrupted)
        focus_work_exists = True
        if mode == "focus" and self.scan_result:
            from .lightroom_results import focus_review_status
            focus_work_exists = any(
                asset.ai_focus_dirty
                or (asset.ai_focus_result is None and focus_review_status(asset) is True)
                for asset in self.scan_result.assets
            )
        selected_profile = (
            self._selected_api_profile()
            if mode == "legacy" or (mode == "focus" and focus_work_exists)
            else None
        )
        if mode == "focus" and focus_work_exists and selected_profile is None:
            messagebox.showinfo("AI 复核", "请先在主页面配置并选择可识图的 API。", parent=self)
            return
        focus_profile = MappingProxyType(copy.deepcopy(selected_profile)) if selected_profile else None
        previous = copy.deepcopy(self.scan_result) if mode != "scan" else None
        if not resume and mode != "scan" and previous is None:
            messagebox.showinfo(self._mode_label(mode), "请先扫描图片。", parent=self)
            return
        self._save_preferences()
        self._workspace_cleared = False
        self._stop_event.clear()
        self._processing_mode = mode
        if mode in {"scan", "rescan", "focus", "sheets"}:
            self._set_sheets_ready(False)
        self._set_processing_busy(True)
        if not resume:self._set_progress(0)
        self.next_step_var.set("推荐下一步：等待处理完成")
        self._log(
            "继续处理，已完成部分保留原设置，未完成部分使用当前设置。"
            if resume else f"开始{self._mode_label(mode)}。"
        )
        def work():
            try:
                from .processing_job import start_job, load_job
                job = load_job(workspace, input_dir) if resume else start_job(
                    input_dir, workspace, options, crops,
                    result=previous, regroup=regroup, mode=mode,
                )
                if job is None:raise ValueError("没有可继续的中断任务")
                self._process_events.put(("job", job))
                result = job.run(
                    options,
                    crops,
                    self._stop_event,
                    lambda percent: self._process_events.put(("progress", percent)),
                    focus_profile=focus_profile,
                    on_log=lambda text: self._process_events.put(("log", text)),
                )
                self._process_events.put(("done", (job, result)))
            except Exception as exc:
                self._process_events.put(("error", str(exc)))
        self._scan_thread = threading.Thread(target=work, daemon=True)
        self._scan_thread.start()

    def _poll_processing(self):
        try:
            while True:
                event, value = self._process_events.get_nowait()
                if event == "progress":self._set_progress(value)
                elif event == "log":self._log(value)
                elif event == "job":self._processing_job = value
                elif event == "done":
                    job, result = value
                    if result is None:
                        self._processing_job = job
                        self._log("已停止并保存进度，下次可点击继续处理。")
                    else:
                        mode = self._job_mode(job)
                        self.scan_result = result
                        self.review_project = None
                        self._processing_job = None
                        self._set_progress(100)
                        from .lightroom_results import focus_review_status
                        ai_rejected = sum(1 for asset in result.assets if asset.screening_reason == "ai_focus_blur")
                        uncertain = sum(1 for asset in result.assets if focus_review_status(asset) is True)
                        local_rejected = sum(
                            1 for asset in result.assets
                            if asset.auto_rejected and asset.screening_reason != "ai_focus_blur"
                        )
                        rejected = local_rejected + ai_rejected
                        eligible = len(result.assets) - rejected
                        if mode == "scan":
                            self._log(
                                f"扫描完成：{len(result.assets)} 张照片；本地虚焦/抖动弃置 {local_rejected} 张；"
                                f"清晰度待 AI 复核 {uncertain} 张。"
                            )
                            next_step = "检测/调整人脸框"
                        elif mode == "rescan":
                            self._log(
                                f"修改照片重新扫描完成；本地虚焦/抖动弃置共 {local_rejected} 张；"
                                f"清晰度待 AI 复核 {uncertain} 张。"
                            )
                            next_step = "AI 复核"
                        elif mode == "focus":
                            self._log(
                                f"AI 复核完成：弃置 {ai_rejected} 张；仍不确定 {uncertain} 张；"
                                f"可进入选片 {eligible} 张。"
                            )
                            next_step = "生成联系表"
                        elif mode == "sheets":
                            self._set_sheets_ready(True)
                            self._log(
                                f"联系表生成完成：主联系表 {len(result.main_pages or [])} 页；"
                                f"弃置复核表 {len(result.rejected_pages or [])} 页。"
                            )
                            next_step = "AI 选片与导出"
                        else:  # interrupted all-in-one job created before v1.2
                            self._set_sheets_ready(True)
                            self._log(
                                f"处理完成：{len(result.assets)} 张照片；初筛技术模糊/抖动弃置 {local_rejected} 张；"
                                f"AI 复核弃置 {ai_rejected} 张；清晰度仍不确定 {uncertain} 张；"
                                f"剩余可进入 AI 选片 {eligible} 张；主联系表 {len(result.main_pages or [])} 页。"
                            )
                            next_step = "AI 选片与导出"
                    self._set_processing_busy(False)
                    self.next_step_var.set("推荐下一步：" + (
                        "继续处理" if self._processing_job else next_step
                    ))
                    if self._close_after_stop:
                        self._close_after_stop = False
                        self.after(50, self._close)
                elif event == "error":
                    self._log("处理未完成：" + value)
                    self._set_processing_busy(False)
                    self._restore_processing_job()
                    messagebox.showerror("处理未完成", value, parent=self)
        except queue.Empty:
            pass
        self.after(100, self._poll_processing)

    def _stop_processing(self):
        if self._processing_busy:
            self._stop_event.set()
            self.stop_button.configure(state="disabled")
            self.next_step_var.set("推荐下一步：等待当前处理单元保存")

    def _continue_processing(self):
        self._start_processing(resume=True, mode=self._processing_mode)

    def _run_scan_thread(self):
        self._start_processing(mode="scan")

    def _run_focus_review(self):
        self._start_processing(mode="focus")

    def _generate_contact_sheets(self):
        self._start_processing(mode="sheets")

    def _open_crop_settings(self) -> None:
        if self.updates.busy:
            messagebox.showinfo("提示", "请等待更新完成。", parent=self)
            return
        if not self._sync_selected_workspace():
            return
        assets = self.scan_result.assets if self.scan_result else []
        CropDialog(self, assets, self.crop_settings, self._save_crop_settings)

    def _save_crop_settings(self, settings: CropSettings) -> None:
        if not self._sync_selected_workspace():
            return
        self._workspace_cleared = False
        if self._active_input and self._active_workspace:
            save_workspace_preferences(
                Path(self._active_workspace),
                asdict(settings),
                self._workspace_options(),
            )
        if self.scan_result:
            for asset in self.scan_result.assets:
                key = settings.key(asset)
                if (self.crop_settings.photos.get(key, {}) != settings.photos.get(key, {})
                        or self.crop_settings.detection_confidence != settings.detection_confidence):
                    asset.ai_focus_dirty = True
            self._save_session()
        self.crop_settings = settings
        if self.scan_result:
            self._set_sheets_ready(False)
            self._log("人脸设置已保存，正在重新扫描修改过的图片。")
            self._start_processing(mode="rescan")
        else:
            self._log("人脸设置已保存，将在下次扫描时应用。")
            self.next_step_var.set("推荐下一步：扫描图片")

    def _open_group_editor(self) -> None:
        if self.updates.busy:
            messagebox.showinfo("提示", "请等待更新完成。", parent=self)
            return
        if not self._sync_selected_workspace():
            return
        if not self.scan_result:
            messagebox.showinfo("提示", "请先扫描照片。")
            return

        def save_changes() -> None:
            if not self.scan_result:
                return
            persist_manual_groups(self.scan_result)
            self._workspace_cleared = False
            self._set_sheets_ready(False)
            self._save_session()
            self._log("人工分组已保存到 groups.json。")
            self.next_step_var.set("推荐下一步：AI 复核")

        GroupEditor(self, self.scan_result.assets, save_changes)

    def _regenerate_contacts(self):
        self._ensure_selected_session()
        if not self.scan_result:
            messagebox.showinfo("提示", "请先扫描照片。", parent=self)
            return
        settings_path = self.scan_result.workspace_dir / 'processing-settings.json'
        try:
            previous = json.loads(settings_path.read_text('utf-8')).get('grouping_preset')
        except (OSError, ValueError):
            previous = self._loaded_grouping
        regroup = previous != GROUPING_LABELS[self.preset_var.get()]
        if regroup:
            try:
                manual = json.loads(self.scan_result.group_store_path.read_text('utf-8')).get('source') == 'manual'
            except (OSError, ValueError):
                manual = self.scan_result.groups_loaded_from_store
            if manual and not messagebox.askyesno("覆盖人工分组", "分组灵敏度已改变，重新分组会覆盖人工拆分／合并。是否继续？", parent=self):
                return
        self._start_processing(mode="rescan", regroup=regroup)

    def _clear_workspace(self):
        if self._processing_busy or self.updates.busy or any(
            isinstance(child, tk.Toplevel) and child.winfo_exists()
            for child in self.winfo_children()
        ):
            messagebox.showinfo("暂不能清理", "请等待当前扫描、更新或窗口中的任务结束。", parent=self)
            return
        if not self.input_var.get().strip() or not self.workspace_var.get().strip():
            messagebox.showinfo("暂不能清理", "请先选择照片文件夹。", parent=self)
            return
        synced = self._sync_selected_workspace()
        if not synced and (
            not self._workspace_blocked
            or self.input_var.get().strip() != self._active_input
            or self.workspace_var.get().strip() != self._active_workspace
        ):
            messagebox.showinfo("暂不能清理", "请先选择有效的照片文件夹和工作区。", parent=self)
            return
        if not messagebox.askyesno(
            "清空工作区",
            "将清空当前工作区中的扫描结果、分组、人脸调整、AI 结果、联系表、缓存和日志。\n"
            "原照片和全局 API 配置不会删除。是否继续？",
            parent=self,
        ):
            return
        originals = []
        if self.scan_result:
            for asset in self.scan_result.assets:
                originals.extend(
                    value for value in (
                        asset.primary_path, asset.raw_path, asset.jpg_path, *asset.rating_target_paths
                    ) if value
                )
        with self._log_lock:
            self._log_epoch += 1
            try:
                from .workspace_clear import clear_workspace
                clear_workspace(
                    self.workspace_var.get(),
                    input_dir=self.input_var.get(),
                    program_dir=application_dir(),
                    original_paths=originals,
                )
            except (OSError, ValueError) as exc:
                messagebox.showerror("无法清空工作区", str(exc), parent=self)
                return
            self.scan_result = None
            self.review_project = None
            self._processing_job = None
            self.crop_settings = CropSettings()
            self._suppress_settings_trace = True
            try:
                self.preset_var.set("标准")
                self.per_page_var.set(16)
                self.columns_var.set(4)
                self.screening_var.set(True)
            finally:
                self._suppress_settings_trace = False
            self.saved_options = {"no_auto_updates": self.no_updates_var.get()}
            self._workspace_cleared = True
            self._workspace_blocked = False
            self._workspace_error = ""
            self._processing_mode = "scan"
            self._scan_progress = 0
            self._display_progress("扫描图片进度：", 0)
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        self.continue_button.configure(state="disabled")
        self.next_step_var.set("推荐下一步：扫描图片")

    def _clear_log(self):
        """Compatibility alias for integrations written before v1.2."""
        self._clear_workspace()

    def _open_contact_dir(self) -> None:
        if not self._sync_selected_workspace():
            return
        if not self.scan_result or not self._sheets_ready():
            messagebox.showinfo("提示", "请先生成联系表")
            return
        path = self.scan_result.contact_dir
        if not path.exists():
            messagebox.showerror("错误", f"目录不存在：{path}")
            return
        try:
            import os
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            messagebox.showinfo("联系表目录", str(path))

    def _ensure_selected_session(self):
        if not self._sync_selected_workspace():
            return
        if self.scan_result and (self.scan_result.workspace_dir.resolve() != Path(self.workspace_var.get()).resolve() or (self.scan_result.input_dir and self.scan_result.input_dir.resolve() != Path(self.input_var.get()).resolve())):
            self.scan_result = None
            self._restore_session()
            self._restore_processing_job()

    def _open_ai_review(self):
        if self.updates.busy or (getattr(self, '_scan_thread', None) and self._scan_thread.is_alive()):
            messagebox.showinfo("提示", "请等待扫描或更新结束。", parent=self)
            return
        self._ensure_selected_session()
        if not self.scan_result:
            messagebox.showinfo("提示", "请先扫描图片并生成联系表。", parent=self)
            return
        if not self._sheets_ready():
            messagebox.showinfo("提示", "当前联系表尚未生成或已经过期，请先生成联系表。", parent=self)
            return
        try:
            from .ai_project import ReviewProject
            from .ai_review_ui import ReviewDialog
            self.review_project = ReviewProject(self.scan_result.workspace_dir)
            self.review_project.refresh(self.scan_result.assets,self.crop_settings)
            ReviewDialog(self,self.review_project,self.scan_result.assets,self.crop_settings,self.settings_dir,
                home_pages=list(self.scan_result.main_pages or []) + list(self.scan_result.rejected_pages or []))
        except Exception as exc:
            messagebox.showerror("AI 选片",str(exc),parent=self)

    def _open_lr_plugin(self):
        import os
        path = application_dir() / "lightroom"
        if path.exists():
            os.startfile(path)
        else:
            messagebox.showinfo("Lightroom 插件", "请使用完整便携包中的 lightroom 文件夹。", parent=self)

    def _log(self, text: str) -> None:
        with self._log_lock:
            epoch = self._log_epoch
            try:
                workspace_value = self.workspace_var.get().strip()
                if workspace_value and not self._workspace_blocked:
                    logfile = workspace_path(Path(workspace_value), "session.log")
                    logfile.parent.mkdir(parents=True, exist_ok=True)
                    with logfile.open("a", encoding="utf-8") as stream:
                        stream.write(text + "\n")
            except OSError:
                pass
        def append():
            if epoch != self._log_epoch:return
            self.log_text.configure(state="normal")
            self.log_text.insert("end", text + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, append)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
