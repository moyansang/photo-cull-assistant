from __future__ import annotations

import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .group_editor import GroupEditor
from .settings import application_dir, load_paths, save_paths, read_values, save_values
from .crop_settings import CropSettings
from .crop_dialog import CropDialog
from dataclasses import asdict
from .workflow import (
    ScanResult,
    apply_selection_text,
    persist_manual_groups,
    regenerate_contact_sheet_sets,
    reset_auto_groups,
    run_scan,
)


GROUPING_LABELS = {"严格": "strict", "标准": "standard", "宽松": "loose"}


class App(tk.Tk):
    def __init__(self, settings_dir: Path | None = None) -> None:
        super().__init__()
        self.title("AI 选片助手 v0.4.6")
        self.geometry("980x780")
        self.scan_result: ScanResult | None = None
        self.settings_dir = settings_dir if settings_dir is not None else application_dir()
        self.saved_paths = load_paths(self.settings_dir)
        self.crop_settings = CropSettings.from_dict(read_values(self.settings_dir).get("face_crop", {}))
        self.saved_options = read_values(self.settings_dir).get("options", {})
        if not isinstance(self.saved_options, dict):
            self.saved_options = {}
        self._settings_pending = None
        self._build_ui()
        for variable in (self.input_var, self.workspace_var, self.export_var, self.preset_var, self.per_page_var, self.columns_var, self.screening_var):
            variable.trace_add("write", self._schedule_settings_save)
        self.protocol("WM_DELETE_WINDOW", self._close)

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
        for key, variable, low, high in [('per_page',self.per_page_var,8,60), ('columns',self.columns_var,2,6)]:
            try:
                value = variable.get()
                if low <= value <= high:
                    options[key] = value
            except tk.TclError:
                pass  # Keep the previous valid value while a spinbox is being edited.
        try:
            save_values(self.settings_dir, dict(input=self.input_var.get(), workspace=self.workspace_var.get(), export=self.export_var.get(), options=options))
            self.saved_options = options
        except OSError as exc:
            self._log(f"设置保存失败：{exc}")

    def _close(self) -> None:
        if self._settings_pending:
            self.after_cancel(self._settings_pending)
        self._save_preferences()
        self.destroy()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        self.input_var = tk.StringVar(value=self.saved_paths["input"])
        self.workspace_var = tk.StringVar(value=self.saved_paths["workspace"])
        self.export_var = tk.StringVar(value=self.saved_paths["export"])
        self.preset_var = tk.StringVar(value=self.saved_options.get("grouping") if self.saved_options.get("grouping") in GROUPING_LABELS else "标准")
        self.per_page_var = tk.IntVar(value=self._option_int("per_page", 16, 8, 60))
        self.columns_var = tk.IntVar(value=self._option_int("columns", 4, 2, 6))
        self.screening_var = tk.BooleanVar(value=self.saved_options.get("screening", True) if isinstance(self.saved_options.get("screening", True), bool) else True)

        row = 0
        self._path_row(frame, row, "照片文件夹", self.input_var, self._choose_input)
        row += 1
        self._path_row(frame, row, "工作区", self.workspace_var, self._choose_workspace)
        row += 1
        self._path_row(frame, row, "精选导出目录", self.export_var, self._choose_export)
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
            text="人物主体明显虚焦/严重抖动 → Lightroom 弃置（保守模式）",
            variable=self.screening_var,
        ).grid(row=row, column=0, columnspan=6, sticky="w", pady=(2, 8))
        row += 1

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=row, column=0, columnspan=6, sticky="w", pady=10)
        ttk.Button(btn_frame, text="1. 扫描并生成联系表", command=self._run_scan_thread).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="编辑选片组", command=self._open_group_editor).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="重新生成联系表", command=self._regenerate_contacts).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="重新自动分组", command=self._reset_auto_groups).pack(side="left", padx=(0, 8))

        row += 1
        second_btn_frame = ttk.Frame(frame)
        second_btn_frame.grid(row=row, column=0, columnspan=6, sticky="w", pady=(0, 10))
        ttk.Button(second_btn_frame, text="打开联系表目录", command=self._open_contact_dir).pack(side="left", padx=(0, 8))
        ttk.Button(second_btn_frame, text="2. 应用选片结果", command=self._apply_selection).pack(side="left", padx=(0, 8))

        ttk.Button(second_btn_frame, text="人脸细节设置", command=self._open_crop_settings).pack(side="left", padx=(0, 8))

        row += 1
        ttk.Label(frame, text="把 ChatGPT 返回的选片结果粘贴到这里：").grid(row=row, column=0, columnspan=6, sticky="w")
        row += 1
        self.selection_text = tk.Text(frame, height=16, wrap="word")
        self.selection_text.grid(row=row, column=0, columnspan=6, sticky="nsew")
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

    def _choose_export(self) -> None:
        path = filedialog.askdirectory(title="选择精选导出目录")
        if path:
            self.export_var.set(path)

    def _run_scan_thread(self) -> None:
        if not self.input_var.get().strip():
            messagebox.showwarning("提示", "请先选择照片文件夹")
            return
        thread = threading.Thread(target=self._run_scan, daemon=True)
        thread.start()

    def _run_scan(self) -> None:
        try:
            self._log("开始扫描照片...")
            result = run_scan(
                self.input_var.get(),
                self.workspace_var.get(),
                grouping_preset=GROUPING_LABELS[self.preset_var.get()],
                photos_per_page=self.per_page_var.get(),
                columns=self.columns_var.get(),
                technical_screening=self.screening_var.get(),
                crop_settings=self.crop_settings,
            )
            self.scan_result = result
            self._log(f"扫描完成：共 {len(result.assets)} 张逻辑照片")
            if result.groups_loaded_from_store:
                self._log("已读取工作区 groups.json，保留上次人工分组。")
                self._log("如需使用新版分组算法，请点击“重新自动分组”（会覆盖人工分组）。")
            else:
                self._log("未找到可用的保存分组，已按当前灵敏度自动分组。")
            self._log(f"主联系表：{result.contact_dir / 'main'}")
            if self.screening_var.get():
                self._log(f"自动弃置：{result.rejected_count} 张（仅明显人物主体虚焦/严重抖动）")
                self._log(f"弃置复核表：{result.contact_dir / 'rejected_review'}")
                if result.screening_results_path:
                    self._log(f"技术筛选报告：{result.screening_results_path}")
            self._log("可先进入“编辑选片组”人工拆分/合并，再重新生成联系表。")
        except Exception as exc:
            self._log(f"扫描失败：{exc}")
            messagebox.showerror("扫描失败", str(exc))

    def _open_crop_settings(self) -> None:
        assets = self.scan_result.assets if self.scan_result else []
        CropDialog(self, assets, self.crop_settings, self._save_crop_settings)

    def _save_crop_settings(self, settings: CropSettings) -> None:
        save_values(self.settings_dir, {"face_crop": asdict(settings)})
        self.crop_settings = settings
        if self.scan_result:
            sheets = regenerate_contact_sheet_sets(self.scan_result, photos_per_page=self.per_page_var.get(), columns=self.columns_var.get(), crop_settings=settings)
            self._log(f"人脸细节设置已保存；已生成主表 {len(sheets.main_pages)} 页、复核表 {len(sheets.rejected_pages)} 页。")
        else:
            self._log("人脸细节设置已保存，下次生成联系表时使用。")

    def _open_group_editor(self) -> None:
        if not self.scan_result:
            messagebox.showinfo("提示", "请先扫描照片。")
            return

        def save_changes() -> None:
            if not self.scan_result:
                return
            persist_manual_groups(self.scan_result)
            self._log("人工分组已保存到 groups.json。")

        GroupEditor(self, self.scan_result.assets, save_changes)

    def _regenerate_contacts(self) -> None:
        if not self.scan_result:
            messagebox.showinfo("提示", "请先扫描照片。")
            return
        try:
            sheets = regenerate_contact_sheet_sets(
                self.scan_result,
                photos_per_page=self.per_page_var.get(),
                columns=self.columns_var.get(),
                crop_settings=self.crop_settings,
            )
            self._log(f"联系表已重新生成：主表 {len(sheets.main_pages)} 页；弃置复核 {len(sheets.rejected_pages)} 页。")
            self._log(f"主联系表目录：{self.scan_result.contact_dir / 'main'}")
        except Exception as exc:
            self._log(f"重新生成联系表失败：{exc}")
            messagebox.showerror("生成失败", str(exc))

    def _reset_auto_groups(self) -> None:
        if not self.scan_result:
            messagebox.showinfo("提示", "请先扫描照片。")
            return
        confirmed = messagebox.askyesno(
            "重新自动分组",
            "这会覆盖当前人工拆组/合组结果，并按当前灵敏度重新计算。继续吗？",
        )
        if not confirmed:
            return
        try:
            reset_auto_groups(self.scan_result, GROUPING_LABELS[self.preset_var.get()])
            sheets = regenerate_contact_sheet_sets(
                self.scan_result,
                photos_per_page=self.per_page_var.get(),
                columns=self.columns_var.get(),
                crop_settings=self.crop_settings,
            )
            self._log(f"已重新自动分组并覆盖 groups.json；主联系表 {len(sheets.main_pages)} 页。")
        except Exception as exc:
            self._log(f"重新自动分组失败：{exc}")
            messagebox.showerror("分组失败", str(exc))

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

    def _apply_selection(self) -> None:
        if not self.scan_result:
            messagebox.showwarning("提示", "请先扫描并生成联系表")
            return
        text = self.selection_text.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("提示", "请先粘贴选片结果")
            return
        try:
            applied, missing, rating_map = apply_selection_text(
                self.scan_result.assets,
                text,
                export_dir=self.export_var.get().strip() or None,
            )
            self._log(f"已应用评级：{applied} 张；未匹配：{missing} 张")
            self._log(f"已生成/更新 XMP，导出目录：{self.export_var.get()}")
            self._log("Lightroom Classic 中可执行：元数据 → 从文件读取元数据")
            if rating_map:
                top = list(rating_map.items())[:10]
                self._log("示例评级：" + ", ".join(f"{stem}={rating}" for stem, rating in top))
        except Exception as exc:
            self._log(f"应用失败：{exc}")
            messagebox.showerror("应用失败", str(exc))

    def _log(self, text: str) -> None:
        def append() -> None:
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
