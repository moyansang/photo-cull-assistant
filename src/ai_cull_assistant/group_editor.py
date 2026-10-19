from __future__ import annotations

from collections import Counter, OrderedDict
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
from itertools import count
from queue import Empty, SimpleQueue
from threading import Lock
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from PIL import Image, ImageOps, ImageTk

from .grouping import merge_adjacent_groups, split_group_at
from .models import PhotoAsset
from .ui_help import install_control_help, install_page_chrome
from .ui_page import WorkspacePage
from .ui_style import apply_page, set_button_style, COLORS, initialize
from .window_layout import fit_window


def grouped_assets(assets: list[PhotoAsset]) -> list[tuple[int, list[PhotoAsset]]]:
    """Coalesce noncontiguous workspace-wide groups in first-appearance order."""
    groups: dict[int, list[PhotoAsset]] = {}
    for asset in assets:
        groups.setdefault(asset.group_id, []).append(asset)
    return list(groups.items())



def grouping_recommendations(members: list[PhotoAsset]) -> dict:
    """Use the shared current-evidence gate, never revive stale cached verdicts."""
    from .lightroom_results import local_recommendation
    result = {}
    for asset in members:
        evidence = asset.clarity_evidence if isinstance(asset.clarity_evidence, dict) else {}
        recommendation = local_recommendation(asset)
        if evidence.get('reliability') != 'adequate':
            recommendation = 'review'
        result[str(asset.primary_path)] = {'recommendation': recommendation,
                                          'recommended': recommendation == 'keep'}
    keep_count = sum(item['recommended'] for item in result.values())
    for item in result.values():
        item['tied'] = item['recommended'] and keep_count > 1
    return result

def local_recommendation_summary(members: list[PhotoAsset], selected: PhotoAsset | None = None) -> str:
    """Format current cached local evidence; never decode or change decisions."""
    recommendations = grouping_recommendations(members)
    count = sum(bool(item['recommended']) for item in recommendations.values())
    summary = f"本地推荐保留 {count} 张" if count else "本地暂无保留推荐"
    if count > 1:
        summary += "（并列）"
    if selected is not None:
        item = recommendations.get(str(selected.primary_path), {})
        label = {'keep': '推荐保留', 'reject': '推荐弃置', 'review': '待复核'}.get(item.get('recommendation'), '待复核')
        summary += f" · 当前本地：{label}"
    return summary



def grouping_reason_items(asset: PhotoAsset) -> list[str]:
    """Read existing reasons only. Unknown codes stay visible, never inferred."""
    from .lightroom_results import focus_review_reason
    evidence = asset.clarity_evidence if isinstance(asset.clarity_evidence, dict) else {}
    policy = evidence.get('primary_quality', {})
    scoped = evidence.get('reasons_by_part') or (policy.get('reasons_by_part') if isinstance(policy, dict) else None)
    entries = []
    labels = {'selection': '主体选择', 'face': '面部', 'body': '身体', 'measurement': '测量'}
    if isinstance(scoped, dict):
        for part, codes in scoped.items():
            if isinstance(codes, list):
                entries.extend((labels.get(part, str(part)), code) for code in codes)
    else:
        codes = evidence.get('reasons', [])
        if isinstance(codes, list):
            entries = [('', code) for code in codes]
    additional_labels = {
        'face_visibility_unverified': '无法可靠确认面部可见性，请检查原图；不代表已确认遮挡。',
        'face_region_outside_source': '面部检查区域超出原图有效范围，无法可靠测量。',
        'body_region_ownership_unresolved': '身体区域所属人物未能可靠确认。',
    }
    result = []
    for part, code in entries:
        if not isinstance(code, str) or not code.strip():
            continue
        formatted = additional_labels.get(code) or focus_review_reason({'clarity_evidence': {'reasons': [code]}})
        if formatted == '清晰度证据不足，需要检查原图。':
            formatted = f'原始原因：{code}'
        if part:
            formatted = f'{part}：{formatted}'
        if formatted not in result:
            result.append(formatted)
    # Scoped evidence is authoritative; do not re-mix legacy body/face reasons.
    if not result and (asset.screening_reason or asset.person_review_pending):
        result.append(focus_review_reason(asset))
    return result


