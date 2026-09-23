"""Candidate window for workspace-wide person face assist.

Owns one dedicated worker thread (never the parent's face-preview queue), a
tree of proposals, a preview with blue dashed candidate boxes, per-photo
confirm/manual-adjust/skip actions, and a final accept that hands guarded
items back to the parent dialog.  Nothing is written to settings here.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageOps, ImageTk

from . import group_face_assist
from .ui_help import install_control_help, install_page_chrome
from .ui_style import set_button_style, apply_page, COLORS
from .window_layout import fit_window

LEVEL_LABELS = {'reliable': '可靠', 'review': '待确认', 'missing': '未找到'}


class GroupFaceAssistDialog(tk.Toplevel):
    def __init__(self, parent, reference, reference_box, targets, apply_items, on_close=None,
                 group_id=None, detection_confidence=.8, target_assets=None,
                 preview_cache_dir=None, on_target_prepared=None):
        super().__init__(parent)
        self.title(f"补齐人脸 · 参考人物 {reference.stem}")
        self.transient(parent)
        self.reference = reference
        self.detection_confidence = detection_confidence
        self.reference_box = tuple(float(value) for value in reference_box)
        self.targets = list(targets)
        self.target_assets = dict(target_assets or {})
        self.preview_cache_dir = Path(preview_cache_dir) if preview_cache_dir else None
        self.on_target_prepared = on_target_prepared
        self.apply_items = apply_items
        self.on_close_callback = on_close
        self._closed = False
        self._stop = threading.Event()
        self._messages = queue.Queue()
        self._poll_token = None
        self._worker = None
        self._awaiting_done = False
        self._finished = False
        self._drag_start = None
        self._drag_box = None
        self._drag_key = None
        self._selected_box_key = None
        self._image_rect = None
        self._photos = []
        self._preview_cache = OrderedDict()
        self._thumbnail_cache = OrderedDict()
        self._thumbnail_pending = {}
        self._thumbnail_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='assist-thumb')
        self.rows = {target.key: {
            'target': target, 'stem': target.stem, 'proposal': None,
            'box': None, 'accepted': False, 'status': '排队中',
        } for target in self.targets}

        install_page_chrome(self, "assist")
        # Reserve header and footer before the resizable body so every action
        # remains visible on a 720-pixel-high desktop.
        header = ttk.Frame(self, padding=(12, 8, 12, 0))
        header.pack(side='top', fill='x')
        self.reference_label = ttk.Label(header, text=f"参考人物：{reference.stem}", width=30, anchor="w")
        self.reference_label.pack(side="left", fill="x", expand=True)
        self.progress_label = ttk.Label(header, text=f"待检查 0 / {len(self.targets)}")
        self.progress_label.pack(side='left', padx=18)
        self.stop_button = ttk.Button(header, text='停止查找', command=self.stop_search)
        self.stop_button.pack(side='right')

        footer = ttk.Frame(self, padding=(12, 4, 12, 10))
        footer.pack(side='bottom', fill='x')
        self.reason_label = ttk.Label(footer, text='', wraplength=560)
        self.reason_label.pack(fill='x')
        footer.bind('<Configure>', lambda e: self.reason_label.configure(wraplength=max(200, e.width-24)))
        buttons = ttk.Frame(footer)
        buttons.pack(fill='x', pady=(6, 0))
        ttk.Button(buttons, text='确认此框', command=self.confirm_current).pack(side='left', padx=(0, 6))
        ttk.Button(buttons, text='不是这个人', command=self.reject_current).pack(side='left', padx=6)
        ttk.Button(buttons, text='暂时跳过', command=self.skip_current).pack(side='left', padx=6)
        ttk.Button(buttons, text='取消', command=self.destroy).pack(side='right', padx=(6, 0))
        self.accept_button = ttk.Button(buttons, text='采用选中候选', command=self.accept_selected)
        self.accept_button.pack(side='right', padx=6)

        body = ttk.Frame(self, padding=(12, 6))
        body.pack(side='top', fill='both', expand=True)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)

        reference_frame = ttk.LabelFrame(body, text='当前参考人物', padding=6)
        reference_frame.grid(row=0, column=0, sticky='ns')
        self.reference_preview = tk.Canvas(
            reference_frame, width=155, height=250, background=COLORS['photo'], highlightthickness=0)
        self.reference_preview.pack(fill='both', expand=True)
        self._reference_photos = []

        list_frame = ttk.Frame(body)
        list_frame.grid(row=0, column=1, sticky='ns', padx=(8, 0))
        style = ttk.Style(self)
        style.configure('Assist.Treeview', rowheight=54)
        self.tree = ttk.Treeview(
            list_frame, columns=('accept', 'stem', 'status'), show='tree headings', height=8,
            selectmode='browse', style='Assist.Treeview',
        )
        self.tree.heading('#0', text='照片')
        self.tree.column('#0', width=58, minwidth=58, stretch=False)
        ui_scale = max(1.0, float(self.tk.call('tk', 'scaling')) / (96 / 72))
        for column, label, width in (('#1', '采用', 42), ('#2', '文件名', 130), ('#3', '状态', 72)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=round(width * ui_scale), anchor='center' if column != '#2' else 'w')
        self.tree.pack(side='left', fill='y')
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self._scroll_tree)
        scrollbar.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<<TreeviewSelect>>', self.show_current)
        self.tree.bind('<ButtonRelease-1>', self._tree_click)
        self.tree.bind('<MouseWheel>', self._tree_wheel)
        self.tree.bind('<Configure>', lambda _event: self._schedule_visible_thumbnails())
        self.preview = tk.Canvas(body, background=COLORS['photo'], highlightthickness=0)
        self.preview.grid(row=0, column=2, sticky='nsew', padx=(8, 8))
        self.preview.bind('<Configure>', lambda _e: self.show_current())
        self.preview.bind('<ButtonPress-1>', self.pointer_down)
        self.preview.bind('<B1-Motion>', self.pointer_move)
        self.preview.bind('<ButtonRelease-1>', self.pointer_up)
        self.preview.bind('<MouseWheel>', self.pointer_wheel)
        inset = ttk.Frame(body)
        inset.grid(row=0, column=3, sticky='ns')
        self.bind('<Configure>', lambda e: (inset.grid() if e.width >= round(1040 * ui_scale) else inset.grid_remove())
                  if e.widget is self else None)
        ttk.Label(inset, text='候选人脸').pack()
        self.inset = tk.Canvas(inset, width=140, height=170, background=COLORS['photo'], highlightthickness=0)
        self.inset.pack()

        fit_window(self, (1120, 650), minimum_size=(640, 480), parent=parent)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        for target in self.targets:
            self.tree.insert('', 'end', iid=target.key, text='', values=('☐', target.stem, '排队中'))
        first = self.targets[0].key if self.targets else None
        if first:
            self.tree.selection_set(first)
        self.update_idletasks()
        self._draw_reference()
        self._schedule_visible_thumbnails()
        install_control_help(self, "assist")
        apply_page(self)
        set_button_style(self.accept_button, "primary")
        from .ui_help import ToolTip
        self.reference_label._filename_tip = ToolTip(self.reference_label, reference.stem)
        self.tree._filename_tip = ToolTip(self.tree, "")
        if not self.targets:
            self.preview.create_text(160, 100, text="没有待补齐的照片", fill=COLORS["photo_text"])
        self.grab_set()
        self._start_worker()

    # ------------------------------------------------------------------ worker

    def _start_worker(self):
        self._worker = threading.Thread(
            target=self._work, name='group-face-assist', daemon=True)
        self._worker.start()
        self._poll_token = self.after(80, self._poll)

    def _work(self):
        try:
            prepared_targets = []
            for index, target in enumerate(self.targets, 1):
                if self._stop.is_set():
                    break
                self._messages.put(('preparing', index, len(self.targets)))
                prepared = self._prepare_target(target)
                prepared_targets.append(prepared)
                if prepared is not target:
                    self._messages.put(('target_prepared', prepared))
            group_face_assist.propose_person_faces(
                self.reference, self.reference_box, prepared_targets, self._stop,
                on_progress=lambda done, total, proposal: self._messages.put(
                    ('proposal', done, total, proposal)),
                detection_confidence=self.detection_confidence,
            )
            self._messages.put(('done', None))
        except Exception as exc:  # noqa: BLE001 - surfaced on the main thread
            self._messages.put(('error', str(exc)))

    def _prepare_target(self, target):
        if target.preview_path and Path(target.preview_path).is_file():
            return target
        asset = self.target_assets.get(target.key)
        if asset is None:
            return target
        try:
            from .preview import ensure_preview
            path = ensure_preview(asset, self.preview_cache_dir)
        except Exception:
            return target
        return replace(target, preview_path=str(path))

    def _poll(self):
        self._poll_token = None
        if self._closed:
            return
        drained = 0
        while drained < 200:
            drained += 1
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break
            self._handle_message(message)
        if not self._closed:
            self._poll_token = self.after(80, self._poll)

    def _handle_message(self, message):
        kind = message[0]
        if kind == 'preparing':
            _, done, total = message
            self.progress_label.configure(text=f"准备预览 {done} / {total}")
        elif kind == 'proposal':
            _, done, total, proposal = message
            self.progress_label.configure(text=f"{done} / {total}")
            row = self.rows.get(proposal.target_key)
            if row is None:
                return
            row['proposal'] = proposal
            # A manually adjusted box always wins over a late proposal.
            if row['box'] is None:
                row['box'] = proposal.box
            self._update_row(proposal.target_key)
            if self.tree.selection() and self.tree.selection()[0] == proposal.target_key:
                self.show_current()
        elif kind == 'target_prepared':
            target = message[1]
            row = self.rows.get(target.key)
            if row is not None:
                row['target'] = target
                if self.on_target_prepared is not None:
                    self.on_target_prepared(target.key, target.preview_path)
                self._schedule_visible_thumbnails()
                if self.tree.selection() and self.tree.selection()[0] == target.key:
                    self.show_current()
        elif kind == 'thumbnail':
            _, key, thumb = message
            self._thumbnail_pending.pop(key, None)
            try:
                photo = ImageTk.PhotoImage(thumb, master=self.tree) if thumb is not None else None
            finally:
                if thumb is not None:
                    thumb.close()
            self._thumbnail_cache[key] = photo
            self._thumbnail_cache.move_to_end(key)
            while len(self._thumbnail_cache) > 80:
                self._thumbnail_cache.popitem(last=False)
            if self.tree.exists(key):
                self.tree.item(key, image=photo or '')
        elif kind == 'done':
            self._finished = True
            self.stop_button.configure(state='disabled')
            for key, row in self.rows.items():
                if row['proposal'] is None and row['status'] == '排队中':
                    row['status'] = '未处理'
                    self._update_row(key)
            if self._awaiting_done:
                self._awaiting_done = False
                self._finish_accept()
        elif kind == 'error':
            self._finished = True
            self.stop_button.configure(state='disabled')
            if not self._closed:
                messagebox.showerror('补齐人脸', f"匹配未完成：{message[1]}", parent=self)
            if self._awaiting_done:
                self._awaiting_done = False
                self.destroy()

    def _update_row(self, key):
        row = self.rows[key]
        proposal = row['proposal']
        status = row['status']
        if row['accepted']:
            status = '已确认'
        elif status not in ('手动调整', '跳过', '不是这个人') and proposal is not None:
            status = LEVEL_LABELS.get(proposal.level, proposal.level)
        mark = '☑' if row['accepted'] else '☐'
        self.tree.item(key, values=(mark, row['stem'], status))

    # ------------------------------------------------------------- thumbnails

    @staticmethod
    def _load_thumbnail(path):
        if not path:
            return None
        try:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
                return ImageOps.contain(image, (48, 48))
        except OSError:
            return None

    def _thumbnail_done(self, key, future: Future):
        try:
            thumb = future.result()
        except Exception:
            thumb = None
        if self._closed:
            if thumb is not None:
                thumb.close()
            return
        self._messages.put(('thumbnail', key, thumb))

    def _visible_target_keys(self):
        children = self.tree.get_children()
        if not children:
            return []
        first_fraction, last_fraction = self.tree.yview()
        first = max(0, int(first_fraction * len(children)) - 1)
        last = min(len(children), max(first + 1, int(last_fraction * len(children)) + 2))
        return list(children[first:last])

    def _schedule_visible_thumbnails(self):
        if self._closed or not self.winfo_exists():
            return
        for key in self._visible_target_keys():
            if key in self._thumbnail_cache or key in self._thumbnail_pending:
                continue
            row = self.rows.get(key)
            if row is None or len(self._thumbnail_pending) >= 18:
                break
            path = row['target'].preview_path
            if not path or not Path(path).is_file():
                continue
            future = self._thumbnail_executor.submit(
                self._load_thumbnail, path)
            self._thumbnail_pending[key] = future
            future.add_done_callback(lambda done, item_key=key: self._thumbnail_done(item_key, done))

    def _scroll_tree(self, *args):
        self.tree.yview(*args)
        self.after_idle(self._schedule_visible_thumbnails)

    def _tree_wheel(self, event):
        self.tree.yview_scroll(int(-event.delta / 120), 'units')
        self.after_idle(self._schedule_visible_thumbnails)
        return 'break'

    # ----------------------------------------------------------------- preview

    def _load_preview(self, path):
        cached = self._preview_cache.get(path)
        if cached is not None:
            self._preview_cache.move_to_end(path)
            return cached
        with Image.open(path) as source:
            image = ImageOps.contain(ImageOps.exif_transpose(source).convert('RGB'), (1600, 1600))
        self._preview_cache[path] = image
        while len(self._preview_cache) > 6:
            self._preview_cache.popitem(last=False)
        return image

    def _draw_reference(self):
        self.reference_preview.delete('all')
        self._reference_photos.clear()
        if not self.reference.preview_path:
            self.reference_preview.create_text(78, 90, text='参考预览不可用', fill=COLORS['photo_text'])
            return
        try:
            image = self._load_preview(self.reference.preview_path)
        except OSError as exc:
            self.reference_preview.create_text(78, 90, text=f'参考预览不可用\n{exc}', fill=COLORS['photo_text'])
            return
        width = max(120, self.reference_preview.winfo_width() - 8)
        whole = ImageOps.contain(image, (width, 132))
        whole_photo = ImageTk.PhotoImage(whole, master=self.reference_preview)
        self._reference_photos.append(whole_photo)
        self.reference_preview.create_image((width + 8) / 2, 68, image=whole_photo)
        x, y, w, h = self.reference_box
        bounds = (x * image.width, y * image.height, (x + w) * image.width, (y + h) * image.height)
        crop = image.crop(tuple(int(value) for value in bounds))
        face = ImageOps.contain(crop, (width, 92))
        face_photo = ImageTk.PhotoImage(face, master=self.reference_preview)
        self._reference_photos.append(face_photo)
        self.reference_preview.create_text((width + 8) / 2, 154, text='参考人脸', fill=COLORS['photo_text'])
        self.reference_preview.create_image((width + 8) / 2, 218, image=face_photo)

    def show_current(self, *_):
        selection = self.tree.selection()
        if self._closed or not selection:
            return
        key = selection[0]
        if self._drag_key is not None and self._drag_key != key:
            self._drag_start = self._drag_box = self._drag_key = None
        row = self.rows.get(key)
        if row is None:
            return
        self.preview.delete('all')
        self._photos = []
        self._image_rect = None
        target = row['target']
        if hasattr(self.tree, '_filename_tip'):
            self.tree._filename_tip.text = target.stem
        proposal = row['proposal']
        reason = proposal.reason if proposal else '尚未计算'
        if row['accepted']:
            reason = '已确认；点击采用选中候选后写入人脸编辑草稿'
        elif row['status'] == '手动调整':
            reason = '人脸框已修改，请确认；点击框后可拖动，滚轮缩放'
        elif row['status'] == '跳过':
            reason = '本张暂不判断，可稍后返回处理'
        elif row['status'] == '不是这个人':
            reason = '已标记为不是参考人物，不会写入人脸框'
        self.reason_label.configure(text=f"原因：{reason}")
        if not target.preview_path:
            self.preview.create_text(self.preview.winfo_width() // 2 or 200, 100,
                                      text='预览缺失或不可读', fill=COLORS['photo_text'])
            self.inset.delete('all')
            return
        try:
            image = self._load_preview(target.preview_path)
        except OSError as exc:
            self.preview.create_text(200, 100, text=f'预览不可用：{exc}', fill=COLORS['photo_text'], width=max(100, self.preview.winfo_width()-32))
            self.inset.delete('all')
            return
        width = max(120, self.preview.winfo_width())
        height = max(120, self.preview.winfo_height())
        shown = ImageOps.contain(image, (width - 12, height - 12))
        photo = ImageTk.PhotoImage(shown, master=self.preview)
        self._photos.append(photo)
        left = (width - shown.width) / 2
        top = (height - shown.height) / 2
        self.preview.create_image(left, top, image=photo, anchor='nw')
        self._image_rect = (left, top, shown.width, shown.height, image.width, image.height)
        box = row['box']
        if box is not None:
            x, y, w, h = box
            bounds = (left + x * shown.width, top + y * shown.height,
                      left + (x + w) * shown.width, top + (y + h) * shown.height)
            if self._selected_box_key == key:
                self.preview.create_rectangle(*bounds, outline='#e6ae00', width=3)
            elif row['accepted'] or row['status'] == '手动调整':
                self.preview.create_rectangle(*bounds, outline='#00aa66', width=3)
            else:
                self.preview.create_rectangle(*bounds, outline='#3399ff', width=2, dash=(6, 4))
            self._draw_inset(image, box)
        else:
            self.inset.delete('all')

    def _draw_inset(self, image, box):
        self.inset.delete('all')
        x, y, w, h = box
        pad_x, pad_y = w * .5, h * .5
        region = (
            max(0, int((x - pad_x) * image.width)),
            max(0, int((y - pad_y) * image.height)),
            min(image.width, int((x + w + pad_x) * image.width)),
            min(image.height, int((y + h + pad_y) * image.height)),
        )
        if region[2] <= region[0] or region[3] <= region[1]:
            return
        # Unmapped canvases report width 1; fall back to the requested size.
        box_width = max(60, self.inset.winfo_width() - 4)
        box_height = max(60, self.inset.winfo_height() - 4)
        crop = ImageOps.contain(image.crop(region), (box_width, box_height))
        photo = ImageTk.PhotoImage(crop, master=self.inset)
        self._photos.append(photo)
        self.inset.create_image(box_width / 2 + 2, box_height / 2 + 2, image=photo)

    # ------------------------------------------------------------- interaction

    def _tree_click(self, event):
        if self._awaiting_done:
            return
        row_id = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not row_id or column != '#1':
            return
        row = self.rows.get(row_id)
        if row is None or row['box'] is None:
            return
        row['accepted'] = not row['accepted']
        self._update_row(row_id)
        self.show_current()

    def confirm_current(self):
        if self._awaiting_done:
            return
        key = self._current_key()
        if key is None:
            return
        row = self.rows[key]
        if row['box'] is None:
            messagebox.showinfo('补齐人脸', '本张还没有可用候选框，请直接在图片上拖拽画框。', parent=self)
            return
        row['accepted'] = True
        self._selected_box_key = None
        self._update_row(key)
        self.show_current()

    def skip_current(self):
        if self._awaiting_done:
            return
        key = self._current_key()
        if key is None:
            return
        row = self.rows[key]
        row['accepted'] = False
        row['status'] = '跳过'
        self._update_row(key)
        values = self.tree.get_children()
        index = values.index(key)
        if index + 1 < len(values):
            self.tree.selection_set(values[index + 1])
            self.tree.see(values[index + 1])

    def reject_current(self):
        if self._awaiting_done:
            return
        key = self._current_key()
        if key is None:
            return
        row = self.rows[key]
        row['accepted'] = False
        row['status'] = '不是这个人'
        self._update_row(key)
        values = self.tree.get_children()
        index = values.index(key)
        if index + 1 < len(values):
            self.tree.selection_set(values[index + 1])
            self.tree.see(values[index + 1])

    def pointer_down(self, event):
        if not self._image_rect or self._awaiting_done:
            return
        ix, iy, dw, dh, _, _ = self._image_rect
        if not (ix <= event.x <= ix + dw and iy <= event.y <= iy + dh):
            return
        key = self._current_key()
        if key is None:
            return
        point = ((event.x - ix) / dw, (event.y - iy) / dh)
        box = self.rows[key]['box']
        if box and box[0] <= point[0] <= box[0] + box[2] and box[1] <= point[1] <= box[1] + box[3]:
            self._drag_box = tuple(box)
            self._selected_box_key = key
        else:
            self._drag_box = None
            self._selected_box_key = None
        self._drag_key = key
        self._drag_start = point
        self.show_current()

    def pointer_move(self, event):
        if self._drag_start is None:
            return
        ix, iy, dw, dh, _, _ = self._image_rect
        ax, ay = self._drag_start
        if self._drag_box is not None:
            x, y, w, h = self._drag_box
            box = (max(0, min(1-w, x + (event.x-ix)/dw-ax)),
                   max(0, min(1-h, y + (event.y-iy)/dh-ay)), w, h)
            self._set_manual_box(box)
        else:
            self.preview.delete('drag')
            self.preview.create_rectangle(ix+ax*dw, iy+ay*dh,
                max(ix, min(ix+dw, event.x)), max(iy, min(iy+dh, event.y)),
                outline='#ff9900', width=2, tags='drag')

    def pointer_up(self, event):
        if self._drag_start is None or not self._image_rect:
            self._drag_start = None
            return
        ax, ay = self._drag_start
        self._drag_start = None
        self.preview.delete('drag')
        key = self._current_key()
        if key is None:
            return
        if self._drag_box is not None:
            self._drag_box = self._drag_key = None
            return
        ix, iy, dw, dh, _, _ = self._image_rect
        ax, ay = ix + ax * dw, iy + ay * dh
        bx, by = max(ix, min(ix + dw, event.x)), max(iy, min(iy + dh, event.y))
        if abs(bx - ax) < 8 or abs(by - ay) < 8:
            return
        box = group_face_assist.clamp_box(
            (min(ax, bx) - ix) / dw, (min(ay, by) - iy) / dh, abs(bx - ax) / dw, abs(by - ay) / dh)
        self._set_manual_box(box)
        self._selected_box_key = None
        self.show_current()

    def _set_manual_box(self, box):
        key = self._current_key()
        if key is None or not group_face_assist.normalized_box_ok(box):
            return
        row = self.rows[key]
        row['box'] = box
        row['accepted'] = False
        row['status'] = '手动调整'
        self._selected_box_key = key
        self._update_row(key)
        self.show_current()

    def pointer_wheel(self, event):
        key = self._current_key()
        if not key or self._selected_box_key != key or self._awaiting_done:
            return 'break'
        box = self.rows[key]['box']
        if not box or not event.delta:
            return 'break'
        x, y, w, h = box
        factor = 1.02 ** max(-4, min(4, event.delta / 120))
        new_w, new_h = w * factor, h * factor
        if min(new_w, new_h) < .005 or max(new_w, new_h) > 1:
            return 'break'
        self._set_manual_box((max(0, min(1-new_w, x+(w-new_w)/2)),
                              max(0, min(1-new_h, y+(h-new_h)/2)), new_w, new_h))
        return 'break'

    def _current_key(self):
        selection = self.tree.selection()
        return selection[0] if selection and selection[0] in self.rows else None

    # ------------------------------------------------------------- accept/stop

    def stop_search(self):
        self._stop.set()
        self.stop_button.configure(state='disabled')
        self.progress_label.configure(text=self.progress_label.cget('text') + '（已停止）')

    def accept_selected(self):
        if self._awaiting_done:
            return
        items = []
        for key, row in self.rows.items():
            if row['accepted'] and row['box'] is not None:
                items.append((key, row['box'], row['target'].snapshot, row['stem'],
                              row['proposal'].source_key if row['proposal'] else self.reference.key))
        if not items:
            messagebox.showinfo('补齐人脸', '请先勾选或逐张确认要采用的候选。', parent=self)
            return
        if self._finished:
            self._finish_accept(items)
            return
        # Freeze this round first so the accepted set cannot change mid-accept.
        self._stop.set()
        self.stop_button.configure(state='disabled')
        self.accept_button.configure(state='disabled')
        self._awaiting_done = True
        self._pending_items = items

    def _finish_accept(self, items=None):
        if items is None:
            items = getattr(self, '_pending_items', [])
        self._pending_items = []
        if self._closed:
            return
        reference_key = self.reference.key
        try:
            applied, skipped = self.apply_items(reference_key, self.reference_box, [
                {'target_key': key, 'box': box, 'snapshot': snapshot, 'stem': stem,
                 'source_key': source_key}
                for key, box, snapshot, stem, source_key in items
            ])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror('采用候选失败', str(exc), parent=self)
            return
        if skipped:
            lines = '\n'.join(f"{stem}：{reason}" for stem, reason in skipped[:8])
            more = f"\n…共 {len(skipped)} 项" if len(skipped) > 8 else ''
            messagebox.showwarning(
                '部分候选未写入',
                f"已采用 {applied} 张；以下 {len(skipped)} 张被跳过：\n{lines}{more}",
                parent=self,
            )
        self.destroy()

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thumbnail_executor.shutdown(wait=False, cancel_futures=True)
        self._thumbnail_pending.clear()
        self._thumbnail_cache.clear()
        self._preview_cache.clear()
        self._photos.clear()
        if self._poll_token:
            self.after_cancel(self._poll_token)
            self._poll_token = None
        callback = self.on_close_callback
        super().destroy()
        if callback is not None:
            try:
                callback()
            except tk.TclError:
                pass
