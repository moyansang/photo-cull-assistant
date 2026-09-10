from __future__ import annotations

import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .group_editor import GroupEditor
from .settings import application_dir, load_paths, save_paths
from .workflow import (
    ScanResult,
    apply_selection_text,
    persist_manual_groups,
    regenerate_contact_sheet_sets,
    reset_auto_groups,
    run_scan,
)


class App(tk.Tk):
    def __init__(self, settings_dir: Path | None = None) -> None:
        super().__init__()
        self.title("AI 选片助手 v0.4.1")
        self.geometry("980x780")
        self.scan_result: ScanResult | None = None
        self.settings_dir = settings_dir if settings_dir is not None else application_dir()
        self.saved_paths = load_paths(self.settings_dir)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self) -> None:
        try:
            save_paths(self.settings_dir, {
                "input": self.input_var.get(),
                "workspace": self.workspace_var.get(),
                "export": self.export_var.get(),
            })
        except OSError as exc:
            messagebox.showwarning("目录未保存", f"无法保存目录设置，请将程序解压到可写入的文件夹。\n{exc}")
        self.destroy()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        self.input_var = tk.StringVar(value=self.saved_paths["input"])
        self.workspace_var = tk.StringVar(value=self.saved_paths["workspace"])
        self.export_var = tk.StringVar(value=self.saved_paths["export"])
        self.preset_var = tk.StringVar(value="standard")
        self.per_page_var = tk.IntVar(value=16)
        self.columns_var = tk.IntVar(value=4)
        self.screening_var = tk.BooleanVar(value=True)

        row = 0
        self._path_row(frame, row, "照片文件夹", self.input_var, self._choose_input)
        row += 1
        self._path_row(frame, row, "工作区", self.workspace_var, self._choose_workspace)
        row += 1
        self._path_row(frame, row, "精选导出目录", self.export_var, self._choose_export)
        row += 1

        ttk.Label(frame, text="分组灵敏度").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Combobox(frame, textvariable=self.preset_var, values=["strict", "standard", "loose"], state="readonly", width=12).grid(row=row, column=1, sticky="w")
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
                grouping_preset=self.preset_var.get(),
                photos_per_page=self.per_page_var.get(),
                columns=self.columns_var.get(),
                technical_screening=self.screening_var.get(),
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
            reset_auto_groups(self.scan_result, self.preset_var.get())
            sheets = regenerate_contact_sheet_sets(
                self.scan_result,
                photos_per_page=self.per_page_var.get(),
                columns=self.columns_var.get(),
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