def grouping_review_stage(asset: PhotoAsset) -> str:
    """Describe saved reason scope without claiming a fresh human confirmation."""
    evidence = asset.clarity_evidence if isinstance(asset.clarity_evidence, dict) else {}
    policy = evidence.get('primary_quality', {})
    policy = policy if isinstance(policy, dict) else {}
    codes = set()
    scoped = evidence.get('reasons_by_part') or policy.get('reasons_by_part')
    if isinstance(scoped, dict):
        for values in scoped.values():
            if isinstance(values, list):
                codes.update(code for code in values if isinstance(code, str))
    elif isinstance(policy.get('issues'), list):
        codes.update(issue['reason'] for issue in policy['issues']
                     if isinstance(issue, dict) and isinstance(issue.get('reason'), str))
    else:
        legacy = evidence.get('reasons', [])
        if isinstance(legacy, list):
            codes.update(code for code in legacy if isinstance(code, str))
    if getattr(asset, 'ai_focus_dirty', False) or 'measurement_confirmation_mismatch' in codes:
        return '需重新扫描：范围或源文件可能已改变，旧证据不能代表当前结果。'
    if 'manual_face_scope_ambiguous' in codes:
        return '所选人脸框内有多个人脸：请缩小或分别框选要检查的人物，再保存重算。'
    if 'primary_selection_unconfirmed' in codes:
        return '主体范围待指定或重新确认：请选择本次要检查的人物。'
    if policy.get('recommendation') == 'review' or (evidence.get('recommendation') == 'review' and codes):
        return '清晰度或局部证据待复核：选好人物也不保证能够可靠测量，请检查具体原因。'
    return '主体确认与清晰度是两件事；本页只显示已保存证据。'


def grouping_evidence_text(asset: PhotoAsset, recommendation: str) -> str:
    """Separate read-only technical advice from decisions held elsewhere."""
    import json
    label = {'keep': '建议保留', 'reject': '建议弃置', 'review': '待复核'}.get(recommendation, '待复核')
    evidence = asset.clarity_evidence if isinstance(asset.clarity_evidence, dict) else {}
    reliability = {'adequate': '充分', 'insufficient': '不足'}.get(evidence.get('reliability'), '未记录')
    lines = [asset.stem, '', f'本地技术建议：{label}', f'已保存证据充分性：{reliability}',
             '人工决定：本页未读取，请在选片页查看', '', grouping_review_stage(asset), '', '本地技术原因']
    if getattr(asset, 'ai_focus_stale', False):
        lines.append('旧 AI 复核已失效；不表示本地测量也已过期，以下展示已保存本地证据。')
    if evidence.get('recommendation') in {'keep', 'reject'} and recommendation == 'review':
        lines.append('缓存建议未获当前校验，以待复核为准；以下仅为已保存证据。')
    reasons = grouping_reason_items(asset)
    lines.extend(f'{i}. {reason}' for i, reason in enumerate(reasons, 1))
    if not reasons:
        lines.append('未记录具体原因。')
    lines.extend(['', '原图检查', '双击照片可打开全屏查看器。',
                  '本页未建立可靠坐标映射，不绘制推测区域。', '', '选择检查人物',
                  '入口只打开当前照片的框选页，不会确认、保存或重扫。',
                  '在框选页明确确认并保存后，会按现有流程重扫并重判自动分组；人工分组保留。'])
    if evidence:
        lines.extend(['', '已保存的原始证据（只读）', json.dumps(evidence, ensure_ascii=False, indent=2, default=str)])
    return '\n'.join(lines)

