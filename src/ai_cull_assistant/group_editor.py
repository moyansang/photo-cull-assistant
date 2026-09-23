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
    ROW_H = 145
    ROW_THUMB = (105, 105)
    DETAIL_THUMB = (150, 125)
    THUMB_CACHE_SIZE = 160
    THUMB_POLL_MS = 20

    def __init__(self, parent: tk.Misc, assets: list[PhotoAsset], on_change) -> None:
        super().__init__(parent)
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
        self._build_ui()
        fit_window(self, (1180, 680), minimum_size=(640, 480), parent=parent)
        self._redraw_rows()
        self._redraw_detail()
        install_control_help(self, "groups")

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(10, 6, 10, 4))
        header.pack(side="top", fill="x")
        ttk.Label(header, text="选片组与照片").pack(side="left")
        self.status_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.status_var).pack(side="right")

        actions = ttk.Frame(self, padding=(10, 4, 10, 8))
        actions.pack(side="bottom", fill="x")
        ttk.Button(actions, text="从所选照片拆分", command=self._split_selected).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="与上一组合并", command=lambda: self._merge_neighbor(-1)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="与下一组合并", command=lambda: self._merge_neighbor(1)).pack(side="left", padx=(0, 8))

        detail_frame = ttk.LabelFrame(self, text="当前组全部照片（点击照片选择拆分位置）", padding=8)
        detail_frame.pack(side="bottom", fill="x", padx=10, pady=(0, 4))
        self.detail_canvas = tk.Canvas(detail_frame, height=175, highlightthickness=0, background="white")
        xbar = ttk.Scrollbar(detail_frame, orient="horizontal", command=self._scroll_detail)
        self.detail_canvas.configure(xscrollcommand=xbar.set)
        self.detail_canvas.pack(fill="x", expand=True)
        xbar.pack(fill="x")
        self.detail_canvas.bind("<Configure>", lambda _event: self._schedule_detail_redraw())

        upper = ttk.Frame(self, padding=(10, 0, 10, 6))
        upper.pack(side="top", fill="both", expand=True)
        self.rows_canvas = tk.Canvas(upper, highlightthickness=0, background="#f4f4f4")
        ybar = ttk.Scrollbar(upper, orient="vertical", command=self._scroll_rows)
        self.rows_canvas.configure(yscrollcommand=ybar.set)
        self.rows_canvas.pack(side="left", fill="both", expand=True)
        ybar.pack(side="right", fill="y")
        self.rows_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.rows_canvas.bind("<Configure>", lambda _event: self._schedule_rows_redraw())

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
        self.detail_canvas.xview(*args)
        self._schedule_detail_redraw()

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
        width = max(self.rows_canvas.winfo_width(), 1080)
        total_h = max(len(self._groups) * self.ROW_H, 1)
        self.rows_canvas.configure(scrollregion=(0, 0, width, total_h))

        # A Canvas can describe thousands of rows, but only the rows around the
        # viewport need widgets and decoded thumbnails.  Keeping a one-row
        # buffer makes wheel and scrollbar movement appear continuous.
        viewport_top = max(0, int(self.rows_canvas.canvasy(0)))
        viewport_height = max(self.ROW_H, self.rows_canvas.winfo_height())
        visible_rows = visible_group_range(len(self._groups), viewport_top, viewport_height, self.ROW_H)
        self._row_requested_keys = {
            self._thumbnail_key(asset, self.ROW_THUMB)
            for row_index in visible_rows
            for asset in self._groups[row_index][1][:8]
        }
        self._cancel_stale_thumbnail_requests()
        for row_index in visible_rows:
            group_id, members = self._groups[row_index]
            y0 = row_index * self.ROW_H
            selected = group_id == self.selected_group_id
            fill = "#e9f2ff" if selected else "white"
            outline = "#4b7bec" if selected else "#d0d0d0"
            tag = f"group:{group_id}"
            self.rows_canvas.create_rectangle(5, y0 + 4, width - 8, y0 + self.ROW_H - 4, fill=fill, outline=outline, width=2 if selected else 1, tags=(tag,))
            self.rows_canvas.create_text(18, y0 + 20, anchor="nw", text=f"G{group_id:03d}\n{len(members)} 张", font=("Segoe UI", 12, "bold"), tags=(tag,))

            x = 105
            max_preview = 8
            for asset in members[:max_preview]:
                photo = self._cached_photo(asset, self.ROW_THUMB)
                if photo is not None:
                    self._row_images.append(photo)
                    self.rows_canvas.create_image(x, y0 + 10, anchor="nw", image=photo, tags=(tag, f"asset:{asset.stem}"))
                self.rows_canvas.create_text(x, y0 + 119, anchor="nw", text=asset.stem, font=("Segoe UI", 9), tags=(tag, f"asset:{asset.stem}"))
                x += 118
            if len(members) > max_preview:
                self.rows_canvas.create_text(x + 6, y0 + 54, anchor="nw", text=f"+{len(members) - max_preview}\n更多", font=("Segoe UI", 11, "bold"), tags=(tag,))

            self.rows_canvas.tag_bind(tag, "<Button-1>", lambda e, gid=group_id: self._select_group(gid))

        self.status_var.set(f"共 {len(self._groups)} 组选片组")

    def _redraw_detail(self) -> None:
        self.detail_canvas.delete("all")
        self._detail_images.clear()
        if self.selected_group_id is None:
            return
        members = self._members_by_group.get(self.selected_group_id, [])
        total_width = max(10 + len(members) * 170, 1)
        self.detail_canvas.configure(scrollregion=(0, 0, total_width, 170))
        viewport_left = max(0, int(self.detail_canvas.canvasx(0)))
        viewport_width = max(170, self.detail_canvas.winfo_width())
        first = max(0, viewport_left // 170 - 1)
        last = min(len(members), (viewport_left + viewport_width) // 170 + 2)
        self._detail_requested_keys = {
            self._thumbnail_key(members[index], self.DETAIL_THUMB) for index in range(first, last)
        }
        self._cancel_stale_thumbnail_requests()
        for index in range(first, last):
            asset = members[index]
            x = 10 + index * 170
            selected = asset.stem == self.selected_stem
            if selected:
                self.detail_canvas.create_rectangle(x - 4, 5, x + 158, 158, outline="#4b7bec", width=3)
            photo = self._cached_photo(asset, self.DETAIL_THUMB)
            if photo is not None:
                self._detail_images.append(photo)
                item = self.detail_canvas.create_image(x, 10, anchor="nw", image=photo, tags=(f"detail:{asset.stem}",))
                self.detail_canvas.tag_bind(item, "<Button-1>", lambda e, stem=asset.stem: self._select_asset(stem))
            text = self.detail_canvas.create_text(x, 140, anchor="nw", text=asset.stem, font=("Segoe UI", 9), tags=(f"detail:{asset.stem}",))
            self.detail_canvas.tag_bind(text, "<Button-1>", lambda e, stem=asset.stem: self._select_asset(stem))

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
