"""Candidate window for same-group face assist.

Owns one dedicated worker thread (never the parent's face-preview queue), a
tree of proposals, a preview with blue dashed candidate boxes, per-photo
confirm/manual-adjust/skip actions, and a final accept that hands guarded
items back to the parent dialog.  Nothing is written to settings here.
"""
from __future__ import annotations

from collections import OrderedDict
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageOps, ImageTk

from . import group_face_assist
from .window_layout import fit_window

LEVEL_LABELS = {'reliable': '可靠', 'review': '待确认', 'missing': '未找到'}


class GroupFaceAssistDialog(tk.Toplevel):
    def __init__(self, parent, reference, reference_box, targets, apply_items, on_close=None,
                 group_id=None, detection_confidence=.8):
        super().__init__(parent)
        self.title(f"补齐本组人脸 · 组 {group_id if group_id is not None else reference.stem}")
        self.transient(parent)
        self.reference = reference
        self.detection_confidence = detection_confidence
        self.reference_box = tuple(float(value) for value in reference_box)
        self.targets = list(targets)
        self.apply_items = apply_items
        self.on_close_callback = on_close
        self._closed = False
        self._stop = threading.Event()
        self._messages = queue.Queue()
        self._poll_token = None
        self._worker = None
        self._awaiting_done = False
        self._finished = False
        self._draw_mode = False
        self._drag_start = None
        self._drag_box = None
        self._drag_key = None
        self._selected_box_key = None
        self._image_rect = None
        self._photos = []
        self._preview_cache = OrderedDict()
        self.rows = {target.key: {
            'target': target, 'stem': target.stem, 'proposal': None,
            'box': None, 'accepted': False, 'status': '排队中',
        } for target in self.targets}

        # Reserve header and footer before the resizable body so the action
        # buttons stay visible on short screens.
        header = ttk.Frame(self, padding=(12, 8, 12, 0))
        header.pack(side='top', fill='x')
        ttk.Label(header, text=f"参考照片：{reference.stem}    待检查：{len(self.targets)} 张").pack(side='left')
        self.progress_label = ttk.Label(header, text=f"0 / {len(self.targets)}")
        self.progress_label.pack(side='left', padx=18)
        self.stop_button = ttk.Button(header, text='停止查找', command=self.stop_search)
        self.stop_button.pack(side='right')

        footer = ttk.Frame(self, padding=(12, 4, 12, 10))
        footer.pack(side='bottom', fill='x')
        self.reason_label = ttk.Label(footer, text='', wraplength=560)
        self.reason_label.pack(fill='x')
        buttons = ttk.Frame(footer)
        buttons.pack(fill='x', pady=(6, 0))
        ttk.Button(buttons, text='确认此框', command=self.confirm_current).pack(side='left', padx=(0, 6))
        self.adjust_button = ttk.Button(buttons, text='手动调整', command=self.toggle_draw_mode)
        self.adjust_button.pack(side='left', padx=6)
        ttk.Button(buttons, text='跳过', command=self.skip_current).pack(side='left', padx=6)
        ttk.Button(buttons, text='取消', command=self.destroy).pack(side='right', padx=(6, 0))
        self.accept_button = ttk.Button(buttons, text='采用选中候选', command=self.accept_selected)
        self.accept_button.pack(side='right', padx=6)

        body = ttk.Frame(self, padding=(12, 6))
        body.pack(side='top', fill='both', expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        list_frame = ttk.Frame(body)
        list_frame.grid(row=0, column=0, sticky='ns')
        self.tree = ttk.Treeview(
            list_frame, columns=('accept', 'stem', 'status'), show='headings', height=16,
            selectmode='browse',
        )
        for column, label, width in (('#1', '采用', 46), ('#2', '文件名', 150), ('#3', '状态', 80)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, anchor='center' if column != '#2' else 'w')
        self.tree.pack(side='left', fill='y')
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<<TreeviewSelect>>', self.show_current)
        self.tree.bind('<ButtonRelease-1>', self._tree_click)
        self.preview = tk.Canvas(body, background='#eeeeee', highlightthickness=0)
        self.preview.grid(row=0, column=1, sticky='nsew', padx=(8, 8))
        self.preview.bind('<Configure>', lambda _e: self.show_current())
        self.preview.bind('<ButtonPress-1>', self.pointer_down)
        self.preview.bind('<B1-Motion>', self.pointer_move)
        self.preview.bind('<ButtonRelease-1>', self.pointer_up)
        self.preview.bind('<MouseWheel>', self.pointer_wheel)
        inset = ttk.Frame(body)
        inset.grid(row=0, column=2, sticky='ns')
        self.bind('<Configure>', lambda e: (inset.grid() if e.width >= 800 else inset.grid_remove())
                  if e.widget is self else None)
        ttk.Label(inset, text='局部放大').pack()
        self.inset = tk.Canvas(inset, width=140, height=170, background='#ffffff', highlightthickness=0)
        self.inset.pack()

        fit_window(self, (920, 640), minimum_size=(560, 430), parent=parent)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        for target in self.targets:
            self.tree.insert('', 'end', iid=target.key, text='', values=('☐', target.stem, '排队中'))
        first = self.targets[0].key if self.targets else None
        if first:
            self.tree.selection_set(first)
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
            group_face_assist.propose_group_faces(
                self.reference, self.reference_box, self.targets, self._stop,
                on_progress=lambda done, total, proposal: self._messages.put(
                    ('proposal', done, total, proposal)),
                detection_confidence=self.detection_confidence,
            )
            self._messages.put(('done', None))
        except Exception as exc:  # noqa: BLE001 - surfaced on the main thread
            self._messages.put(('error', str(exc)))

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
        if kind == 'proposal':
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
                messagebox.showerror('补齐本组人脸', f"匹配未完成：{message[1]}", parent=self)
            if self._awaiting_done:
                self._awaiting_done = False
                self.destroy()

    def _update_row(self, key):
        row = self.rows[key]
        proposal = row['proposal']
        status = row['status']
        if status not in ('手动调整', '跳过') and proposal is not None:
            status = LEVEL_LABELS.get(proposal.level, proposal.level)
        mark = '☑' if row['accepted'] else '☐'
        self.tree.item(key, values=(mark, row['stem'], status))

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
        proposal = row['proposal']
        reason = proposal.reason if proposal else '尚未计算'
        if row['status'] == '手动调整':
            reason = '在右侧预览上按住拖动画出人脸框'
        elif row['status'] == '跳过':
            reason = '已跳过本张'
        self.reason_label.configure(text=f"原因：{reason}")
        if not target.preview_path:
            self.preview.create_text(self.preview.winfo_width() // 2 or 200, 100,
                                      text='预览缺失或不可读', fill='#666666')
            self.inset.delete('all')
            return
        try:
            image = self._load_preview(target.preview_path)
        except OSError as exc:
            self.preview.create_text(200, 100, text=f'预览不可用：{exc}')
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
            elif row['accepted']:
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
            messagebox.showinfo('补齐本组人脸', '本张还没有可用候选框，请先手动调整画出范围。', parent=self)
            return
        row['accepted'] = True
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

    def toggle_draw_mode(self):
        if self._awaiting_done:
            return
        self._draw_mode = not self._draw_mode
        self.adjust_button.configure(text='退出手动调整' if self._draw_mode else '手动调整')

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
        if box and not self._draw_mode and box[0] <= point[0] <= box[0] + box[2] and box[1] <= point[1] <= box[1] + box[3]:
            self._drag_box = tuple(box)
            self._selected_box_key = key
        elif self._draw_mode or box is None:
            self._drag_box = None
        else:
            return
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
        self._draw_mode = False
        self.adjust_button.configure(text='手动调整')

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
            messagebox.showinfo('补齐本组人脸', '请先勾选或逐张确认要采用的候选。', parent=self)
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
