"""Read-only, preview-first photo inspection using the existing Tk workbench.

The browser owns no scoring or decision callbacks. A single daemon worker
replaces pending requests, and never calls Tk. Old generations are discarded
before publication; an in-flight native decoder cannot safely be interrupted.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from math import isclose
from queue import Empty, Queue
from threading import Condition, Thread
from typing import Callable
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from .models import PhotoAsset, RAW_EXTENSIONS
from .ui_style import COLORS, apply_page
from .window_layout import fit_window


SOURCE_LABELS = {
    'paired_jpeg': 'JPG 原图',
    'standard_image': '原尺寸图片',
    'raw_embedded_jpeg': 'RAW 内嵌预览（JPG）',
    'raw_embedded_bitmap': 'RAW 内嵌预览（位图）',
    'raw_full_decode': 'RAW 完整解码图',
    'cached_preview': '缓存预览（来源待确认）',
}


@dataclass
class Viewport:
    """Normalized center and fit-relative magnification, independent of Tk.

    Keeping ``zoom`` when changing source resolution preserves the normalized
    field of view. Different aspect ratios retain the center and fit-relative
    magnification, clamped to keep the available picture inside the viewport.
    """
    width: int = 1
    height: int = 1
    image_width: int = 1
    image_height: int = 1
    center_x: float = .5
    center_y: float = .5
    zoom: float = 1.0

    @property
    def fit_scale(self) -> float:
        return min(self.width / self.image_width, self.height / self.image_height)

    @property
    def scale(self) -> float:
        return self.fit_scale * self.zoom

    def resize(self, width: int, height: int) -> None:
        self.width, self.height = max(1, width), max(1, height)
        self.clamp()

    def set_image(self, size: tuple[int, int]) -> None:
        self.image_width, self.image_height = max(1, size[0]), max(1, size[1])
        self.clamp()

    def fit(self) -> None:
        self.zoom, self.center_x, self.center_y = 1.0, .5, .5

    def native(self) -> None:
        self.zoom = 1.0 / self.fit_scale
        self.clamp()

    def clamp(self) -> None:
        for axis in ('x', 'y'):
            size = self.image_width if axis == 'x' else self.image_height
            view = self.width if axis == 'x' else self.height
            half = view / (2 * size * self.scale)
            value = getattr(self, 'center_' + axis)
            setattr(self, 'center_' + axis, .5 if half >= .5 else min(1 - half, max(half, value)))

    def image_point(self, x: float, y: float) -> tuple[float, float]:
        return (self.center_x + (x - self.width / 2) / (self.scale * self.image_width),
                self.center_y + (y - self.height / 2) / (self.scale * self.image_height))

    def zoom_at(self, factor: float, x: float, y: float) -> None:
        before_x, before_y = self.image_point(x, y)
        # Up to 32 source pixels per displayed pixel in either direction is
        # unnecessary; cap magnification while always allowing fit and 100%.
        max_zoom = max(32.0, 16.0 / self.fit_scale)
        self.zoom = min(max_zoom, max(.1, self.zoom * factor))
        self.center_x = before_x - (x - self.width / 2) / (self.scale * self.image_width)
        self.center_y = before_y - (y - self.height / 2) / (self.scale * self.image_height)
        self.clamp()

    def pan(self, dx: float, dy: float) -> None:
        self.center_x -= dx / (self.scale * self.image_width)
        self.center_y -= dy / (self.scale * self.image_height)
        self.clamp()

    def render(self, image: Image.Image) -> Image.Image:
        """Allocate only a viewport-sized display, even at extreme zoom."""
        left = self.center_x * self.image_width - self.width / (2 * self.scale)
        top = self.center_y * self.image_height - self.height / (2 * self.scale)
        scale = self.scale
        if isclose(scale, 1.0, rel_tol=1e-9, abs_tol=1e-9):
            # Native inspection must not blur adjacent pixels when an odd-sized
            # source, even viewport or fractional pan yields a half-pixel origin.
            left, top, scale = round(left), round(top), 1.0
        return image.transform((self.width, self.height), Image.Transform.AFFINE,
                               (1 / scale, 0, left, 0, 1 / scale, top),
                               Image.Resampling.BILINEAR, fillcolor=COLORS['photo'])


@dataclass
class DisplayFrame:
    image: Image.Image
    source_kind: str
    preview: bool = False

    def copy(self) -> 'DisplayFrame':
        return DisplayFrame(self.image.copy(), self.source_kind, self.preview)

    def close(self) -> None:
        self.image.close()


class BrowseCache:
    """Worker-owned LRU; conservative resident-pixel estimate, no disk writes."""
    def __init__(self, max_bytes: int = 192 * 1024 * 1024, max_entries: int = 3):
        self.max_bytes = max(0, max_bytes)
        self.max_entries = max(0, max_entries)
        self.bytes_used = 0
        self._items: OrderedDict[tuple, tuple[DisplayFrame, int]] = OrderedDict()

    def get(self, key: tuple) -> DisplayFrame | None:
        value = self._items.get(key)
        if value is None:
            return None
        self._items.move_to_end(key)
        return value[0].copy()

    def put(self, key: tuple, frame: DisplayFrame) -> bool:
        """Take ownership if the frame fits. Caller retains oversized frames."""
        size = frame.image.width * frame.image.height * max(4, len(frame.image.getbands()))
        if size > self.max_bytes or not self.max_entries:
            return False
        if key in self._items:
            old, old_size = self._items.pop(key)
            self.bytes_used -= old_size
            old.close()
        while self._items and (len(self._items) >= self.max_entries or self.bytes_used + size > self.max_bytes):
            _, (old, old_size) = self._items.popitem(last=False)
            self.bytes_used -= old_size
            old.close()
        self._items[key] = frame, size
        self.bytes_used += size
        return True

    def discard(self, key: tuple) -> None:
        """Drop one superseded source version, releasing only cache-owned pixels."""
        value = self._items.pop(key, None)
        if value is not None:
            frame, size = value
            self.bytes_used -= size
            frame.close()

    def clear(self) -> None:
        for frame, _ in self._items.values():
            frame.close()
        self._items.clear()
        self.bytes_used = 0


@dataclass
class LoadResult:
    generation: int
    frame: DisplayFrame | None = None
    error: str | None = None


class ViewerSourceChangedError(RuntimeError):
    """The requested source version changed; the user can explicitly reopen it."""


def source_key(asset: PhotoAsset, full_raw: bool) -> tuple:
    """Share the decoder's cheap source identity/change-time boundary.

    Size and mtime alone alias replacements made by restore/copy tools. This
    also versions ctime/device/inode and every possible JPG/RAW source. It is
    not a content hash: undetectable all-stat-preserving in-place changes remain
    a platform limitation, particularly where ctime is creation time.
    """
    from .image_decode import asset_source_signature
    return bool(full_raw), asset_source_signature(asset)


def load_source(asset: PhotoAsset, *, full_raw: bool = False) -> DisplayFrame:
    from .image_decode import load_display_image
    image, source = load_display_image(asset, full_raw=full_raw)
    return DisplayFrame(image, source)


def load_preview(asset: PhotoAsset) -> DisplayFrame | None:
    """Read only an existing preview; browsing never writes into the asset."""
    if not asset.preview_path:
        return None
    try:
        with Image.open(asset.preview_path) as image:
            # Workspace previews are already oriented by their producer.
            return DisplayFrame(image.convert('RGB'), 'cached_preview', preview=True)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


class ViewerLoader:
    """One current decode, one replaceable pending request, bounded results."""
    def __init__(self, decoder: Callable = load_source, preview_loader: Callable = load_preview,
                 *, cache: BrowseCache | None = None):
        self.decoder, self.preview_loader = decoder, preview_loader
        self.cache = cache if cache is not None else BrowseCache()
        self.results: Queue[LoadResult] = Queue()
        self._condition = Condition()
        self._pending = None
        self._generation = 0
        self._closed = False
        self.thread = Thread(target=self._run, name='photo-viewer', daemon=True)
        self.thread.start()

    def submit(self, asset: PhotoAsset, *, full_raw: bool = False) -> int:
        with self._condition:
            if self._closed:
                raise RuntimeError('Viewer loader is closed')
            self._generation += 1
            self._discard_results()
            self._pending = self._generation, asset, bool(full_raw)
            self._condition.notify()
            return self._generation

    def _discard_results(self) -> None:
        while True:
            try:
                result = self.results.get_nowait()
            except Empty:
                return
            if result.frame is not None:
                result.frame.close()

    def _current(self, generation: int) -> bool:
        with self._condition:
            return not self._closed and generation == self._generation

    def _publish(self, result: LoadResult) -> bool:
        with self._condition:
            if self._closed or result.generation != self._generation:
                if result.frame is not None:
                    result.frame.close()
                return False
            self.results.put_nowait(result)
            return True

    def _check_source_version(self, asset: PhotoAsset, full_raw: bool,
                              key: tuple, frame: DisplayFrame) -> None:
        """Close an obsolete result rather than mis-keying or publishing it.

        Check both before retention and immediately before publication. A file
        can change during cache lookup/copy as well as during native decoding.
        This is a point-in-time stat check, not a source-file lock or watcher.
        """
        if source_key(asset, full_raw) != key:
            self.cache.discard(key)
            frame.close()
            raise ViewerSourceChangedError('查看过程中源文件发生变化，请重新打开照片。')

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._pending is not None)
                    if self._closed:
                        return
                    generation, asset, full_raw = self._pending
                    self._pending = None
                try:
                    key = source_key(asset, full_raw)
                    cached = self.cache.get(key)
                    if cached is not None:
                        self._check_source_version(asset, full_raw, key, cached)
                        self._publish(LoadResult(generation, cached))
                        continue
                    preview = self.preview_loader(asset)
                    if preview is not None:
                        self._check_source_version(asset, full_raw, key, preview)
                        if not self._publish(LoadResult(generation, preview)):
                            continue
                    if not self._current(generation):
                        continue
                    frame = self.decoder(asset, full_raw=full_raw)
                    if not self._current(generation):
                        frame.close()
                        continue
                    self._check_source_version(asset, full_raw, key, frame)
                    if self.cache.put(key, frame):
                        frame = frame.copy()
                    self._check_source_version(asset, full_raw, key, frame)
                    self._publish(LoadResult(generation, frame))
                except Exception as exc:
                    self._publish(LoadResult(generation, error=f'{type(exc).__name__}: {exc}'))
        finally:
            self.cache.clear()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._generation += 1
            self._discard_results()
            self._condition.notify()


class ImageViewer(tk.Toplevel):
    """A browsing-only window. ``on_index_changed`` must only select a photo."""
    def __init__(self, parent, assets, index: int = 0, *, fullscreen: bool = True,
                 on_index_changed: Callable[[int], None] | None = None,
                 loader: ViewerLoader | None = None):
        if not assets:
            raise ValueError('没有可查看的照片')
        super().__init__(parent)
        self.withdraw()
        # Workspace pages are embedded Frames; wm transient needs their native owner.
        self.transient(parent.winfo_toplevel())
        self.assets = list(assets)
        self.index = min(len(self.assets) - 1, max(0, index))
        self.on_index_changed = on_index_changed
        self.viewport = Viewport()
        self.loader = loader if loader is not None else ViewerLoader()
        self._closed = False
        self._frame: DisplayFrame | None = None
        self._photo = None
        self._drag = None
        self._poll_id = None
        self._render_id = None
        self._fullscreen = False
        self._native_pending = False
        self._load_error = None
        self._loading_source = False
        self._raw_requested = False
        self._previous_grab = self.grab_current()
        if self._previous_grab is not None:
            self._previous_grab.grab_release()
        self.title('照片查看器')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Destroy>', self._on_destroy, add='+')

        toolbar = ttk.Frame(self, padding=(12, 8))
        toolbar.pack(fill='x')
        self.prev_button = ttk.Button(toolbar, text='上一张 ←', command=lambda: self.navigate(-1))
        self.prev_button.pack(side='left')
        self.next_button = ttk.Button(toolbar, text='下一张 →', command=lambda: self.navigate(1))
        self.next_button.pack(side='left', padx=(6, 14))
        ttk.Button(toolbar, text='适应屏幕', command=self.fit).pack(side='left')
        ttk.Button(toolbar, text='100%', command=self.native).pack(side='left', padx=6)
        self.fullscreen_button = ttk.Button(toolbar, text='全屏', command=self.toggle_fullscreen)
        self.fullscreen_button.pack(side='left')
        ttk.Button(toolbar, text='关闭', command=self.close).pack(side='right')
        source_bar = ttk.Frame(self, padding=(12, 0, 12, 8))
        source_bar.pack(fill='x')
        self.raw_button = ttk.Button(source_bar, text='查看 RAW 完整解码', command=self.show_raw)
        self.raw_button.pack(side='right', padx=(12, 0))
        self.source_var = tk.StringVar(self, value='正在读取照片…')
        self.source_label = ttk.Label(source_bar, textvariable=self.source_var)
        self.source_label.pack(side='left', fill='x', expand=True)
        self.canvas = tk.Canvas(self, background=COLORS['photo'], highlightthickness=0,
                                cursor='fleur', takefocus=True)
        self.canvas.pack(fill='both', expand=True)
        footer = ttk.Frame(self, padding=(12, 8))
        footer.pack(fill='x')
        self.file_var = tk.StringVar(self)
        ttk.Label(footer, textvariable=self.file_var).pack(anchor='w')
        ttk.Label(footer, text='滚轮：鼠标位置缩放   拖动：移动   双击：全屏   Esc：退出全屏 / 关闭',
                  style='Muted.TLabel').pack(anchor='w', pady=(3, 0))
        apply_page(self)
        self.canvas.configure(background=COLORS['photo'])
        self.canvas.bind('<Configure>', self._resize)
        self.canvas.bind('<MouseWheel>', self._wheel)
        self.canvas.bind('<Button-4>', self._wheel)
        self.canvas.bind('<Button-5>', self._wheel)
        self.canvas.bind('<ButtonPress-1>', self._drag_start)
        self.canvas.bind('<B1-Motion>', self._drag_move)
        self.canvas.bind('<ButtonRelease-1>', lambda _event: setattr(self, '_drag', None))
        self.canvas.bind('<Double-Button-1>', lambda _event: self.toggle_fullscreen())
        self.bind('<Escape>', self._escape)
        self.bind('<Left>', lambda _event: self.navigate(-1))
        self.bind('<Right>', lambda _event: self.navigate(1))
        self.bind('<F11>', lambda _event: self.toggle_fullscreen())
        self.bind('<Key-0>', lambda _event: self.fit())
        self.bind('<Key-1>', lambda _event: self.native())
        fit_window(self, (1100, 780), minimum_size=(620, 360), parent=parent)
        self.deiconify()
        self.set_fullscreen(fullscreen)
        self.canvas.focus_set()
        self.grab_set()
        self._request()
        self._poll_id = self.after(25, self._poll)

    def _request(self, *, full_raw: bool = False, keep_current: bool = False) -> None:
        asset = self.assets[self.index]
        self._load_error = None
        self._loading_source = True
        self._raw_requested = full_raw
        self._generation = self.loader.submit(asset, full_raw=full_raw)
        if not keep_current and self._frame is not None:
            self._frame.close()
            self._frame = None
            self._photo = None
        self.file_var.set(f'{self.index + 1} / {len(self.assets)}  ·  {asset.display_path.name}')
        self.title(f'{asset.display_path.name} · 照片查看器')
        self.source_var.set('正在读取 RAW 完整解码图…' if full_raw else '正在读取照片…')
        has_raw = bool(asset.raw_path or asset.primary_path.suffix.lower() in RAW_EXTENSIONS)
        self.raw_button.configure(state='normal' if has_raw else 'disabled')
        self.prev_button.configure(state='normal' if self.index > 0 else 'disabled')
        self.next_button.configure(state='normal' if self.index + 1 < len(self.assets) else 'disabled')
        if not keep_current:
            self.canvas.delete('all')
            self.canvas.create_text(max(1, self.canvas.winfo_width()) / 2,
                                    max(1, self.canvas.winfo_height()) / 2,
                                    text='正在读取…', fill=COLORS['photo_text'])

    def navigate(self, delta: int) -> str:
        index = min(len(self.assets) - 1, max(0, self.index + delta))
        if index != self.index:
            self.index = index
            self._native_pending = False
            self._request()
            if self.on_index_changed is not None:
                self.on_index_changed(index)
        return 'break'

    def show_raw(self) -> None:
        self._request(full_raw=True, keep_current=True)

    def _poll(self) -> None:
        self._poll_id = None
        if self._closed:
            return
        while True:
            try:
                result = self.loader.results.get_nowait()
            except Empty:
                break
            if result.generation != self._generation:
                if result.frame is not None:
                    result.frame.close()
                continue
            if result.error is not None:
                self._load_error = result.error
                self._loading_source = False
                self.source_var.set('读取失败（不代表画质差）：' + result.error)
                continue
            if result.frame is None:
                continue
            # A RAW request should not replace an already visible JPG with a
            # lower-resolution cache preview while the full decode is running.
            if result.frame.preview and self._frame is not None and not self._frame.preview:
                result.frame.close()
                continue
            if self._frame is not None:
                self._frame.close()
            self._frame = result.frame
            if not self._frame.preview:
                self._loading_source = False
            self.viewport.set_image(self._frame.image.size)
            if self._native_pending:
                self.viewport.native()
            self._render()
        self._poll_id = self.after(25, self._poll)

    def _resize(self, event) -> None:
        self.viewport.resize(event.width, event.height)
        if self._native_pending:
            self.viewport.native()
        self._schedule_render()

    def _schedule_render(self) -> None:
        if not self._closed and self._render_id is None:
            self._render_id = self.after(16, self._render)

    def _render(self) -> None:
        if self._render_id is not None:
            self.after_cancel(self._render_id)
            self._render_id = None
        if self._closed or self._frame is None:
            return
        image = self.viewport.render(self._frame.image)
        self._photo = ImageTk.PhotoImage(image, master=self)
        image.close()
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, image=self._photo, anchor='nw')
        source = SOURCE_LABELS.get(self._frame.source_kind, self._frame.source_kind)
        if self._frame.source_kind == 'standard_image':
            source = self.assets[self.index].primary_path.suffix.lstrip('.').upper() + ' 原图'
        pending = ''
        if self._load_error:
            pending = ' · 读取失败（不代表画质差）：' + self._load_error
        elif self._loading_source:
            pending = ' · 正在加载 RAW 完整解码' if self._raw_requested else ' · 正在加载原尺寸'
        self.source_var.set(f'{source} · {self._frame.image.width} × {self._frame.image.height}'
                            f' · {self.viewport.scale * 100:.0f}%{pending}')

    def fit(self) -> str:
        self._native_pending = False
        self.viewport.fit()
        self._render()
        return 'break'

    def native(self) -> str:
        self._native_pending = True
        self.viewport.native()
        self._render()
        return 'break'

    def _wheel(self, event) -> str:
        self._native_pending = False
        delta = getattr(event, 'delta', 0)
        button = getattr(event, 'num', None)
        direction = 1 if button == 4 or delta > 0 else -1
        if not delta and button not in (4, 5):
            return 'break'
        self.viewport.zoom_at(1.2 ** direction, event.x, event.y)
        self._schedule_render()
        return 'break'

    def _drag_start(self, event) -> None:
        self.canvas.focus_set()
        self._drag = event.x, event.y

    def _drag_move(self, event) -> None:
        if self._drag is None:
            return
        x, y = self._drag
        self.viewport.pan(event.x - x, event.y - y)
        self._drag = event.x, event.y
        self._schedule_render()

    def set_fullscreen(self, enabled: bool) -> None:
        self.attributes('-fullscreen', bool(enabled))
        self._fullscreen = bool(enabled)
        self.fullscreen_button.configure(text='退出全屏' if enabled else '全屏')

    def toggle_fullscreen(self) -> str:
        self.set_fullscreen(not self._fullscreen)
        return 'break'

    def _escape(self, _event=None) -> str:
        if self._fullscreen:
            self.set_fullscreen(False)
        else:
            self.close()
        return 'break'

    def close(self) -> None:
        if not self._closed:
            self.destroy()

    def destroy(self) -> None:
        if not self._closed:
            self._closed = True
            for name in ('_poll_id', '_render_id'):
                job = getattr(self, name, None)
                if job is not None:
                    self.after_cancel(job)
                    setattr(self, name, None)
            self.loader.close()
            if self._frame is not None:
                self._frame.close()
                self._frame = None
            self._photo = None
        super().destroy()

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        previous, self._previous_grab = self._previous_grab, None
        try:
            if previous is not None and previous.winfo_exists() and previous.winfo_viewable():
                previous.grab_set()
            if self.master.winfo_exists():
                self.master.focus_set()
        except tk.TclError:
            pass