def visible_group_range(count: int, viewport_top: int, viewport_height: int, row_height: int) -> range:
    """Return visible row indexes plus one buffer row on either side."""
    first = max(0, viewport_top // row_height - 1)
    last = min(count, (viewport_top + max(row_height, viewport_height)) // row_height + 2)
    return range(first, last)


def ellipsize_text(text: str, measure: Callable[[str], int], max_width: int) -> str:
    """Fit one line to a pixel width while keeping the start and end useful."""
    if not text or measure(text) <= max_width:
        return text
    marker = "…"
    if max_width <= measure(marker):
        return marker
    low, high = 0, len(text)
    while low < high:
        keep = (low + high + 1) // 2
        left = (keep + 1) // 2
        candidate = text[:left] + marker + text[-(keep - left):] if keep > left else text[:left] + marker
        if measure(candidate) <= max_width:
            low = keep
        else:
            high = keep - 1
    left = (low + 1) // 2
    return text[:left] + marker + text[-(low - left):] if low > left else text[:left] + marker


def detail_grid_layout(width: int, height: int, photo_count: int, caption_height: int):
    """Fit small groups to the viewport; keep large groups readable by scrolling."""
    width, height = max(40, width), max(60, height)
    count = max(1, photo_count)
    overhead = caption_height + 26
    if count <= 2:
        columns = count
        row_height = height - 8
    else:
        candidates = []
        for columns in range(1, min(count, max(1, width // 160)) + 1):
            rows = (count + columns - 1) // columns
            row_height = (height - 8) // rows
            score = min(width // columns - 22, row_height - overhead)
            candidates.append((score, -rows, columns, row_height))
        score, _, columns, row_height = max(candidates)
        if score < 120:
            columns = min(count, max(1, width // 240))
            row_height = max(180, min(320, width // columns))
    cell_width = width // columns
    # Reuse cached thumbnails during small resize movements; cap pixel storage.
    box = tuple(max(8, min(768, value // 8 * 8)) for value in
                (cell_width - 22, row_height - overhead))
    return columns, cell_width, row_height, box


ThumbnailKey = tuple[int, tuple[int, int]]


def load_thumbnail(asset: PhotoAsset, box: tuple[int, int]) -> Image.Image | None:
    """Decode and resize a thumbnail without creating any Tk objects."""
    try:
        from .preview import ensure_preview

        with Image.open(ensure_preview(asset)) as img:
            oriented = ImageOps.exif_transpose(img).convert("RGB")
            return ImageOps.contain(oriented, box)
    except Exception:
        return None


@dataclass
class _ThumbnailRequest:
    token: int
    asset: PhotoAsset
    box: tuple[int, int]
    future: Future[Image.Image | None] | None = None


class ThumbnailLoader:
    """A bounded worker pool whose completion queue is safe to poll from Tk."""

    def __init__(
        self,
        *,
        max_workers: int = 2,
        max_pending: int = 24,
        worker: Callable[[PhotoAsset, tuple[int, int]], Image.Image | None] = load_thumbnail,
    ) -> None:
        self.max_pending = max_pending
        self._max_workers = max_workers
        self._worker = worker
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="group-thumb")
        self._pending: dict[ThumbnailKey, _ThumbnailRequest] = {}
        self._active_assets: set[int] = set()
        self._completed: SimpleQueue[tuple[ThumbnailKey, int, Image.Image | None]] = SimpleQueue()
        self._tokens = count()
        self._state_lock = Lock()
        self._closed = False

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def request(self, key: ThumbnailKey, asset: PhotoAsset, box: tuple[int, int]) -> bool:
        if self._closed or key in self._pending or len(self._pending) >= self.max_pending:
            return False
        self._pending[key] = _ThumbnailRequest(next(self._tokens), asset, box)
        self._dispatch()
        return True

    def _dispatch(self) -> None:
        if self._closed:
            return
        active_count = sum(request.future is not None for request in self._pending.values())
        for key, request in self._pending.items():
            if active_count >= self._max_workers:
                break
            asset_key = id(request.asset)
            if request.future is not None or asset_key in self._active_assets:
                continue
            self._active_assets.add(asset_key)
            future = self._executor.submit(self._worker, request.asset, request.box)
            request.future = future
            future.add_done_callback(
                lambda done, k=key, t=request.token: self._collect(k, t, done)
            )
            active_count += 1

    def _collect(self, key: ThumbnailKey, token: int, future: Future[Image.Image | None]) -> None:
        try:
            image = future.result()
        except CancelledError:
            image = None
        except Exception:
            image = None
        with self._state_lock:
            if self._closed:
                if image is not None:
                    image.close()
                return
            self._completed.put((key, token, image))

    def pop_completed(self) -> list[tuple[ThumbnailKey, Image.Image | None]]:
        ready: list[tuple[ThumbnailKey, Image.Image | None]] = []
        while True:
            try:
                key, token, image = self._completed.get_nowait()
            except Empty:
                break
            current = self._pending.get(key)
            if current is None or current.token != token:
                if image is not None:
                    image.close()
                continue
            self._active_assets.discard(id(current.asset))
            del self._pending[key]
            ready.append((key, image))
        self._dispatch()
        return ready

    def cancel_except(self, wanted: set[ThumbnailKey]) -> None:
        for key, request in list(self._pending.items()):
            if key in wanted:
                continue
            if request.future is None or request.future.cancel():
                if request.future is not None:
                    self._active_assets.discard(id(request.asset))
                del self._pending[key]
        self._dispatch()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        for request in self._pending.values():
            if request.future is not None:
                request.future.cancel()
        self._pending.clear()
        self._active_assets.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)
        for _key, image in self.pop_completed():
            if image is not None:
                image.close()


class GroupEditor(WorkspacePage):
    ROW_H = 62
    THUMB_CACHE_SIZE = 160
    THUMB_POLL_MS = 20

    def __init__(
        self,
        parent: tk.Misc,
        assets: list[PhotoAsset],
        on_change,
        *,
        embedded: bool = False,
        on_select_subject=None,
    ) -> None:
        super().__init__(parent, embedded=embedded)
        if not embedded:
            self.withdraw()
            self.title("选片组编辑")
            self.transient(parent)
        self.assets = assets
        self.on_change = on_change
        self.on_select_subject = on_select_subject
        self._selected_subject_asset = None
        self.selected_group_id: int | None = assets[0].group_id if assets else None
        self.selected_stem: str | None = None
        self._row_images: list[ImageTk.PhotoImage] = []
        self._detail_images: list[ImageTk.PhotoImage] = []
        self._thumb_cache: OrderedDict[tuple[int, tuple[int, int]], ImageTk.PhotoImage | None] = OrderedDict()
        self._thumbnail_loader = ThumbnailLoader()
        self._row_requested_keys: set[ThumbnailKey] = set()
        self._detail_requested_keys: set[ThumbnailKey] = set()
        self._thumb_poll_token: str | None = None
        self._closed = False
        self._inspector_override = False
        self._groups: list[tuple[int, list[PhotoAsset]]] = grouped_assets(self.assets)
        self._members_by_group = {group_id: members for group_id, members in self._groups}
        self._redraw_token: str | None = None
        self._detail_redraw_token: str | None = None
        if not embedded:
            install_page_chrome(self, "groups")
        initialize(self)
        root = self
        while root.master is not None:
            root = root.master
        self._fonts = root._cull_fonts
        fonts = self._fonts
        self._group_row_h = max(
            self.ROW_H,
            fonts['body'].metrics('linespace') + fonts['shortcut'].metrics('linespace') + 25,
        )
        self._build_ui()
        if not embedded:
            fit_window(self, (1180, 680), minimum_size=(640, 480), parent=parent)
        self._redraw_rows()
        self._redraw_detail()
        install_control_help(self, "groups")
        apply_page(self)

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(10, 6, 10, 4))
        header.pack(side="top", fill="x")
        ttk.Label(header, text="编辑选片组", style="Heading.TLabel").pack(side="left")
        self.status_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.status_var, style="Muted.TLabel").pack(side="right")

        actions = ttk.Frame(self, padding=(10, 4, 10, 8))
        actions.pack(side="bottom", fill="x")
        ttk.Button(actions, text="返回主页" if self._embedded else "关闭",
                   command=self.return_home).pack(side="right")
        ttk.Button(actions, text="从所选照片拆分", command=self._split_selected).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="与上一组合并", command=lambda: self._merge_neighbor(-1)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="与下一组合并", command=lambda: self._merge_neighbor(1)).pack(side="left", padx=(0, 8))

        filters = ttk.Frame(self, padding=(10, 0, 10, 5))
        filters.pack(side="top", fill="x")
        ttk.Label(filters, text="当前组筛选", style="Muted.TLabel").pack(side="left")
        self.filter_var = tk.StringVar(value="全部")
        selector = ttk.Combobox(filters, textvariable=self.filter_var, state="readonly", width=12,
                                values=("全部", "待复核", "建议保留", "建议弃置"))
        selector.pack(side="left", padx=8)
        selector.bind("<<ComboboxSelected>>", self._filter_changed)
        self.filter_count_var = tk.StringVar()
        ttk.Label(filters, textvariable=self.filter_count_var, style="Muted.TLabel").pack(side="left")
        self.inspector_button = ttk.Button(filters, text="收起证据", command=self._toggle_inspector)
        self.inspector_button.pack(side="right")
        self.subject_button = ttk.Button(filters, text="选择检查人物", command=self._choose_subject_for_photo, state="disabled")
        self.subject_button.pack(side="right", padx=(0, 8))

        content = ttk.Frame(self, padding=(10, 0, 10, 6))
        content.pack(side="top", fill="both", expand=True)
        self.content = content
        content.columnconfigure(0, minsize=190)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        groups_frame = ttk.LabelFrame(content, text="选片组", padding=6)
        groups_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        groups_frame.columnconfigure(0, weight=1)
        groups_frame.rowconfigure(0, weight=1)
        self.rows_canvas = tk.Canvas(groups_frame, width=190, highlightthickness=0, background=COLORS["bg"])
        ybar = ttk.Scrollbar(groups_frame, orient="vertical", command=self._scroll_rows)
        self.rows_canvas.configure(yscrollcommand=ybar.set)
        self.rows_canvas.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        self.rows_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.rows_canvas.bind("<Configure>", lambda _event: self._schedule_rows_redraw())

        detail_frame = ttk.LabelFrame(content, text="当前组 · 单击选拆分起点 / 查看证据 · 双击全屏", padding=6)
        detail_frame.grid(row=0, column=1, sticky="nsew")
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(1, weight=1)
        self.detail_status_var = tk.StringVar(value="")
        self.detail_status_label = ttk.Label(detail_frame, textvariable=self.detail_status_var, style="Muted.TLabel", wraplength=420)
        self.detail_status_label.grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 5)
        )
        self.detail_canvas = tk.Canvas(detail_frame, width=1, highlightthickness=0, background=COLORS["surface"])
        detail_ybar = ttk.Scrollbar(detail_frame, orient="vertical", command=self._scroll_detail)
        self.detail_canvas.configure(yscrollcommand=detail_ybar.set)
        self.detail_canvas.grid(row=1, column=0, sticky="nsew")
        detail_ybar.grid(row=1, column=1, sticky="ns")
        self.detail_canvas.bind("<MouseWheel>", self._on_detail_mousewheel)
        self.detail_canvas.bind("<Configure>", self._detail_configured)
        self.inspector_frame = ttk.LabelFrame(content, text="照片证据 · 只读", padding=6)
        self.inspector_frame.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        self.inspector_frame.columnconfigure(0, weight=1)
        self.inspector_frame.rowconfigure(0, weight=1)
        self.evidence_text = tk.Text(self.inspector_frame, width=28, height=1, wrap="word",
                                     relief="flat", padx=6, pady=6, state="disabled")
        evidence_bar = ttk.Scrollbar(self.inspector_frame, command=self.evidence_text.yview)
        self.evidence_text.configure(yscrollcommand=evidence_bar.set)
        self.evidence_text.grid(row=0, column=0, sticky="nsew")
        evidence_bar.grid(row=0, column=1, sticky="ns")
        content.bind("<Configure>", self._content_configured)
        self._refresh_inspector()

    def _detail_configured(self, event) -> None:
        self.detail_status_label.configure(wraplength=max(160, event.width - 12))
        self._schedule_detail_redraw()

    def _content_configured(self, event) -> None:
        if event.widget is self.content and not self._inspector_override:
            self._show_inspector(event.width >= 1120)

    def _show_inspector(self, visible: bool) -> None:
        if visible:
            self.inspector_frame.grid()
        else:
            self.inspector_frame.grid_remove()
        self.inspector_button.configure(text="收起证据" if visible else "展开证据")

    def _toggle_inspector(self) -> None:
        self._inspector_override = True
        self._show_inspector(not bool(self.inspector_frame.winfo_manager()))

    def _filtered_members(self):
        members = self._members_by_group.get(self.selected_group_id, [])
        recommendations = grouping_recommendations(members)
        wanted = {"待复核": "review", "建议保留": "keep", "建议弃置": "reject"}.get(self.filter_var.get())
        return [a for a in members if wanted is None or recommendations.get(str(a.primary_path), {}).get('recommendation', 'review') == wanted]

    def _filter_changed(self, _event=None) -> None:
        visible = self._filtered_members()
        exact = getattr(self, '_selected_subject_asset', None)
        if self.selected_stem not in {a.stem for a in visible} or (exact is not None and not any(item is exact for item in visible)):
            self.selected_stem = None
            self._selected_subject_asset = None
        self.detail_canvas.yview_moveto(0)
        self._redraw_detail()
        self._refresh_inspector()

    def _subject_target(self):
        """Route only an exact currently visible photo; duplicate stems fail closed."""
        visible = self._filtered_members()
        exact = getattr(self, '_selected_subject_asset', None)
        if exact is not None:
            return exact if exact.stem == self.selected_stem and any(item is exact for item in visible) else None
        matches = [item for item in visible if item.stem == self.selected_stem]
        return matches[0] if len(matches) == 1 else None

    def _choose_subject_for_photo(self) -> None:
        target = self._subject_target()
        callback = getattr(self, 'on_select_subject', None)
        if target is not None and callable(callback):
            callback(target)

    def _refresh_inspector(self) -> None:
        members = self._members_by_group.get(self.selected_group_id, [])
        asset = self._subject_target()
        button = getattr(self, 'subject_button', None)
        if button is not None:
            button.configure(state='normal' if asset is not None and callable(getattr(self, 'on_select_subject', None)) else 'disabled')
        text = "选择一张照片查看完整技术原因。\n\n本地建议与人工决定分开显示。待复核不代表弃置。\n\n单击仍选择拆分起点；拆分和合并后立即保存。"
        if asset is not None:
            recommendation = grouping_recommendations(members).get(str(asset.primary_path), {}).get('recommendation', 'review')
            text = grouping_evidence_text(asset, recommendation)
        if text == getattr(self, "_inspector_text", None):
            return
        self._inspector_text = text
        self.evidence_text.configure(state="normal")
        self.evidence_text.delete("1.0", "end")
        self.evidence_text.insert("1.0", text)
        self.evidence_text.configure(state="disabled")


    def _on_mousewheel(self, event) -> None:
        self.rows_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._schedule_rows_redraw()
        return "break"

    def _scroll_rows(self, *args: str) -> None:
        self.rows_canvas.yview(*args)
        self._schedule_rows_redraw()

    def _schedule_rows_redraw(self) -> None:
        if self._redraw_token is not None:
            return
        self._redraw_token = self.after_idle(self._run_scheduled_redraw)

    def _run_scheduled_redraw(self) -> None:
        self._redraw_token = None
        if not self._closed and self.winfo_exists():
            self._redraw_rows()

    def _scroll_detail(self, *args: str) -> None:
        self.detail_canvas.yview(*args)
        self._schedule_detail_redraw()

    def _on_detail_mousewheel(self, event) -> str:
        self.detail_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._schedule_detail_redraw()
        return "break"

    def _schedule_detail_redraw(self) -> None:
        if self._detail_redraw_token is None:
            self._detail_redraw_token = self.after_idle(self._run_scheduled_detail_redraw)

    def _run_scheduled_detail_redraw(self) -> None:
        self._detail_redraw_token = None
        if not self._closed and self.winfo_exists():
            self._redraw_detail()

    def _redraw_rows(self) -> None:
        self.rows_canvas.delete("all")
        self._row_images.clear()
        width = max(self.rows_canvas.winfo_width(), 170)
        row_height = self._group_row_h
        total_h = max(len(self._groups) * row_height, 1)
        self.rows_canvas.configure(scrollregion=(0, 0, width, total_h))

        # A Canvas can describe thousands of rows, but only the rows around the
        # viewport need widgets and decoded thumbnails.  Keeping a one-row
        # buffer makes wheel and scrollbar movement appear continuous.
        viewport_top = max(0, int(self.rows_canvas.canvasy(0)))
        viewport_height = max(row_height, self.rows_canvas.winfo_height())
        visible_rows = visible_group_range(len(self._groups), viewport_top, viewport_height, row_height)
        self._row_requested_keys = set()
        self._cancel_stale_thumbnail_requests()
        for row_index in visible_rows:
            group_id, members = self._groups[row_index]
            y0 = row_index * row_height
            selected = group_id == self.selected_group_id
            fill = COLORS['selection'] if selected else COLORS['surface']
            outline = COLORS['accent'] if selected else COLORS['border']
            tag = f"group:{group_id}"
            self.rows_canvas.create_rectangle(
                4, y0 + 3, width - 5, y0 + row_height - 3,
                fill=fill, outline=outline, width=2 if selected else 1, tags=(tag,)
            )
            self.rows_canvas.create_text(
                14, y0 + 12, anchor="nw", text=f"G{group_id:03d}",
                font=self._fonts["heading"], fill=COLORS['text'], tags=(tag,)
            )
            self.rows_canvas.create_text(
                width - 14, y0 + 12, anchor="ne", text=f"{len(members)} 张",
                font=self._fonts["small"], fill=COLORS['muted'], tags=(tag,)
            )
            first_stem = members[0].stem
            last_stem = members[-1].stem
            full_range = first_stem if first_stem == last_stem else f"{first_stem} — {last_stem}"
            range_text = ellipsize_text(
                full_range,
                self._fonts['shortcut'].measure,
                max(40, width - 28),
            )
            self.rows_canvas.create_text(
                14, y0 + 35, anchor="nw", text=range_text,
                font=self._fonts["shortcut"], fill=COLORS['muted'], tags=(tag,)
            )

            self.rows_canvas.tag_bind(tag, "<Button-1>", lambda e, gid=group_id: self._select_group(gid))
            self.rows_canvas.tag_bind(
                tag, "<Enter>",
                lambda e, gid=group_id, full=full_range: self.status_var.set(f"G{gid:03d} · {full}"),
            )
            self.rows_canvas.tag_bind(
                tag, "<Leave>", lambda e: self.status_var.set(f"共 {len(self._groups)} 组选片组")
            )

        self.status_var.set(f"共 {len(self._groups)} 组选片组")

    def _redraw_detail(self) -> None:
        self.detail_canvas.delete("all")
        self._detail_images.clear()
        if self.selected_group_id is None:
            self.detail_status_var.set("没有可显示的选片组")
            self.filter_count_var.set("显示 0 / 0 张")
            self._detail_requested_keys = set()
            self._cancel_stale_thumbnail_requests()
            self._refresh_inspector()
            return
        all_members = self._members_by_group.get(self.selected_group_id, [])
        members = self._filtered_members()
        self.filter_count_var.set(f"显示 {len(members)} / {len(all_members)} 张")
        self._refresh_detail_status()
        self._refresh_inspector()

        width = max(40, self.detail_canvas.winfo_width())
        caption_height = self._fonts['small'].metrics('linespace') * 3 + 8
        columns, cell_width, row_height, thumb_box = detail_grid_layout(
            width, self.detail_canvas.winfo_height(), len(members), caption_height)
        row_count = (len(members) + columns - 1) // columns
        total_height = max(8 + row_count * row_height, 1)
        self.detail_canvas.configure(scrollregion=(0, 0, width, total_height))
        viewport_top = max(0, int(self.detail_canvas.canvasy(0)))
        viewport_height = max(row_height, self.detail_canvas.winfo_height())
        first_row = max(0, viewport_top // row_height - 1)
        last_row = min(row_count, (viewport_top + viewport_height) // row_height + 2)
        first = first_row * columns
        last = min(len(members), last_row * columns)
        self._detail_requested_keys = {
            self._thumbnail_key(members[index], thumb_box) for index in range(first, last)
        }
        self._cancel_stale_thumbnail_requests()
        recommendations = grouping_recommendations(all_members)
        if not members:
            self.detail_canvas.create_text(20, 24, anchor="nw", text="当前筛选没有照片，可切换为全部。", font=self._fonts["small"], fill=COLORS["muted"])
        stem_counts = Counter(item.stem for item in members)
        for index in range(first, last):
            asset = members[index]
            row, column = divmod(index, columns)
            x0 = column * cell_width + 5
            y0 = row * row_height + 5
            x1 = (column + 1) * cell_width - 5
            y1 = y0 + row_height - 10
            tag = f"detail:{asset.stem}" if stem_counts[asset.stem] == 1 else f"detail-object:{id(asset)}"
            exact = getattr(self, '_selected_subject_asset', None)
            selected = (asset is exact and asset.stem == self.selected_stem) if exact is not None else asset.stem == self.selected_stem
            self.detail_canvas.create_rectangle(
                x0, y0, x1, y1,
                fill=COLORS['selection'] if selected else COLORS['surface'],
                outline=COLORS['accent'] if selected else COLORS['border'],
                width=2 if selected else 1,
                tags=(tag,),
            )
            photo = self._cached_photo(asset, thumb_box)
            if photo is not None:
                self._detail_images.append(photo)
                image_x = x0 + max(6, (x1 - x0 - photo.width()) // 2)
                image_area_height = row_height - caption_height - 26
                image_y = y0 + 6 + max(0, (image_area_height - photo.height()) // 2)
                self.detail_canvas.create_image(image_x, image_y, anchor="nw", image=photo, tags=(tag,))
            local = recommendations.get(str(asset.primary_path), {})
            local_label = {'keep': '本地保留建议', 'reject': '本地弃置建议', 'review': '待复核'}.get(local.get('recommendation'), '待复核')
            if local.get('tied'):
                local_label += '（并列）'
            reasons = grouping_reason_items(asset)
            reason_line = reasons[0] if reasons else '未记录具体原因'
            extra = f" · 另 {len(reasons) - 1} 项" if len(reasons) > 1 else ''
            line_height = self._fonts['small'].metrics('linespace')
            for line, value in enumerate((asset.stem, local_label,
                                          ellipsize_text(reason_line, self._fonts['small'].measure,
                                                         max(20, x1 - x0 - 16 - self._fonts['small'].measure(extra))) + extra)):
                self.detail_canvas.create_text(
                    x0 + 8, y1 - caption_height + 3 + line * line_height,
                    anchor="nw", text=ellipsize_text(value, self._fonts['small'].measure, max(40, x1 - x0 - 16)),
                    font=self._fonts['small'], fill=COLORS['text'] if line < 2 else COLORS['muted'], tags=(tag,))
            self.detail_canvas.tag_bind(tag, "<Button-1>", lambda e, item=asset: self._select_asset(item.stem, item))
            self.detail_canvas.tag_bind(tag, "<Double-Button-1>", lambda e, item=asset: self._open_viewer(item))
            self.detail_canvas.tag_bind(
                tag, "<Enter>", lambda e, stem=asset.stem: self._refresh_detail_status(stem)
            )
            self.detail_canvas.tag_bind(tag, "<Leave>", lambda e: self._refresh_detail_status())

    def _open_viewer(self, asset: PhotoAsset) -> None:
        from .image_viewer import ImageViewer
        members = self._members_by_group.get(asset.group_id, [asset])
        index = next((position for position, member in enumerate(members) if member is asset), 0)
        ImageViewer(self, members, index=index, fullscreen=True)

    def _refresh_detail_status(self, hovered_stem: str | None = None) -> None:
        if self.selected_group_id is None:
            self.detail_status_var.set("没有可显示的选片组")
            return
        members = self._members_by_group.get(self.selected_group_id, [])
        stem = hovered_stem or self.selected_stem
        suffix = f" · {'当前' if hovered_stem else '已选'} {stem}" if stem else ""
        matches = [asset for asset in members if asset.stem == stem] if stem else []
        selected = self._subject_target() if hovered_stem is None else (matches[0] if len(matches) == 1 else None)
        summary = local_recommendation_summary(members, selected)
        self.detail_status_var.set(f"G{self.selected_group_id:03d} · {len(members)} 张 · {summary}{suffix}")

    def _select_group(self, group_id: int) -> None:
        self.selected_group_id = group_id
        self.selected_stem = None
        self._selected_subject_asset = None
        self.detail_canvas.yview_moveto(0)
        self._redraw_rows()
        self._redraw_detail()

    def _select_asset(self, stem: str, exact_asset=None) -> None:
        self.selected_stem = stem
        asset = exact_asset if any(item is exact_asset for item in self.assets) else next((a for a in self.assets if a.stem == stem), None)
        self._selected_subject_asset = exact_asset if asset is exact_asset else None
        if asset is not None:
            self.selected_group_id = asset.group_id
        self._redraw_rows()
        self._redraw_detail()

    def _split_selected(self) -> None:
        if not self.selected_stem:
            messagebox.showinfo("提示", "请先选择一张照片作为拆分起点。", parent=self)
            return
        if sum(asset.stem == self.selected_stem for asset in self.assets) != 1:
            messagebox.showinfo("无法唯一拆分", "存在同名照片，当前拆分接口不能唯一定位；本次未修改分组。", parent=self)
            return
        changed = split_group_at(self.assets, self.selected_stem)
        if not changed:
            messagebox.showinfo("提示", "这张照片已经是该组第一张，无法从这里继续拆分。", parent=self)
            return
        selected = next((a for a in self.assets if a.stem == self.selected_stem), None)
        self.selected_group_id = selected.group_id if selected else self.selected_group_id
        self._notify_change()

    def _merge_neighbor(self, direction: int) -> None:
        if self.selected_group_id is None:
            return
        order = [group_id for group_id, _members in self._groups]
        if self.selected_group_id not in order:
            return
        selected_index = order.index(self.selected_group_id)
        other_index = selected_index + direction
        if not 0 <= other_index < len(order):
            messagebox.showinfo("提示", "没有可合并的相邻组。", parent=self)
            return
        other = order[other_index]
        changed = merge_adjacent_groups(self.assets, self.selected_group_id, other)
        if not changed:
            messagebox.showinfo("提示", "没有可合并的相邻组。", parent=self)
            return
        self.selected_group_id = order[min(selected_index, other_index)]
        self.selected_stem = None
        self._notify_change()

    def _notify_change(self) -> None:
        self.on_change()
        self._groups = grouped_assets(self.assets)
        self._members_by_group = {group_id: members for group_id, members in self._groups}
        self._redraw_rows()
        self._redraw_detail()

    def on_activate(self) -> None:
        """Refresh group membership and visible thumbnails after page switches."""
        if self._closed or not self.winfo_exists():
            return
        self._groups = grouped_assets(self.assets)
        self._members_by_group = {group_id: members for group_id, members in self._groups}
        if self.selected_group_id not in self._members_by_group:
            self.selected_group_id = self._groups[0][0] if self._groups else None
            self.selected_stem = None
        elif self.selected_stem is not None:
            members = self._members_by_group[self.selected_group_id]
            if all(asset.stem != self.selected_stem for asset in members):
                self.selected_stem = None
        visible = self._filtered_members()
        exact = getattr(self, '_selected_subject_asset', None)
        if self.selected_stem not in {asset.stem for asset in visible} or (exact is not None and not any(item is exact for item in visible)):
            self.selected_stem = None
            self._selected_subject_asset = None
        self._redraw_rows()
        self._redraw_detail()

    def _cached_photo(self, asset: PhotoAsset, box: tuple[int, int]) -> ImageTk.PhotoImage | None:
        key = self._thumbnail_key(asset, box)
        if key in self._thumb_cache:
            photo = self._thumb_cache.pop(key)
            self._thumb_cache[key] = photo
            return photo
        self._thumbnail_loader.request(key, asset, box)
        self._ensure_thumbnail_poll()
        return None

    @staticmethod
    def _thumbnail_key(asset: PhotoAsset, box: tuple[int, int]) -> ThumbnailKey:
        return id(asset), box

    def _cancel_stale_thumbnail_requests(self) -> None:
        wanted = self._row_requested_keys | self._detail_requested_keys
        self._thumbnail_loader.cancel_except(wanted)

    def _ensure_thumbnail_poll(self) -> None:
        if self._closed or self._thumb_poll_token is not None or not self._thumbnail_loader.has_pending:
            return
        self._thumb_poll_token = self.after(self.THUMB_POLL_MS, self._drain_thumbnail_results)

    def _drain_thumbnail_results(self) -> None:
        self._thumb_poll_token = None
        if self._closed:
            return
        ready = self._thumbnail_loader.pop_completed()
        for key, thumb in ready:
            try:
                photo = ImageTk.PhotoImage(thumb, master=self) if thumb is not None else None
            except Exception:
                photo = None
            finally:
                if thumb is not None:
                    thumb.close()
            self._thumb_cache[key] = photo
            while len(self._thumb_cache) > self.THUMB_CACHE_SIZE:
                self._thumb_cache.popitem(last=False)
        if ready:
            # One batched redraw both reveals completed images and submits any
            # visible requests that previously waited for a bounded queue slot.
            self._schedule_rows_redraw()
            self._schedule_detail_redraw()
        self._ensure_thumbnail_poll()

    def destroy(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        for token_name in ("_redraw_token", "_detail_redraw_token", "_thumb_poll_token"):
            token = getattr(self, token_name, None)
            if token is not None:
                try:
                    self.after_cancel(token)
                except tk.TclError:
                    pass
                setattr(self, token_name, None)
        loader = getattr(self, "_thumbnail_loader", None)
        if loader is not None:
            loader.close()
        super().destroy()
