from __future__ import annotations

import copy
import os
import queue
import threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Iterable

from PIL import Image, ImageOps, ImageTk
from .window_layout import fit_window, ScrollableFrame


DONE_STATUSES = {"completed", "complete", "done", "已完成"}
STATUS_LABELS = {
    "pending": "待发送",
    "awaiting_response": "待粘贴",
    "running": "提交中",
    "completed": "已完成",
    "complete": "已完成",
    "done": "已完成",
    "failed": "失败",
    "invalid": "需要修正",
}


def _state(project: Any) -> dict[str, Any]:
    """Return the project's public JSON state while tolerating common wrappers."""
    for name in ("data", "state", "project"):
        value = getattr(project, name, None)
        if isinstance(value, dict):
            return value
    if isinstance(project, dict):
        return project
    raise AttributeError("项目对象没有可读取的 data/state JSON")


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("id", ""))


def _batch_id(batch: dict[str, Any]) -> str:
    return str(batch.get("id", ""))


def _is_done(batch: dict[str, Any]) -> bool:
    return str(batch.get("status", "pending")).lower() in DONE_STATUSES


def _asset_value(asset: Any, name: str, default: Any = None) -> Any:
    if isinstance(asset, dict):
        return asset.get(name, default)
    return getattr(asset, name, default)


class PasteResponseDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        on_submit: Callable[[str], bool],
        *,
        title: str = "粘贴模型回答",
        instruction: str = "粘贴模型返回的完整内容（可包含 JSON 代码块）：",
        submit_text: str = "校验并保存 JSON",
    ) -> None:
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.on_submit = on_submit
        viewport = ScrollableFrame(self, padding=12)
        viewport.pack(fill="both", expand=True)
        body = viewport.content
        ttk.Label(body, text=instruction).pack(anchor="w")
        text_frame = ttk.Frame(body)
        text_frame.pack(fill="both", expand=True, pady=(6, 10))
        self.text = tk.Text(text_frame, wrap="word", undo=True)
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        row = ttk.Frame(body)
        row.pack(fill="x")
        ttk.Button(row, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(row, text=submit_text, command=self._submit).pack(side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        fit_window(self, (760, 560), minimum_size=(480, 360), parent=parent)
        self.grab_set()
        self.text.focus_set()

    def _submit(self) -> None:
        raw = self.text.get("1.0", "end-1c")
        if not raw.strip():
            messagebox.showinfo("模型回答", "请先粘贴模型回答。", parent=self)
            return
        if self.on_submit(raw):
            self.destroy()


class RawResponsesDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, responses: Iterable[Any]) -> None:
        super().__init__(parent)
        self.title("原始回答")
        self.transient(parent)
        self.responses = list(responses)
        viewport = ScrollableFrame(self, padding=12)
        viewport.pack(fill="both", expand=True)
        body = viewport.content
        left = ttk.Frame(body)
        left.pack(side="left", fill="y", padx=(0, 10))
        self.listbox = tk.Listbox(left, width=26, exportselection=False)
        self.listbox.pack(fill="both", expand=True)
        for index, response in enumerate(self.responses, 1):
            source = None
            if isinstance(response, dict):
                profile = response.get("api_profile") or {}
                source = response.get("format") or profile.get("model")
            self.listbox.insert("end", f"第 {index} 次{f' · {source}' if source else ''}")
        self.text = tk.Text(body, wrap="word", state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._select)
        if self.responses:
            self.listbox.selection_set(0)
            self._select()
        else:
            self._show("本批尚无保存的原始回答。")
        ttk.Button(self, text="关闭", command=self.destroy).pack(pady=(0, 10))
        fit_window(self, (900, 620), minimum_size=(520, 360), parent=parent)
        self.grab_set()

    def _select(self, _event: Any = None) -> None:
        selected = self.listbox.curselection()
        if not selected:
            return
        response = self.responses[selected[0]]
        if isinstance(response, dict):
            value = response.get("text", "")
        else:
            value = str(response)
        self._show(str(value))

    def _show(self, value: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.configure(state="disabled")


class ReviewDialog(tk.Toplevel):
    """AI task submission and explicit human review workflow."""

    def __init__(
        self,
        parent: tk.Misc,
        project: Any,
        assets: Iterable[Any],
        crop_settings: Any,
        settings_dir: str | Path,
        home_pages=None,
    ) -> None:
        super().__init__(parent)
        self.title("AI 选片与 Lightroom 导出")
        self.transient(parent)
        self.project = project
        self.assets = list(assets)
        self.crop_settings = crop_settings
        self.settings_dir = Path(settings_dir)
        self.home_pages = list(home_pages) if home_pages is not None else None
        self._asset_by_stem = {str(_asset_value(a, "stem", "")): a for a in self.assets}
        self._photo_ids: list[str] = []
        self._task_labels: dict[str, str] = {}
        self._profile_labels: dict[str, dict[str, Any]] = {}
        self._preview_photo: ImageTk.PhotoImage | None = None
        self._api_queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._api_pending: list[dict[str, Any]] = []
        self._api_active = False
        self._preparing_task = False
        self._pause_requested = False
        self._closing_requested = False
        self._poll_token: str | None = None

        self.preference_vars = {
            "intensity": tk.StringVar(value="均衡保留"),
            "strategy": tk.StringVar(value="允许保留多个不同动作"),
            "focus": tk.StringVar(value="综合判断"),
            "target": tk.StringVar(value="不限制"),
            "extra": tk.StringVar(value=""),
        }
        self.task_var = tk.StringVar()
        self.profile_var = tk.StringVar()
        self.filter_var = tk.StringVar(value="全部")
        self.status_var = tk.StringVar(value="就绪")
        self.export_status_var = tk.StringVar(value="")
        self.review_caption_var = tk.StringVar(value="请选择照片")
        self._build_ui()
        fit_window(self, (1180, 820), minimum_size=(640, 500), parent=parent)
        self._load_saved_preferences()
        self._load_ui_settings()
        self._refresh_profiles()
        self._refresh_tasks(select_current=True)
        self._refresh_review()
        self._refresh_export_status()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Left>", lambda _e: self._navigate_photo(-1))
        self.bind("<Right>", lambda _e: self._navigate_photo(1))
        self._poll_token = self.after(120, self._poll_api)
        self.grab_set()
        self._create_task("initial", reuse_unchanged=True)

    # ---- layout ---------------------------------------------------------
    def _build_ui(self) -> None:
        viewport = ScrollableFrame(self, padding=12)
        viewport.pack(fill="both", expand=True)
        outer = viewport.content
        self.common_tasks = ttk.Frame(outer)
        self.common_tasks.pack(fill="x")
        notebook = self.notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        self.task_tab = ttk.Frame(notebook, padding=12)
        self.review_tab = ttk.Frame(notebook, padding=10)
        self.web_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.task_tab, text="API 选片")
        notebook.add(self.web_tab, text="网页选片")
        notebook.add(self.review_tab, text="选片结果")
        notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        self._build_task_tab()
        self._build_web_tab()
        self._build_review_tab()
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Label(footer, textvariable=self.status_var).pack(side="left")
        ttk.Button(footer, text="关闭", command=self._close).pack(side="right")
        self.export_button = ttk.Button(footer, text="导出到 LR", command=self._export_ai_ratings)
        self.export_button.pack(side="right", padx=8)

    def _tab_changed(self, _event=None):
        # Keep the preference panel in place across tabs so returning never
        # repacks the API page or changes its available area.
        if hasattr(self, "review_tree") and self.notebook.index(self.notebook.select()) == 2:
            self._show_selected_photo()

    def _build_task_tab(self) -> None:
        tab = self.task_tab
        prefs = ttk.LabelFrame(self.common_tasks, text="本轮选片偏好", padding=10)
        prefs.pack(fill="x", pady=10)
        fields = (
            ("选片力度", "intensity", ("少量精选", "均衡保留", "多留备选")),
            ("同组策略", "strategy", ("通常保留一张", "允许保留多个不同动作")),
            ("评价重点", "focus", ("表情优先", "动作优先", "综合判断")),
        )
        for col, (label, key, values) in enumerate(fields):
            ttk.Label(prefs, text=label).grid(row=0, column=col * 2, sticky="w", padx=(0, 4))
            ttk.Combobox(prefs, textvariable=self.preference_vars[key], values=values, state="readonly", width=20).grid(
                row=0, column=col * 2 + 1, sticky="ew", padx=(0, 12)
            )
        ttk.Label(prefs, text="目标数量").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(prefs, textvariable=self.preference_vars["target"], width=22).grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(8, 0))
        ttk.Label(prefs, text="补充要求").grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Entry(prefs, textvariable=self.preference_vars["extra"]).grid(row=1, column=3, columnspan=3, sticky="ew", pady=(8, 0))
        for col in (1, 3, 5):
            prefs.columnconfigure(col, weight=1)
        create = ttk.Frame(prefs)
        create.grid(row=2, column=0, columnspan=6, sticky="w", pady=(10, 0))
        self.refine_button = ttk.Button(create, text="精选照片再选一轮", command=self._create_refine)
        self.refine_button.pack(side="left", padx=(0, 8))

        batches = ttk.LabelFrame(tab, text="批次", padding=8)
        batches.pack(fill="both", expand=True)
        columns = ("status", "photos", "images", "error")
        self.batch_tree = ttk.Treeview(batches, columns=columns, show="tree headings", height=7, selectmode="browse")
        self.batch_tree.heading("#0", text="批次")
        self.batch_tree.heading("status", text="状态")
        self.batch_tree.heading("photos", text="照片")
        self.batch_tree.heading("images", text="图片")
        self.batch_tree.heading("error", text="说明")
        self.batch_tree.column("#0", width=180, stretch=True)
        self.batch_tree.column("status", width=90, anchor="center")
        self.batch_tree.column("photos", width=65, anchor="center")
        self.batch_tree.column("images", width=65, anchor="center")
        self.batch_tree.column("error", width=390, stretch=True)
        scroll = ttk.Scrollbar(batches, orient="vertical", command=self.batch_tree.yview)
        self.batch_tree.configure(yscrollcommand=scroll.set)
        self.batch_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        api = ttk.LabelFrame(tab, text="API 自动提交", padding=8)
        api.pack(fill="x", pady=(10, 0))
        ttk.Label(api, text="主页面 API").grid(row=0, column=0, sticky="w")
        ttk.Label(api, textvariable=self.profile_var).grid(row=0, column=1, sticky="w", padx=8)
        api.columnconfigure(1, weight=1)
        actions = ttk.Frame(api)
        actions.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.run_button = ttk.Button(actions, text="开始 / 继续未完成批次", command=self._start_api)
        self.run_button.pack(side="left", padx=(0, 8))
        self.resubmit_button = ttk.Button(actions, text="重新提交", command=self._resubmit_api)
        self.resubmit_button.pack(side="left", padx=(0, 8))
        self.pause_button = ttk.Button(actions, text="完成当前批后暂停", command=self._pause_api, state="disabled")
        self.pause_button.pack(side="left")
        self.split_button = ttk.Button(actions, text="拆分所选批次重试", command=self._split_selected_batch)
        self.split_button.pack(side="left", padx=(8, 0))
        ttk.Button(tab, text="查看所选批次原始回答", command=self._show_raw_responses).pack(anchor="w", pady=(8, 0))

    def _build_web_tab(self) -> None:
        tab = self.web_tab
        ttk.Label(tab, text="选择多个批次合并提交：一次上传多张联系表，使用一份完整提示词，整份回答一次导入。\n照片分组保持不变；按 Ctrl / Shift 多选，也可全选未完成批次。").pack(anchor="w")
        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="全选未完成批次", command=self._web_select_pending).pack(side="left")
        ttk.Button(actions, text="全选", command=lambda: self.web_tree.selection_set(self.web_tree.get_children())).pack(side="left", padx=8)
        ttk.Button(actions, text="合并准备所选批次", command=self._prepare_web).pack(side="left")
        self.web_summary = tk.StringVar()
        ttk.Label(actions, textvariable=self.web_summary).pack(side="left", padx=12)
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True)
        self.web_tree = ttk.Treeview(frame, columns=("status", "photos", "images"), show="tree headings", selectmode="extended", height=7)
        for key, title in (("#0", "可合并批次"), ("status", "状态"), ("photos", "照片数"), ("images", "联系表数")):
            self.web_tree.heading(key, text=title)
            self.web_tree.column(key, width=160, anchor="center")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.web_tree.yview)
        self.web_tree.configure(yscrollcommand=scrollbar.set)
        self.web_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.web_tree.bind("<<TreeviewSelect>>", lambda _e: self._web_selection_summary())
        history = ttk.LabelFrame(tab, text="已准备的网页提交（关闭后可继续）", padding=8)
        history.pack(fill="x", pady=(10, 0))
        self.web_history_var = tk.StringVar()
        self.web_history = ttk.Combobox(history, textvariable=self.web_history_var, state="readonly")
        self.web_history.pack(fill="x")
        self.web_history.bind("<<ComboboxSelected>>", lambda _e: self._remember_web())
        buttons = ttk.Frame(history)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="复制完整提示词", command=self._copy_web_prompt).pack(side="left")
        ttk.Button(buttons, text="打开全部联系表目录", command=self._open_web_folder).pack(side="left", padx=8)
        ttk.Button(buttons, text="导入整份回答", command=self._paste_web_response).pack(side="left")
        ttk.Button(buttons, text="查看原始回答", command=self._show_web_raw).pack(side="left", padx=8)
        self._web_labels = {}

    def _web_selection_summary(self) -> None:
        task = self._current_task() or {}
        chosen = set(self.web_tree.selection())
        batches = [b for b in task.get("batches", []) if b["id"] in chosen]
        self.web_summary.set(f"已选 {len(batches)} 批 / {sum(len(b['photo_ids']) for b in batches)} 张照片 / {sum(len(b['image_paths']) for b in batches)} 张联系表")

    def _web_select_pending(self) -> None:
        task = self._current_task() or {}
        self.web_tree.selection_set([b["id"] for b in task.get("batches", []) if not _is_done(b)])
        self._web_selection_summary()

    def _refresh_web(self, selected_id: str | None = None) -> None:
        old = set(self.web_tree.selection())
        self.web_tree.delete(*self.web_tree.get_children())
        task = self._current_task() or {}
        for b in task.get("batches", []):
            self.web_tree.insert("", "end", iid=b["id"], text=b["id"], values=(STATUS_LABELS.get(b["status"], b["status"]), len(b["photo_ids"]), len(b["image_paths"])))
        self.web_tree.selection_set([bid for bid in self.web_tree.get_children() if bid in old])
        self._web_labels = {}
        for item in task.get("web_submissions", []):
            label = f"{item['id']} · {len(item['batch_ids'])} 批 / {len(item['photo_ids'])} 张照片 · {STATUS_LABELS.get(item['status'], item['status'])}"
            self._web_labels[label] = item["id"]
        self.web_history.configure(values=list(self._web_labels))
        wanted = selected_id or task.get("current_web_submission_id")
        label = next((k for k, v in self._web_labels.items() if v == wanted), None)
        self.web_history_var.set(label or next(reversed(self._web_labels), ""))
        self._web_selection_summary()

    def _current_web(self) -> tuple[Any, Any]:
        task = self._current_task()
        wanted = self._web_labels.get(self.web_history_var.get())
        submission = next((s for s in (task or {}).get("web_submissions", []) if s["id"] == wanted), None)
        return task, submission

    def _remember_web(self) -> None:
        task, submission = self._current_web()
        if task and submission:
            task["current_web_submission_id"] = submission["id"]
            self._safe_save()

    def _prepare_web(self) -> None:
        if self._api_active:
            messagebox.showinfo("网页提交", "请先暂停并等待当前 API 请求结束。", parent=self)
            return
        task = self._current_task()
        if not task or not self.web_tree.selection():
            messagebox.showinfo("网页提交", "请先创建任务并选择要合并的批次。", parent=self)
            return
        if self._preparing_task:
            return
        selected = list(self.web_tree.selection())
        import gc
        gc.collect()
        self._set_preparing(True)
        self.status_var.set("正在准备网页联系表和清晰度细节图片…")
        def work():
            try:
                submission = self.project.create_web_submission(task, selected)
                task["current_web_submission_id"] = submission["id"]
                self.project.save()
                self._api_queue.put(("web_prepared", submission["id"]))
            except Exception as exc:
                self._api_queue.put(("web_prepare_error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _copy_web_prompt(self) -> None:
        task, submission = self._current_web()
        if not submission:
            messagebox.showinfo("网页提交", "请先合并准备批次，或选择历史提交。", parent=self)
            return
        try:
            images = self.project.web_images(task, submission)
            self.clipboard_clear()
            self.clipboard_append(submission["prompt"])
            self.update_idletasks()
            if submission["status"] == "pending":
                submission["status"] = "awaiting_response"
                self.project.save()
                self._refresh_web(submission["id"])
            self.status_var.set(f"已复制完整提示词，请在网页上传目录中的 {len(images)} 张图片（联系表及清晰度细节）并粘贴提示词。")
        except Exception as exc:
            messagebox.showerror("复制失败", str(exc), parent=self)

    def _open_web_folder(self) -> None:
        task, submission = self._current_web()
        if not submission:
            return
        try:
            images = self.project.web_images(task, submission)
            os.startfile(str(Path(images[0]).parent))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _paste_web_response(self) -> None:
        if self._api_active:
            messagebox.showinfo("网页提交", "请先暂停并等待当前 API 请求结束。", parent=self)
            return
        task, submission = self._current_web()
        if not submission:
            messagebox.showinfo("网页提交", "请先选择已准备的网页提交。", parent=self)
            return
        def store(raw: str) -> bool:
            try:
                issues = self.project.ingest_web(task, submission, raw)
            except Exception as exc:
                messagebox.showerror("导入失败", str(exc), parent=self)
                return False
            self._refresh_batches()
            self._refresh_review()
            self._log_web_focus(submission)
            if issues:
                messagebox.showwarning("回答需要修正", "原始回答已保存；请检查以下异常，未确认的清晰度保持待确认。\n" + "\n".join(issues), parent=self)
                return False
            self.status_var.set(f"网页提交 {submission['id']} 的 {len(submission['photo_ids'])} 张照片已导入，可导出到 Lightroom 复核。")
            return True
        PasteResponseDialog(self, store, title="导入合并提交的完整回答", instruction=f"粘贴网页提交 {submission['id']} 的完整 JSON 回答，软件会自动分配到各批次：")

    def _log_web_focus(self, submission):
        rows = [self._photos().get(pid, {}) for pid in submission.get("photo_ids", [])]
        results = [row.get("ai_focus_result") or {} for row in rows]
        blurred = sum(r.get("status") == "blur" and r.get("source") == "web" for r in results)
        pending = sum(row.get("focus_review") is True for row in rows)
        logger = getattr(self.master, "_log", None)
        if logger:
            logger(f"网页回答已处理：AI 清晰度复查弃置 {blurred} 张；清晰度待确认 {pending} 张。")

    def _show_web_raw(self) -> None:
        _, submission = self._current_web()
        if submission:
            RawResponsesDialog(self, submission.get("raw_responses", []))

    def _build_review_tab(self) -> None:
        bar = ttk.Frame(self.review_tab)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="筛选").pack(side="left")
        combo = ttk.Combobox(bar, textvariable=self.filter_var, values=("全部", "清晰度待确认", "待复核", "AI 建议弃置", "4～5 星", "回答缺失或异常"), state="readonly", width=18)
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda _e: (self._refresh_review(), self._save_ui_settings()))
        ttk.Label(bar, textvariable=self.export_status_var).pack(side="right")
        table = ttk.Frame(self.review_tab)
        table.pack(fill="both", expand=True)
        columns = ("name", "group", "ai", "flag", "reason", "clarity")
        self.review_tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse", height=8)
        for key, label, width in (("name", "照片", 145), ("group", "分组", 55), ("ai", "星级", 60), ("flag", "是否弃置", 120), ("reason", "对应 AI 回复", 400), ("clarity", "清晰度", 150)):
            self.review_tree.heading(key, text=label)
            self.review_tree.column(key, width=width, minwidth=45, stretch=key in {"name", "reason"})
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.review_tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.review_tree.xview)
        self.review_tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.review_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1); table.columnconfigure(0, weight=1)
        self.review_tree.bind("<<TreeviewSelect>>", self._show_selected_photo)
        self.review_tree.bind("<Double-1>", lambda _e: self._open_original())
        detail = ttk.Frame(self.review_tab)
        detail.pack(fill="both", expand=True, pady=(8, 0))
        preview_frame = ttk.Frame(detail, width=300, height=200)
        preview_frame.pack(side="left", fill="both", expand=True)
        preview_frame.pack_propagate(False)
        self.preview_label = ttk.Label(preview_frame, text="请选择照片", anchor="center")
        self.preview_label.pack(fill="both", expand=True)
        right = ttk.Frame(detail)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        ttk.Label(right, textvariable=self.review_caption_var).pack(anchor="w")
        details_frame = ttk.Frame(right)
        details_frame.pack(fill="both", expand=True)
        self.details = tk.Text(details_frame, height=6, width=45, wrap="word", state="disabled")
        detail_scroll = ttk.Scrollbar(details_frame, command=self.details.yview)
        self.details.configure(yscrollcommand=detail_scroll.set)
        self.details.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        ttk.Button(right, text="打开原图", command=self._open_original).pack(anchor="w", pady=6)

    # ---- project/task helpers ------------------------------------------
    def _photos(self) -> dict[str, dict[str, Any]]:
        photos = _state(self.project).get("photos", {})
        return photos if isinstance(photos, dict) else {}

    def _tasks(self) -> list[dict[str, Any]]:
        tasks = _state(self.project).get("tasks", [])
        return tasks if isinstance(tasks, list) else []

    def _preferences(self) -> dict[str, str]:
        return {key: variable.get().strip() for key, variable in self.preference_vars.items()}

    def _load_saved_preferences(self) -> None:
        saved = _state(self.project).get("preferences", {})
        if not isinstance(saved, dict):
            return
        for key, variable in self.preference_vars.items():
            if saved.get(key) is not None:
                variable.set(str(saved[key]))

    def _load_ui_settings(self) -> None:
        saved = _state(self.project).get("ui_settings", {})
        if not isinstance(saved, dict):
            return
        if saved.get("review_filter"):
            self.filter_var.set(str(saved["review_filter"]))
        tab = saved.get("submission_tab", 0)
        if tab in (0, 1):
            self.notebook.select(tab)

    def _save_ui_settings(self) -> None:
        data = _state(self.project)
        data["preferences"] = self._preferences()
        data["ui_settings"] = {
            "review_filter": self.filter_var.get(),
            "submission_tab": self.notebook.index(self.notebook.select()),
        }
        self._safe_save()

    def _current_task(self) -> dict[str, Any] | None:
        return self.project.current_task()

    def _selected_batch(self) -> dict[str, Any] | None:
        task = self._current_task()
        selection = self.batch_tree.selection()
        if not task or not selection:
            return None
        wanted = selection[0]
        return next((batch for batch in task.get("batches", []) if _batch_id(batch) == wanted), None)

    def _refresh_tasks(self, select_current=False, selected_id=None) -> None:
        self._refresh_batches()


    def _refresh_batches(self, select_id: str | None = None) -> None:
        old = select_id or (self.batch_tree.selection()[0] if self.batch_tree.selection() else None)
        self.batch_tree.delete(*self.batch_tree.get_children())
        task = self._current_task()
        if not task:
            self._refresh_web()
            return
        for batch in task.get("batches", []):
            batch_id = _batch_id(batch)
            status = str(batch.get("status", "pending"))
            images = batch.get("image_paths", [])
            self.batch_tree.insert(
                "", "end", iid=batch_id, text=batch_id,
                values=(STATUS_LABELS.get(status.lower(), status), len(batch.get("photo_ids", [])), len(images), str(batch.get("error", ""))),
            )
        children = self.batch_tree.get_children()
        if old in children:
            self.batch_tree.selection_set(old)
        elif children:
            self.batch_tree.selection_set(children[0])
        self._refresh_web()

    def _set_preparing(self, preparing):
        self._preparing_task = preparing
        if preparing:
            self._prepare_controls = []
            def walk(parent):
                for widget in parent.winfo_children():
                    if isinstance(widget, (ttk.Button, ttk.Combobox, ttk.Entry)):
                        self._prepare_controls.append((widget, str(widget.cget("state"))))
                        widget.configure(state="disabled")
                    walk(widget)
            walk(self)
        else:
            for widget, state in getattr(self, "_prepare_controls", []):
                if widget.winfo_exists():
                    widget.configure(state=state)

    def _create_task(self, kind: str, photo_ids=None, *, submit_after=False, reuse_unchanged=False) -> None:
        if self._api_active or self._preparing_task:
            return
        eligible = [a for a in self.assets if not _asset_value(a, "auto_rejected", False)]
        if photo_ids == []:
            self.status_var.set("没有可提交 AI 的照片；初筛弃置结果仍可导出到 LR。")
            return
        # Collect retired Tk variable/image cycles on the Tk thread before CPU work.
        import gc
        gc.collect()
        preferences = self._preferences()
        self._set_preparing(True)
        self.status_var.set("正在准备本轮联系表，请稍候…")
        def work():
            try:
                signature = self.project.home_sheet_signature(self.assets, self.crop_settings, self.home_pages)
                if reuse_unchanged and self.project.can_reuse_task(signature):
                    self._api_queue.put(("prepared", self.project.current_task(), False, True))
                    return
                task = self.project.create_task(self.assets, self.crop_settings, preferences,
                    kind=kind, photo_ids=photo_ids, replace_current=(kind == "initial" and photo_ids is None),
                    home_signature=signature)
                self._api_queue.put(("prepared", task, submit_after))
            except Exception as exc:
                self._api_queue.put(("prepare_error", str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _resubmit_api(self):
        self._refresh_profiles()
        if self._api_active or self._preparing_task:
            return
        if not self._profile_labels.get(self.profile_var.get()):
            messagebox.showinfo("重新提交", "请在主页面配置并选择 API；也可以使用网页选片。", parent=self)
            return
        self._create_task("initial", submit_after=True)


    def _create_refine(self) -> None:
        ids: list[str] = []
        for photo_id, photo in self._photos().items():
            if photo.get("stale"):
                continue
            final = photo.get("final") or {}
            ai = photo.get("ai") or {}
            if final.get("confirmed"):
                selected = (final.get("rating") or 0) >= 4 and final.get("pick_status") != -1
            else:
                selected = (ai.get("rating") or 0) >= 4
            if selected:
                ids.append(photo_id)
        self._create_task("refine", ids)

    def _split_selected_batch(self) -> None:
        if self._api_active:
            messagebox.showinfo("拆小本批", "请先等待当前 API 请求结束或暂停。", parent=self)
            return
        task, batch = self._current_task(), self._selected_batch()
        if not task or not batch:
            messagebox.showinfo("拆小本批", "请先选择需要拆分的批次。", parent=self)
            return
        photo_ids = list(batch.get("photo_ids", []))
        if len(photo_ids) <= 1:
            messagebox.showinfo("拆小本批", "本批只有一张照片，无法继续拆分。", parent=self)
            return
        preferences = dict(task.get("preferences") or self._preferences())
        preferences["_split_limit"] = max(1, len(photo_ids) // 2)
        try:
            created = self.project.create_task(
                self.assets,
                self.crop_settings,
                preferences,
                kind=str(task.get("kind", "initial")),
                photo_ids=photo_ids,
            )
            self.project.save()
        except Exception as exc:
            messagebox.showerror("拆分失败", str(exc), parent=self)
            return
        self._refresh_tasks(selected_id=_task_id(created))
        self.status_var.set(f"已把批次 {_batch_id(batch)} 拆成更小的新任务；原任务记录仍保留。")


    # ---- manual and API submission ------------------------------------

    def _batch_images(self, task: dict[str, Any], batch: dict[str, Any]) -> list[Path]:
        return [Path(path) for path in self.project.batch_images(task, batch)]


    def _show_raw_responses(self) -> None:
        batch = self._selected_batch()
        if not batch:
            messagebox.showinfo("原始回答", "请先选择批次。", parent=self)
            return
        RawResponsesDialog(self, batch.get("raw_responses", []))

    def _refresh_profiles(self, *_args: Any) -> None:
        self._profile_labels.clear()
        try:
            getter = getattr(self.master, "_selected_api_profile", None)
            if getter:
                profile = getter()
            else:
                from .settings import read_values
                from .ai_api import load_profiles
                selected = read_values(self.settings_dir).get("selected_api_profile_id")
                profile = next((p for p in load_profiles(self.settings_dir) if p.get("id") == selected), None)
            if profile:
                label = f"{profile.get('name', 'API')} · {profile.get('model', '')}"
                self._profile_labels[label] = copy.deepcopy(profile)
                self.profile_var.set(label)
            else:
                self.profile_var.set("未启用 API；可使用网页选片")
        except Exception as exc:
            self.profile_var.set("API 配置读取失败")
            self.status_var.set(str(exc))

    def _start_api(self) -> None:
        if self._api_active or self._preparing_task:
            return
        self._refresh_profiles()
        task = self._current_task()
        profile = self._profile_labels.get(self.profile_var.get())
        if not task:
            messagebox.showinfo("API 提交", "请先创建或选择任务。", parent=self)
            return
        if not profile:
            messagebox.showinfo("API 提交", "请在主页面配置并选择 API；也可以使用网页选片。", parent=self)
            return
        pending: list[dict[str, Any]] = []
        try:
            for batch in task.get("batches", []):
                if _is_done(batch):
                    continue
                images = self._batch_images(task, batch)
                pending.append({
                    "task": task,
                    "task_id": _task_id(task),
                    "batch": batch,
                    "batch_id": _batch_id(batch),
                    "profile": copy.deepcopy(profile),
                    "prompt": str(self.project.prompt(task, batch)),
                    "image_paths": tuple(str(path) for path in images),
                    "photo_count": len(batch.get("photo_ids", [])),
                })
        except Exception as exc:
            messagebox.showerror("无法准备 API 请求", str(exc), parent=self)
            return
        if not pending:
            messagebox.showinfo("API 提交", "此任务的批次均已完成，不会重复提交。", parent=self)
            return
        photo_count = sum(item["photo_count"] for item in pending)
        image_count = sum(len(item["image_paths"]) for item in pending)
        summary = (
            f"配置：{profile.get('name', profile.get('id', '未命名'))}\n"
            f"接口：{profile.get('base_url', '')}\n"
            f"模型：{profile.get('model', '')}\n\n"
            f"将提交 {len(pending)} 个未完成批次，涉及 {photo_count} 张照片、{image_count} 张批次图片。\n"
            "确认开始联网提交吗？"
        )
        if not messagebox.askyesno("确认 API 提交范围", summary, parent=self):
            return
        self._api_pending = pending
        self._pause_requested = False
        self._api_active = True
        self.run_button.configure(state="disabled")
        self.pause_button.configure(state="normal")
        self.resubmit_button.configure(state="disabled")
        self._start_next_api_batch()

    def _start_next_api_batch(self) -> None:
        if self._pause_requested or not self._api_pending:
            self._finish_api("已暂停，可稍后继续未完成批次。" if self._pause_requested else "API 提交完成。")
            return
        request = self._api_pending.pop(0)
        batch = request["batch"]
        batch["api_profile"] = {
            key: request["profile"].get(key)
            for key in ("id", "name", "base_url", "model", "timeout", "max_tokens", "preset_id")
        }
        batch["status"] = "running"
        batch.pop("error", None)
        try:
            self.project.save()
        except Exception as exc:
            self._finish_api(f"保存批次状态失败：{exc}")
            return
        self._refresh_batches(select_id=request["batch_id"])
        self.status_var.set(f"正在提交批次 {request['batch_id']}…")

        def work(snapshot: dict[str, Any]) -> None:
            try:
                from .ai_api import call_model

                result = call_model(
                    snapshot["profile"], snapshot["prompt"],
                    [Path(path) for path in snapshot["image_paths"]],
                )
                if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                    raise ValueError("API 返回缺少文本结果")
                self._api_queue.put(("success", snapshot["task_id"], snapshot["batch_id"], result.get("text", ""), result.get("usage")))
            except Exception as exc:
                self._api_queue.put(("error", snapshot["task_id"], snapshot["batch_id"], str(exc)))

        immutable = {key: request[key] for key in ("task_id", "batch_id", "profile", "prompt", "image_paths")}
        threading.Thread(target=work, args=(immutable,), daemon=True).start()

    def _pause_api(self) -> None:
        if self._api_active:
            self._pause_requested = True
            self.pause_button.configure(state="disabled")
            self.status_var.set("已请求暂停；当前批次完成后不会提交下一批。")

    def _poll_api(self) -> None:
        try:
            while True:
                event = self._api_queue.get_nowait()
                self._handle_api_event(event)
        except queue.Empty:
            pass
        try:
            if self.winfo_exists():
                self._poll_token = self.after(120, self._poll_api)
        except tk.TclError:
            self._poll_token = None

    def _find_task_batch(self, task_id: str, batch_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        task = next((item for item in self._tasks() if _task_id(item) == task_id), None)
        batch = next((item for item in task.get("batches", []) if _batch_id(item) == batch_id), None) if task else None
        return task, batch

    def _handle_api_event(self, event: tuple[Any, ...]) -> None:
        if event[0] in ("web_prepared", "web_prepare_error"):
            self._set_preparing(False)
            if self._closing_requested:
                self._save_ui_settings()
                self._destroy_now()
            elif event[0] == "web_prepared":
                self._refresh_web(event[1])
                self._copy_web_prompt()
                self._open_web_folder()
            else:
                self.status_var.set("准备失败：" + event[1])
                messagebox.showerror("准备失败", event[1], parent=self)
            return
        if event[0] in ("prepared", "prepare_error"):
            self._set_preparing(False)
            if event[0] == "prepared":
                self._refresh_tasks()
                self._refresh_review()
                self._refresh_export_status()
                self.status_var.set("联系表未变化，已恢复上次任务和选片结果。" if len(event)>3 and event[3] else f"本轮已准备好，共 {len(event[1]['batches'])} 批。")
            else:
                self.status_var.set("准备失败：" + event[1])
                if not self._closing_requested:
                    messagebox.showerror("准备 AI 选片失败", event[1], parent=self)
            if self._closing_requested:
                self._save_ui_settings()
                self._destroy_now()
            elif event[0] == "prepared" and event[2]:
                self._start_api()
            return
        kind, task_id, batch_id = event[:3]
        task, batch = self._find_task_batch(task_id, batch_id)
        if not task or not batch:
            self._finish_api("任务状态已变化，已停止提交。")
            return
        if kind == "success":
            text, usage = event[3], event[4]
            try:
                issues = self.project.ingest(task, batch, text)
                if usage is not None:
                    batch["usage"] = usage
                if batch.get("raw_responses"):
                    response = batch["raw_responses"][-1]
                    if isinstance(response, dict):
                        response["api_profile"] = copy.deepcopy(batch.get("api_profile", {}))
                        response["usage"] = usage if usage is not None else {}
                self.project.save()
            except Exception as exc:
                batch["status"] = "failed"
                batch["error"] = f"返回结果保存失败：{exc}"
                self._safe_save()
                self._finish_api(f"批次 {batch_id} 保存失败，已停止：{exc}")
                self._refresh_batches(select_id=batch_id)
                return
            self._refresh_batches(select_id=batch_id)
            self._refresh_review()
            if issues:
                self.status_var.set(f"批次 {batch_id} 已保存，发现 {len(issues)} 项异常；继续下一批。")
            self._start_next_api_batch()
        else:
            error = str(event[3])
            batch["status"] = "failed"
            batch["error"] = error
            self._safe_save()
            self._refresh_batches(select_id=batch_id)
            self._finish_api(f"批次 {batch_id} 失败，已停止；修正后可继续未完成批次。")
            if not self._closing_requested:
                hint = ""
                if "413" in error or "大小" in error or "图片" in error:
                    hint = "\n\n可选择“拆分所选批次重试”创建更小批次；软件不会自动重复请求。"
                messagebox.showerror("API 批次失败", f"批次 {batch_id}：{error}{hint}", parent=self)

    def _safe_save(self) -> None:
        try:
            self.project.save()
        except Exception as exc:
            self.status_var.set(f"项目保存失败：{exc}")

    def _finish_api(self, message: str) -> None:
        self._api_active = False
        self._api_pending.clear()
        self.run_button.configure(state="normal")
        self.pause_button.configure(state="disabled")
        self.resubmit_button.configure(state="normal")
        self.status_var.set(message)
        if self._closing_requested:
            self._save_ui_settings()
            self._destroy_now()

    # ---- human review --------------------------------------------------
    def _matches_filter(self, photo: dict[str, Any]) -> bool:
        selected = self.filter_var.get()
        ai = photo.get("ai") or {}
        final = photo.get("final") or {}
        if selected == "清晰度待确认":
            return photo.get("focus_review") is True
        if selected == "尚未确认":
            return not bool(final.get("confirmed"))
        if selected == "待复核":
            return bool(ai.get("review_items"))
        if selected == "AI 建议弃置":
            return ai.get("suggest_reject") is True
        if selected == "4～5 星":
            return isinstance(ai.get("rating"), int) and ai["rating"] >= 4
        if selected == "结果已过时":
            return photo.get("stale") is True
        if selected == "回答缺失或异常":
            return bool(photo.get("error")) or not isinstance(photo.get("ai"), dict) or not photo.get("ai")
        return True

    def _asset_for_photo(self, photo: dict[str, Any]) -> Any | None:
        stem = str(photo.get("stem", ""))
        if stem in self._asset_by_stem:
            return self._asset_by_stem[stem]
        path = photo.get("path")
        if path:
            wanted = str(Path(path))
            return next((asset for asset in self.assets if str(_asset_value(asset, "primary_path", _asset_value(asset, "path", ""))) == wanted), None)
        return None

    def _preview_path(self, photo: dict[str, Any]) -> Path | None:
        value = photo.get("preview_path")
        if value:
            return Path(value)
        asset = self._asset_for_photo(photo)
        value = _asset_value(asset, "preview_path") if asset is not None else None
        return Path(value) if value else None

    def _make_thumb(self, photo: dict[str, Any], size: tuple[int, int]) -> ImageTk.PhotoImage | None:
        path = self._preview_path(photo)
        if not path:
            return None
        try:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail(size, Image.Resampling.LANCZOS)
                tile = Image.new("RGB", size, "#eeeeee")
                tile.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
            return ImageTk.PhotoImage(tile, master=self)
        except (OSError, ValueError):
            return None

    def _refresh_review(self, select_id: str | None = None) -> None:
        old = select_id or self._selected_photo_id()
        self.review_tree.delete(*self.review_tree.get_children())
        self._photo_ids.clear()
        for photo_id, photo in self._photos().items():
            if not self._matches_filter(photo):
                continue
            ai = photo.get("ai") or {}
            focus_result = photo.get("ai_focus_result") or {}
            focus_blur = focus_result.get("status") == "blur"
            clarity = "清晰度待确认" if photo.get("focus_review") else {"clear": "AI 复查清楚", "blur": "AI 复查模糊"}.get(focus_result.get("status"), "已初筛" if photo.get("focus_review") is False else "未检查")
            technical = bool(photo.get("technical_reason"))
            flag = "是（AI 复查）" if focus_blur else "是（初筛）" if technical else ("是" if ai.get("suggest_reject") else "否" if ai else "待选片")
            reason = focus_result.get("reason", "AI 复查模糊") if focus_blur else ("主体虚焦／抖动，未提交 AI" if technical else (ai.get("reason") or "尚无 AI 回复"))
            self.review_tree.insert("", "end", iid=str(photo_id), values=(
                photo.get("stem") or Path(str(photo.get("path", ""))).stem,
                str(photo.get("group_id", "")),
                "" if ai.get("rating") is None or technical else ai.get("rating"),
                flag, reason, clarity), tags=("stale",) if photo.get("stale") else ())
            self._photo_ids.append(str(photo_id))
        self.review_tree.tag_configure("stale", foreground="#b05a00")
        if old and old in self._photo_ids:
            self.review_tree.selection_set(old)
            self.review_tree.see(old)
        elif self._photo_ids:
            self.review_tree.selection_set(self._photo_ids[0])
        if self.notebook.index(self.notebook.select()) == 2:
            self._show_selected_photo()

    def _selected_photo_id(self) -> str | None:
        if not hasattr(self, "review_tree"):
            return None
        selected = self.review_tree.selection()
        return selected[0] if selected else None

    def _show_selected_photo(self, _event: Any = None) -> None:
        if self.notebook.index(self.notebook.select()) != 2:
            return
        photo_id = self._selected_photo_id()
        photo = self._photos().get(photo_id or "")
        if not photo:
            self.review_caption_var.set("当前筛选没有照片")
            self.preview_label.configure(image="", text="无预览")
            self._preview_photo = None
            self._set_details("")
            return
        ai = {} if photo.get("technical_reason") else (photo.get("ai") or {})
        self.review_caption_var.set(f"{photo.get('stem', photo_id)}  ·  分组 {photo.get('group_id', '')}{'  ·  结果已过时' if photo.get('stale') else ''}")
        review = "；".join(map(str, ai.get("review_items") or [])) or "无"
        details = (
            f"AI 建议：{'未评分' if ai.get('rating') is None else str(ai.get('rating')) + ' 星'}"
            f"；{'建议弃置' if ai.get('suggest_reject') else '未建议弃置'}\n"
            f"理由：{ai.get('reason') or '无 AI 回答'}\n"
            f"待复核：{review}\n"
            f"技术检查：{photo.get('technical_reason') or '无'}\n"
            f"解析异常：{photo.get('error') or '无'}"
        )
        focus_result = photo.get("ai_focus_result") or {}
        if focus_result:
            focus_label = {"clear": "清楚", "blur": "模糊，已弃置", "uncertain": "清晰度待确认"}.get(focus_result.get("status"), "清晰度待确认")
            details += f"\n清晰度复查：{focus_label}\n复查理由：{focus_result.get('reason', '')}"
        elif photo.get("focus_review"):
            details += "\n清晰度待确认"
        self._set_details(details)
        self.update_idletasks()
        width = max(100, min(420, self.preview_label.winfo_width()))
        height = max(80, min(300, self.preview_label.winfo_height()))
        thumb = self._make_thumb(photo, (width, height))
        self._preview_photo = thumb
        self.preview_label.configure(image=thumb or "", text="" if thumb else "预览不可用")

    def _set_details(self, value: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", value)
        self.details.configure(state="disabled")


    def _navigate_photo(self, step: int) -> None:
        if not self._photo_ids:
            return
        selected = self._selected_photo_id()
        index = self._photo_ids.index(selected) if selected in self._photo_ids else 0
        target = self._photo_ids[(index + step) % len(self._photo_ids)]
        self.review_tree.selection_set(target)
        self.review_tree.focus(target)
        self.review_tree.see(target)
        self._show_selected_photo()

    def _open_original(self) -> None:
        photo_id = self._selected_photo_id()
        photo = self._photos().get(photo_id or "")
        if not photo:
            return
        path = photo.get("path")
        if not path:
            asset = self._asset_for_photo(photo)
            path = _asset_value(asset, "primary_path") if asset is not None else None
        try:
            if not path or not Path(path).exists():
                raise FileNotFoundError(f"原图不存在：{path or '未记录路径'}")
            os.startfile(str(Path(path)))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("打开原图失败", str(exc), parent=self)

    def _export_ai_ratings(self) -> None:
        try:
            self.project.refresh(self.assets, self.crop_settings)
        except Exception as exc:
            messagebox.showerror("刷新照片失败", str(exc), parent=self)
            return
        photos = list(_state(self.project).get("photos", {}).values())
        current = [photo for photo in photos if not photo.get("stale")]
        ai_rated = 0
        technical_rejected = 0
        unscored = 0
        for photo in current:
            ai = photo.get("ai") or {}
            rating = ai.get("rating")
            has_ai_rating = (
                ai.get("fingerprint") == photo.get("fingerprint")
                and type(rating) is int
                and 1 <= rating <= 5
            )
            ai_rated += int(has_ai_rating)
            technical_rejected += int(bool(photo.get("technical_reason")))
            unscored += int(not has_ai_rating)
        if unscored:
            summary = (
                f"当前有效照片共 {len(current)} 张：\n"
                f"• AI 已评分：{ai_rated} 张\n"
                f"• 技术筛选弃置：{technical_rejected} 张\n"
                f"• 尚无 AI 评分：{unscored} 张\n\n"
                "技术筛选弃置为独立统计，可能同时出现在已评分或未评分数量中。\n"
                "继续导出时，未评分照片不会写入 AI 星级；其中已有技术筛选弃置结果的照片仍会导出弃置标记。\n\n"
                "是否导出当前已有结果？选择“否”可返回继续选片。"
            )
            if not messagebox.askyesno("仍有照片未评分", summary, parent=self):
                self.status_var.set("已取消导出，可继续完成 AI 选片。")
                return
        try:
            path = Path(self.project.export_final(ai_ratings=True))
            count = len(_state(self.project).get("last_export_rows", []))
            os.startfile(str(path.parent))
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self._refresh_export_status()
        messagebox.showinfo("到 Lightroom 复核", f"已导出 {count} 张结果到：\n{path}\n\n在 Lightroom 插件中导入此文件，再查看原图调整星级。\n优先使用已人工确认结果，其余合并导出 AI 星级、AI 弃置建议和技术筛选弃置。在 Lightroom 中查看原图复核。", parent=self)


    def _refresh_export_status(self) -> None:
        self.export_status_var.set("导出状态：" + str(_state(self.project).get("export_status", "未导出")))

    # ---- lifetime ------------------------------------------------------
    def _close(self) -> None:
        if self._preparing_task:
            self._closing_requested = True
            self.status_var.set("正在保存本轮任务，完成后关闭…")
            return
        if self._api_active:
            self._pause_requested = True
            self._closing_requested = True
            self.pause_button.configure(state="disabled")
            messagebox.showinfo("正在完成当前请求", "已请求暂停。当前 API 请求完成后窗口会关闭，不会提交下一批。", parent=self)
            return
        self._save_ui_settings()
        self._destroy_now()

    def _destroy_now(self) -> None:
        if self._poll_token:
            try:
                self.after_cancel(self._poll_token)
            except tk.TclError:
                pass
            self._poll_token = None
        self.grab_release()
        super().destroy()
