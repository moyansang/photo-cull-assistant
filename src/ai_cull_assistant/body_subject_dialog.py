"""Manual multi-body selection; editing never starts an AI request or decoding RAW."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageOps, ImageTk

from .crop_settings import CropSettings
from .subject import _valid_normalized_box


def effective_face_boxes(asset, entry, *, face_boxes=None):
    """Read the effective display faces without invoking detection or editing data.

    A prepared preview may supply fresher faces than the persisted asset. Explicit
    selections (including []) and manual boxes remain authoritative either way.
    """
    if entry.get('hidden') or entry.get('subjects_disabled'):
        return []
    if 'selected_faces' in entry or entry.get('manual_face') is not None:
        from .primary_quality import selected_boxes
        try:
            return selected_boxes(entry, 'face')
        except (TypeError, ValueError):
            return []
    elif face_boxes is not None:
        boxes = face_boxes
    else:
        face = getattr(getattr(asset, 'subject_features', None), 'face', None)
        boxes = [face] if face else []
    if not isinstance(boxes, (list, tuple)):
        return []
    return [tuple(map(float, box)) for box in boxes if _valid_normalized_box(box)]


def selected_body_boxes(asset, entry, *, auto_boxes=None):
    """Retain analysis/edit selection independently of face-first visibility."""
    from .primary_quality import manual_faces_disable_body_fallback
    if entry.get('hidden') or entry.get('subjects_disabled'):
        return []
    if 'selected_bodies' in entry:
        from .primary_quality import selected_boxes
        try:
            return selected_boxes(entry, 'body')
        except (TypeError, ValueError):
            return []
    elif manual_faces_disable_body_fallback(entry):
        return []
    elif auto_boxes is not None:
        boxes = auto_boxes
    else:
        boxes = [item.get('box') for item in (getattr(asset, 'clarity_evidence', None) or {}).get('body_candidates', [])
                 if isinstance(item, dict)]
    if not isinstance(boxes, (list, tuple)):
        return []
    return [tuple(map(float, box)) for box in boxes if _valid_normalized_box(box)]


def visible_body_boxes(asset, entry, *, face_boxes=None):
    """Display only: hiding body boxes must never erase selected_bodies."""
    if effective_face_boxes(asset, entry, face_boxes=face_boxes):
        return []
    return selected_body_boxes(asset, entry)


def toggle_body_box(boxes, box):
    from .crop_dialog import same_face_box
    result = list(boxes)
    match = next((index for index, current in enumerate(result) if same_face_box(current, box)), None)
    if match is None:
        result.append(tuple(box))
    else:
        result.pop(match)
    return result


class BodySubjectDialog(tk.Toplevel):
    def __init__(self, parent, asset, entry, on_apply, *, face_boxes=None):
        super().__init__(parent)
        self.title('人体主体：选择、补框和忽略误检')
        self.transient(parent)
        self.geometry('840x680')
        self.minsize(480, 400)
        self.asset = deepcopy(asset)
        self.entry = deepcopy(entry)
        self.on_apply = on_apply
        self.face_boxes = effective_face_boxes(asset, entry, face_boxes=face_boxes)
        self._face_first = bool(self.face_boxes)
        self._has_faces = self._face_first
        from .primary_quality import selected_boxes
        try:
            self._has_manual_faces = bool(selected_boxes(entry, 'face'))
        except (TypeError, ValueError):
            self._has_manual_faces = False
        self.selected = (selected_body_boxes(asset, entry)
                         if 'selected_bodies' in entry or not self._has_faces else [])
        self.candidates = []
        self.original = None
        self._image_rect = None
        self._drag = None
        self._closed = False
        self._poll = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='body-subject-preview')
        self.status = ttk.Label(self, text='正在读取预览与人体候选…', wraplength=780)
        self.status.pack(fill='x', padx=12, pady=8)
        self._body_scope_var = tk.BooleanVar(self, value=False)
        if self._has_faces:
            ttk.Checkbutton(self, text='单独调整身体检查范围（可选，不改变人脸裁切）',
                            variable=self._body_scope_var, command=self.toggle_body_scope,
                            state='normal' if self._has_manual_faces else 'disabled').pack(fill='x', padx=12, pady=(0, 6))
        self.canvas = tk.Canvas(self, background='#20252b', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, padx=12)
        self.canvas.bind('<Configure>', lambda event: self.render())
        self.canvas.bind('<ButtonPress-1>', self.pointer_down)
        self.canvas.bind('<B1-Motion>', self.pointer_move)
        self.canvas.bind('<ButtonRelease-1>', self.pointer_up)
        actions = ttk.Frame(self)
        actions.pack(fill='x', padx=12, pady=10)
        actions.columnconfigure((0,1,2), weight=1)
        self._scope_buttons = []
        for column, (label, command) in enumerate((('全选候选',self.select_all), ('全部忽略',self.clear), ('恢复自动候选',self.restore_auto))):
            button = ttk.Button(actions, text=label, command=command, state='disabled' if self._face_first else 'normal')
            button.grid(row=0,column=column,sticky='ew',padx=3,pady=3)
            self._scope_buttons.append(button)
        ttk.Button(actions, text='取消', command=self.destroy).grid(row=1,column=2,sticky='ew',padx=3,pady=3)
        apply_button = ttk.Button(actions, text='应用身体检查范围', command=self.apply, state='disabled' if self._face_first else 'normal')
        apply_button.grid(row=1,column=1,sticky='ew',padx=3,pady=3)
        self._scope_buttons.append(apply_button)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.bind('<Escape>', lambda event: self.destroy())
        self._future = self._executor.submit(self._prepare)
        self._future.add_done_callback(self._discard_closed_result)
        self._poll = self.after(30, self._finish_prepare)
        self.grab_set()

    def toggle_body_scope(self):
        # A view switch is not a selection, confirmation or save operation.
        if self._has_faces and not getattr(self, '_has_manual_faces', True):
            self._body_scope_var.set(False)
        self._face_first = bool(self._has_faces and not self._body_scope_var.get())
        self._drag = None
        self.canvas.delete('draft')
        for button in self._scope_buttons:
            button.configure(state='disabled' if self._face_first else 'normal')
        self.render()

    def _prepare(self):
        from .preview import ensure_preview
        from .subject import body_candidates
        path = ensure_preview(self.asset)
        with Image.open(path) as image:
            original = ImageOps.exif_transpose(image).convert('RGB')
        # Candidates remain available even when the user previously ignored
        # all of them. That is only a display choice, never silent reselection.
        candidates = body_candidates(self.asset, CropSettings())
        return original, [tuple(candidate['box']) for candidate in candidates]

    def _discard_closed_result(self, future):
        if self._closed and self.original is None:
            try:
                original, _ = future.result()
                original.close()
            except Exception:
                pass

    def _finish_prepare(self):
        self._poll = None
        if self._closed:
            return
        if not self._future.done():
            self._poll = self.after(30, self._finish_prepare)
            return
        try:
            self.original, self.candidates = self._future.result()
        except (OSError, ValueError, TypeError) as exc:
            self.status.configure(text=f'预览无法读取：{exc}')
            return
        if ('selected_bodies' not in self.entry and not self.selected
                and not getattr(self, '_has_faces', False)):
            self.selected = selected_body_boxes(self.asset, self.entry, auto_boxes=self.candidates)
        self.render()

    def render(self):
        if self._closed or self.original is None:
            return
        from .crop_dialog import same_face_box
        self.canvas.delete('all')
        marked = self.original.copy()
        draw = ImageDraw.Draw(marked)
        for box in ([] if self._face_first else self.candidates):
            if any(same_face_box(box, other) for other in self.selected):
                continue
            x,y,w,h = box
            draw.rectangle((x*marked.width,y*marked.height,(x+w)*marked.width,(y+h)*marked.height), outline='#3399ff', width=3)
        for index, (x,y,w,h) in enumerate(self.face_boxes if self._face_first else self.selected, 1):
            bounds = (x*marked.width,y*marked.height,(x+w)*marked.width,(y+h)*marked.height)
            draw.rectangle(bounds, outline='#00dd99', width=4)
            draw.text((bounds[0]+4,bounds[1]+4), str(index) if self._face_first else f'B{index}', fill='#00dd99')
        width, height = max(self.canvas.winfo_width(),1), max(self.canvas.winfo_height(),1)
        self.status.configure(wraplength=max(100,width-10))
        shown = ImageOps.contain(marked, (width,height))
        self.photo = ImageTk.PhotoImage(shown, master=self)
        self.canvas.create_image(width/2,height/2,image=self.photo)
        self._image_rect = ((width-shown.width)/2,(height-shown.height)/2,shown.width,shown.height)
        if self._face_first:
            text = '当前照片有人脸，默认仅显示人脸框；身体选择保留。仅需调整身体检查范围时勾选上方选项；切换或取消不会保存。'
            if not getattr(self, '_has_manual_faces', True):
                text += ' 当前仍是自动人脸候选，请先返回主体页明确选择要检查的人脸，再调整身体范围。'
            self.status.configure(text=text)
            return
        self.status.configure(text=f'已选 {len(self.selected)} 个身体主体。点框可增减；拖拽补框。仅检查明确选中的身体；无选择并应用表示不检查身体。框选不证明与人脸同人，身体清晰度不能证明眼睛清晰。应用后仍需保存确认并局部重算。')

    def _normalized(self, x, y):
        if not self._image_rect:
            return None
        left,top,width,height = self._image_rect
        return max(0,min(1,(x-left)/width)), max(0,min(1,(y-top)/height))

    def pointer_down(self, event):
        if getattr(self, '_face_first', False):
            return
        if not self._image_rect:
            return
        x,y,w,h = self._image_rect
        if x<=event.x<=x+w and y<=event.y<=y+h:
            self._drag = (event.x,event.y)

    def pointer_move(self, event):
        if getattr(self, '_face_first', False):
            return
        if self._drag:
            self.canvas.delete('draft')
            self.canvas.create_rectangle(*self._drag,event.x,event.y,outline='#ffbb33',width=2,tags='draft')

    def pointer_up(self, event):
        if getattr(self, '_face_first', False):
            return
        if not self._drag:
            return
        ax,ay = self._drag
        self._drag = None
        first, second = self._normalized(ax,ay), self._normalized(event.x,event.y)
        if abs(ax-event.x)>=8 and abs(ay-event.y)>=8:
            box = (min(first[0],second[0]),min(first[1],second[1]),abs(first[0]-second[0]),abs(first[1]-second[1]))
            if _valid_normalized_box(box):
                self.selected.append(box)
        else:
            hits = [box for box in self.selected+self.candidates if box[0]<=first[0]<=box[0]+box[2] and box[1]<=first[1]<=box[1]+box[3]]
            if hits:
                self.selected = toggle_body_box(self.selected, min(hits,key=lambda box:box[2]*box[3]))
        self.render()

    def select_all(self):
        if getattr(self, '_face_first', False):
            return
        self.selected = list(self.candidates)
        self.render()

    def clear(self):
        if getattr(self, '_face_first', False):
            return
        self.selected = []
        self.render()

    def restore_auto(self):
        if getattr(self, '_face_first', False):
            return
        self.on_apply(None)
        self.destroy()

    def apply(self):
        if getattr(self, '_face_first', False):
            return
        self.on_apply([list(box) for box in self.selected])
        self.destroy()

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        if self._poll:
            self.after_cancel(self._poll)
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self.original is not None:
            self.original.close()
        elif self._future.done():
            self._discard_closed_result(self._future)
        parent = self.master
        super().destroy()
        if parent.winfo_exists() and not getattr(parent, '_embedded', False):
            parent.grab_set()
