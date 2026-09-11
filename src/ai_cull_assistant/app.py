from __future__ import annotations

import threading
import queue
import copy
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .group_editor import GroupEditor
from .version import VERSION
from .update_ui import UpdateController
import sys
from .settings import application_dir, load_paths, save_paths, read_values, save_values
from .crop_settings import CropSettings
from .crop_dialog import CropDialog
from dataclasses import asdict
from .workflow import (
    ScanResult,
    persist_manual_groups,
    regenerate_contact_sheet_sets,
    reset_auto_groups,
    run_scan,
)


GROUPING_LABELS = {"严格": "strict", "标准": "standard", "宽松": "loose"}


class App(tk.Tk):
    def __init__(self, settings_dir: Path | None = None) -> None:
        super().__init__()
        self.title(f"AI 选片助手 v{VERSION}")
        self.geometry("980x780")
        self.review_project = None
        self.scan_result: ScanResult | None = None
        self.settings_dir = settings_dir if settings_dir is not None else application_dir()
        self.saved_paths = load_paths(self.settings_dir)
        self.crop_settings = CropSettings.from_dict(read_values(self.settings_dir).get("face_crop", {}))
        self.saved_options = read_values(self.settings_dir).get("options", {})
        if not isinstance(self.saved_options, dict):
            self.saved_options = {}
        self._loaded_grouping = GROUPING_LABELS.get(self.saved_options.get("grouping"), "standard")
        self._processing_busy = False
        self._processing_job = None
        self._stop_event = threading.Event()
        self._process_events = queue.Queue()
        self._log_lock = threading.Lock()
        self._log_epoch = 0
        self._close_after_stop = False
        self._settings_pending = None
        self._build_ui()
        self._restore_session()
        for variable in (self.input_var, self.workspace_var, self.preset_var, self.per_page_var, self.columns_var, self.screening_var, self.no_updates_var):
            variable.trace_add("write", self._schedule_settings_save)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.updates = UpdateController(self)
        self._restore_processing_job()
        self.after(100, self._poll_processing)
        if settings_dir is None and getattr(sys, 'frozen', False) and '--self-test' not in sys.argv and not self.no_updates_var.get():
            self.after(1500, lambda: self.updates.check() if not self.no_updates_var.get() and not self._processing_busy else None)

    def _save_session(self, fresh=False):
        if self.scan_result:
            try:
                from .session_store import save_session
                save_session(self.scan_result, fresh=fresh)
            except Exception as exc:
                self._log(f"分析记录保存失败：{exc}")

    def _restore_session(self):
        workspace = Path(self.workspace_var.get())
        logfile = workspace / 'session.log'
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
                self._log(f"已恢复上次分析：{len(self.scan_result.assets)} 张照片，可直接进入 AI 选片。新增照片请重新扫描。")
        except Exception as exc:
            self._log(f"上次分析未恢复：{exc}。请检查照片路径或重新扫描；已有 AI 任务仍保留。")

    def _option_int(self, key, default, minimum, maximum):
        try:
            return max(minimum, min(maximum, int(self.saved_options.get(key, default))))
        except (ValueError, TypeError):
            return default

    def _schedule_settings_save(self, *_):
        if self._settings_pending:
            self.after_cancel(self._settings_pending)
        self._settings_pending = self.after(500, self._save_preferences)

    def _save_preferences(self):
        self._settings_pending = None
        options = dict(self.saved_options)
        options['grouping'] = self.preset_var.get()
        options['screening'] = self.screening_var.get()
        options['no_auto_updates'] = self.no_updates_var.get()
        for key, variable, low, high in [('per_page',self.per_page_var,8,60), ('columns',self.columns_var,2,6)]:
            try:
                value = variable.get()
                if low <= value <= high:
                    options[key] = value
            except tk.TclError:
                pass  # Keep the previous valid value while a spinbox is being edited.
        try:
            save_values(self.settings_dir, dict(input=self.input_var.get(), workspace=self.workspace_var.get(), options=options))
            self.saved_options = options
        except OSError as exc:
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
        for timer in self.tk.splitlist(self.tk.call('after', 'info')):
            self.after_cancel(timer)
        self.destroy()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

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

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=row, column=0, columnspan=6, sticky="w", pady=10)
        ttk.Button(btn_frame, text="扫描并生成联系表", command=self._run_scan_thread).pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(btn_frame, text="停止处理", command=self._stop_processing, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))
        self.continue_button = ttk.Button(btn_frame, text="继续处理", command=self._continue_processing, state="disabled")
        self.continue_button.pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="重新生成联系表", command=self._regenerate_contacts).pack(side="left", padx=(0, 8))
        self.clear_log_button = ttk.Button(btn_frame, text="清空日志", command=self._clear_log)
        self.clear_log_button.pack(side="left")
        row += 1
        second = ttk.Frame(frame)
        second.grid(row=row, column=0, columnspan=6, sticky="w", pady=(0, 10))
        for title, command in (("编辑选片组", self._open_group_editor), ("检查／调整人脸框", self._open_crop_settings), ("打开联系表目录", self._open_contact_dir), ("AI 选片与 LR 导出", self._open_ai_review)):
            ttk.Button(second, text=title, command=command).pack(side="left", padx=(0, 8))
        row += 1
        lr_frame = ttk.Frame(frame)
        lr_frame.grid(row=row, column=0, columnspan=6, sticky="w", pady=(0, 10))
        ttk.Button(lr_frame, text="LR 插件", command=self._open_lr_plugin).pack(side="left")
        ttk.Button(lr_frame, text="检查更新", command=lambda: self.updates.check(True)).pack(side="left", padx=12)
        ttk.Checkbutton(lr_frame, text="不再自动检查更新", variable=self.no_updates_var, command=self._save_preferences).pack(side="left")
        row += 1
        self.next_step_var = tk.StringVar(value="推荐下一步：扫描并生成联系表")
        ttk.Label(frame, textvariable=self.next_step_var).grid(row=row, column=0, columnspan=6, sticky="w", pady=8)
        row += 1
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text = tk.StringVar(value="整体进度约 0%")
        ttk.Progressbar(frame, variable=self.progress_var, maximum=100).grid(row=row, column=0, columnspan=4, sticky="ew")
        ttk.Label(frame, textvariable=self.progress_text).grid(row=row, column=4, columnspan=2)
        row += 1
        ttk.Label(frame, text="日志：").grid(row=row, column=0, columnspan=6, sticky="w", pady=(8, 0))
        row += 1
        self.log_text = tk.Text(frame, height=12, wrap="word", state="disabled")
        self.log_text.grid(row=row, column=0, columnspan=6, sticky="nsew")

        for col in range(6):
            frame.columnconfigure(col, weight=1)
        frame.rowconfigure(row - 1, weight=1)
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

    def _restore_processing_job(self):
        try:
            from .processing_job import load_job
            self._processing_job = load_job(self.workspace_var.get(), self.input_var.get())
        except Exception as exc:
            self._processing_job = None
            self._log(str(exc))
        self.continue_button.configure(state="normal" if self._processing_job else "disabled")
        if self._processing_job:
            self._set_progress(self._processing_job.percent)
        self.next_step_var.set("推荐下一步：" + ("继续处理" if self._processing_job else "AI 选片与 LR 导出" if self.scan_result else "扫描并生成联系表"))

    def _set_progress(self, value):
        value = max(0, min(100, int(value)))
        self.progress_var.set(value)
        self.progress_text.set(f"整体进度约 {value}%")

    def _set_processing_busy(self, busy):
        self._processing_busy = busy
        if busy:
            self._disabled_widgets = []
            def walk(parent):
                for widget in parent.winfo_children():
                    if widget in (self.stop_button, self.clear_log_button):
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

    def _start_processing(self, resume=False, regenerate=False, regroup=False):
        if self._processing_busy or self.updates.busy:
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
        previous = copy.deepcopy(self.scan_result) if regenerate else None
        self._save_preferences()
        self._stop_event.clear()
        self._set_processing_busy(True)
        if not resume:self._set_progress(0)
        self.next_step_var.set("推荐下一步：等待处理完成")
        self._log("继续处理，已完成部分保留原设置，未完成部分使用当前设置。" if resume else "开始重新生成联系表。" if regenerate else "开始扫描并生成联系表。")
        def work():
            try:
                from .processing_job import start_job, load_job
                job = load_job(workspace, input_dir) if resume else start_job(input_dir, workspace, options, crops, result=previous, regroup=regroup)
                if job is None:raise ValueError("没有可继续的中断任务")
                self._process_events.put(("job", job))
                result = job.run(options, crops, self._stop_event, lambda percent: self._process_events.put(("progress", percent)))
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
                elif event == "job":self._processing_job = value
                elif event == "done":
                    job, result = value
                    if result is None:
                        self._processing_job = job
                        self._log("已停止并保存进度，下次可点击继续处理。")
                    else:
                        self.scan_result = result
                        self.review_project = None
                        self._processing_job = None
                        self._set_progress(100)
                        self._log(f"处理完成：{len(result.assets)} 张照片；主联系表 {len(result.main_pages or [])} 页。")
                    self._set_processing_busy(False)
                    self.next_step_var.set("推荐下一步：" + ("继续处理" if self._processing_job else "AI 选片与 LR 导出"))
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
        self._start_processing(resume=True)

    def _run_scan_thread(self):
        self._start_processing()

    def _open_crop_settings(self) -> None:
        if self.updates.busy:
            messagebox.showinfo("提示", "请等待更新完成。", parent=self)
            return
        assets = self.scan_result.assets if self.scan_result else []
        CropDialog(self, assets, self.crop_settings, self._save_crop_settings)

    def _save_crop_settings(self, settings: CropSettings) -> None:
        save_values(self.settings_dir, {"face_crop": asdict(settings)})
        self.crop_settings = settings
        self._log("人脸设置已保存，点击继续处理或重新生成联系表应用到后续内容。")
        self.next_step_var.set("推荐下一步：" + ("继续处理" if self._processing_job else "重新生成联系表"))

    def _open_group_editor(self) -> None:
        if self.updates.busy:
            messagebox.showinfo("提示", "请等待更新完成。", parent=self)
            return
        if not self.scan_result:
            messagebox.showinfo("提示", "请先扫描照片。")
            return

        def save_changes() -> None:
            if not self.scan_result:
                return
            persist_manual_groups(self.scan_result)
            self._save_session()
            self._log("人工分组已保存到 groups.json。")

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
        self._start_processing(regenerate=True, regroup=regroup)

    def _clear_log(self):
        with self._log_lock:
            workspace = Path(self.workspace_var.get())
            try:
                (workspace / 'session.log').unlink(missing_ok=True)
            except OSError as exc:
                messagebox.showerror("清空日志失败", str(exc), parent=self)
                return
            self._log_epoch += 1
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")

    def _open_contact_dir(self) -> None:
        if not self.scan_result:
            messagebox.showinfo("提示", "请先扫描生成联系表")
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
            messagebox.showinfo("提示", "请先扫描照片；同一工作区会恢复保存的 AI 任务与人工结果。", parent=self)
            return
        try:
            from .ai_project import ReviewProject
            from .ai_review_ui import ReviewDialog
            self.review_project = ReviewProject(self.scan_result.workspace_dir)
            self.review_project.refresh(self.scan_result.assets,self.crop_settings)
            ReviewDialog(self,self.review_project,self.scan_result.assets,self.crop_settings,self.settings_dir)
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
                workspace = Path(self.workspace_var.get())
                workspace.mkdir(parents=True, exist_ok=True)
                with (workspace / "session.log").open("a", encoding="utf-8") as stream:
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
