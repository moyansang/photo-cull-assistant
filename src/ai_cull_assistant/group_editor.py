from __future__ import annotations

from collections import OrderedDict
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
from .ui_style import apply_page, set_button_style, COLORS
from .window_layout import fit_window


def grouped_assets(assets: list[PhotoAsset]) -> list[tuple[int, list[PhotoAsset]]]:
    groups: list[tuple[int, list[PhotoAsset]]] = []
    current_group_id: int | None = None
    current_members: list[PhotoAsset] = []
    for asset in assets:
        if current_group_id is None or asset.group_id != current_group_id:
            if current_members:
                groups.append((current_group_id, current_members))  # type: ignore[arg-type]
            current_group_id = asset.group_id
            current_members = [asset]
        else:
            current_members.append(asset)
    if current_members:
        groups.append((current_group_id, current_members))  # type: ignore[arg-type]
    return groups


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


class GroupEditor(tk.Toplevel):
    ROW_H = 62
    DETAIL_THUMB = (160, 120)
    DETAIL_CELL_W = 180
    DETAIL_CELL_H = 162
    THUMB_CACHE_SIZE = 160
    THUMB_POLL_MS = 20

    def __init__(self, parent: tk.Misc, assets: list[PhotoAsset], on_change) -> None:
        super().__init__(parent)
        self.withdraw()
        self.title("选片组编辑")
        self.transient(parent)
        self.assets = assets
        self.on_change = on_change
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
        self._groups: list[tuple[int, list[PhotoAsset]]] = grouped_assets(self.assets)
        self._members_by_group = {group_id: members for group_id, members in self._groups}
        self._redraw_token: str | None = None
        self._detail_redraw_token: str | None = None
        install_page_chrome(self, "groups")
        root = self
        while root.master is not None:
            root = root.master
        self._fonts = root._cull_fonts
        fonts = self._fonts
        self._group_row_h = max(
            self.ROW_H,
            fonts['body'].metrics('linespace') + fonts['shortcut'].metrics('linespace') + 25,
        )
        self._detail_cell_h = max(
            self.DETAIL_CELL_H,
            self.DETAIL_THUMB[1] + fonts['small'].metrics('linespace') + 24,
        )
        self._build_ui()
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
        ttk.Button(actions, text="关闭", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="从所选照片拆分", command=self._split_selected).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="与上一组合并", command=lambda: self._merge_neighbor(-1)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="与下一组合并", command=lambda: self._merge_neighbor(1)).pack(side="left", padx=(0, 8))

        content = ttk.Frame(self, padding=(10, 0, 10, 6))
        content.pack(side="top", fill="both", expand=True)
        content.columnconfigure(0, minsize=220)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        groups_frame = ttk.LabelFrame(content, text="选片组", padding=6)
        groups_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        groups_frame.columnconfigure(0, weight=1)
        groups_frame.rowconfigure(0, weight=1)
        self.rows_canvas = tk.Canvas(groups_frame, width=230, highlightthickness=0, background=COLORS["bg"])
        ybar = ttk.Scrollbar(groups_frame, orient="vertical", command=self._scroll_rows)
        self.rows_canvas.configure(yscrollcommand=ybar.set)
        self.rows_canvas.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        self.rows_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.rows_canvas.bind("<Configure>", lambda _event: self._schedule_rows_redraw())

        detail_frame = ttk.LabelFrame(content, text="当前组照片 · 点击照片选择拆分位置", padding=6)
        detail_frame.grid(row=0, column=1, sticky="nsew")
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(1, weight=1)
        self.detail_status_var = tk.StringVar(value="")
        ttk.Label(detail_frame, textvariable=self.detail_status_var, style="Muted.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 5)
        )
        self.detail_canvas = tk.Canvas(detail_frame, highlightthickness=0, background=COLORS["surface"])
        detail_ybar = ttk.Scrollbar(detail_frame, orient="vertical", command=self._scroll_detail)
        self.detail_canvas.configure(yscrollcommand=detail_ybar.set)
        self.detail_canvas.grid(row=1, column=0, sticky="nsew")
        detail_ybar.grid(row=1, column=1, sticky="ns")
        self.detail_canvas.bind("<MouseWheel>", self._on_detail_mousewheel)
        self.detail_canvas.bind("<Configure>", lambda _event: self._schedule_detail_redraw())

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
        width = max(self.rows_canvas.winfo_width(), 210)
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
                font=("Microsoft YaHei UI", 10, "bold"), fill=COLORS['text'], tags=(tag,)
            )
            self.rows_canvas.create_text(
                width - 14, y0 + 12, anchor="ne", text=f"{len(members)} 张",
                font=("Microsoft YaHei UI", 9), fill=COLORS['muted'], tags=(tag,)
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
                font=("Microsoft YaHei UI", 8), fill=COLORS['muted'], tags=(tag,)
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
            return
        members = self._members_by_group.get(self.selected_group_id, [])
        self._refresh_detail_status()

        width = max(self.detail_canvas.winfo_width(), self.DETAIL_CELL_W)
        columns = max(1, width // self.DETAIL_CELL_W)
        cell_width = max(self.DETAIL_CELL_W, width // columns)
        row_height = self._detail_cell_h
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
            self._thumbnail_key(members[index], self.DETAIL_THUMB) for index in range(first, last)
        }
        self._cancel_stale_thumbnail_requests()
        for index in range(first, last):
            asset = members[index]
            row, column = divmod(index, columns)
            x0 = column * cell_width + 5
            y0 = row * row_height + 5
            x1 = (column + 1) * cell_width - 5
            y1 = y0 + row_height - 10
            tag = f"detail:{asset.stem}"
            selected = asset.stem == self.selected_stem
            self.detail_canvas.create_rectangle(
                x0, y0, x1, y1,
                fill=COLORS['selection'] if selected else COLORS['surface'],
                outline=COLORS['accent'] if selected else COLORS['border'],
                width=2 if selected else 1,
                tags=(tag,),
            )
            photo = self._cached_photo(asset, self.DETAIL_THUMB)
            if photo is not None:
                self._detail_images.append(photo)
                image_x = x0 + max(6, (x1 - x0 - photo.width()) // 2)
                self.detail_canvas.create_image(image_x, y0 + 6, anchor="nw", image=photo, tags=(tag,))
            display_stem = ellipsize_text(
                asset.stem,
                self._fonts['small'].measure,
                max(40, x1 - x0 - 16),
            )
            self.detail_canvas.create_text(
                x0 + 8, y1 - self._fonts['small'].metrics('linespace') - 5,
                anchor="nw", text=display_stem,
                font=("Microsoft YaHei UI", 9), fill=COLORS['text'], tags=(tag,)
            )
            self.detail_canvas.tag_bind(tag, "<Button-1>", lambda e, stem=asset.stem: self._select_asset(stem))
            self.detail_canvas.tag_bind(
                tag, "<Enter>", lambda e, stem=asset.stem: self._refresh_detail_status(stem)
            )
            self.detail_canvas.tag_bind(tag, "<Leave>", lambda e: self._refresh_detail_status())

    def _refresh_detail_status(self, hovered_stem: str | None = None) -> None:
        if self.selected_group_id is None:
            self.detail_status_var.set("没有可显示的选片组")
            return
        members = self._members_by_group.get(self.selected_group_id, [])
        stem = hovered_stem or self.selected_stem
        suffix = f" · {'当前' if hovered_stem else '已选'} {stem}" if stem else ""
        self.detail_status_var.set(f"G{self.selected_group_id:03d} · {len(members)} 张{suffix}")

    def _select_group(self, group_id: int) -> None:
        self.selected_group_id = group_id
        self.selected_stem = None
        self._redraw_rows()
        self._redraw_detail()

    def _select_asset(self, stem: str) -> None:
        self.selected_stem = stem
        asset = next((a for a in self.assets if a.stem == stem), None)
        if asset is not None:
            self.selected_group_id = asset.group_id
        self._redraw_rows()
        self._redraw_detail()

    def _split_selected(self) -> None:
        if not self.selected_stem:
            messagebox.showinfo("提示", "请先在下方选择一张照片作为拆分起点。", parent=self)
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
        other = self.selected_group_id + direction
        if other < 1:
            messagebox.showinfo("提示", "已经是第一组。", parent=self)
            return
        changed = merge_adjacent_groups(self.assets, self.selected_group_id, other)
        if not changed:
            messagebox.showinfo("提示", "没有可合并的相邻组。", parent=self)
            return
        self.selected_group_id = min(self.selected_group_id, other)
        self.selected_stem = None
        self._notify_change()

    def _notify_change(self) -> None:
        self.on_change()
        self._groups = grouped_assets(self.assets)
        self._members_by_group = {group_id: members for group_id, members in self._groups}
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
