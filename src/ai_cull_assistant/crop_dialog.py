from copy import deepcopy
from dataclasses import replace
import cv2
import numpy as np
from .yunet import detect
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageDraw, ImageOps, ImageTk

from .crop_settings import CropSettings, crop_bounds
from .subject import asset_features, face_crop, detail_features
from .preview import ensure_preview
from .window_layout import fit_window, scrollable_body


class CropDialog(tk.Toplevel):
    def __init__(self, parent, assets, settings, on_save):
        super().__init__(parent)
        self.title("检测/调整人脸框")
        self.transient(parent)
        self.assets = [a for a in assets if a.preview_path or a.primary_path]
        self.edits = deepcopy(settings.photos)
        self._loading = False
        self._drag = None
        self._image_rect = None
        self._crop_rect = None
        self._current_head = None
        self.candidates = []
        self.index = 0
        self.on_save = on_save
        self.scale = tk.DoubleVar(value=settings.scale_factor)
        self.shift = tk.DoubleVar(value=settings.shift_factor)
        self.offset_x = tk.DoubleVar(value=settings.offset_x_factor)
        self.confidence = tk.DoubleVar(value=settings.detection_confidence)
        self.ratio = tk.StringVar(value=settings.aspect_ratio)
        self.manual_mode = tk.BooleanVar(value=False)
        self._pending = None
        # Reserve the action footer before allocating the scrollable content.
        actions = ttk.Frame(self, padding=(16, 8))
        actions.pack(side="bottom", fill="x")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(
            actions,
            text="重新扫描修改过的图片" if self.assets else "保存设置",
            command=self.save,
        ).pack(side="right")
        body = scrollable_body(self, padding=16)
        ttk.Label(
            body,
            text="拖动绿色裁切框调整位置；在图片上滚动鼠标滚轮调整范围。小窗输出大小保持不变。",
            wraplength=460,
        ).pack(fill="x", anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="置信度（全局）", width=12).pack(side="left")
        ttk.Scale(row, from_=.7, to=.95, variable=self.confidence, length=300).pack(
            side="left", fill="x", expand=True
        )
        confidence_value = ttk.Label(row, width=8)
        confidence_value.pack(side="left", padx=12)
        self.confidence.trace_add(
            "write",
            lambda *_, widget=confidence_value: widget.configure(text=f"{self.confidence.get():.2f}"),
        )
        confidence_value.configure(text=f"{self.confidence.get():.2f}")
        ttk.Label(
            body,
            text="检测置信度：默认 0.80；降低可减少漏脸，也可能增加错框。与分组灵敏度无关。",
            wraplength=460,
        ).pack(fill="x", anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="裁切比例", width=12).pack(side="left")
        ttk.Combobox(row, textvariable=self.ratio, values=("124:150", "1:1", "3:4"), state="readonly", width=16).pack(side="left")
        ttk.Label(row, text="默认 / 正方形 / 竖向 3:4").pack(side="left", padx=16)
        self.caption = ttk.Label(body)
        self.caption.pack(pady=(12, 4))
        self.canvas = tk.Canvas(body, width=1, height=400, background="#eeeeee", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.schedule_preview)
        self.canvas.bind('<ButtonPress-1>', self.pointer_down)
        self.canvas.bind('<B1-Motion>', self.pointer_move)
        self.canvas.bind('<ButtonRelease-1>', self.pointer_up)
        self.canvas.bind('<MouseWheel>', self.mouse_wheel)
        self.canvas.bind('<Button-4>', self.mouse_wheel)
        self.canvas.bind('<Button-5>', self.mouse_wheel)
        ttk.Label(body, text="绿框：最终裁切范围。蓝框：检测候选。位置、范围和裁切比例仅影响当前照片。").pack()
        manual = ttk.Frame(body)
        manual.pack(pady=4)
        ttk.Checkbutton(
            manual,
            text="手动选人脸",
            variable=self.manual_mode,
            command=self.render,
        ).pack(side="left", padx=6)
        ttk.Button(manual, text="恢复本张自动选脸", command=self.auto_face).pack(side="left", padx=6)
        ttk.Button(manual, text="隐藏本张小窗", command=self.hide_face).pack(side="left", padx=6)
        navigation = ttk.Frame(body)
        navigation.pack(pady=8)
        ttk.Button(navigation, text="上一张", command=lambda: self.navigate(-1)).pack(side="left", padx=6)
        ttk.Button(navigation, text="下一张", command=lambda: self.navigate(1)).pack(side="left", padx=6)
        ttk.Button(navigation, text="下一张未标记", command=self.next_unmarked).pack(side="left", padx=6)
        ttk.Button(navigation, text="重置本张裁切", command=self.reset).pack(side="left", padx=6)
        for variable in (self.scale, self.shift, self.offset_x, self.ratio, self.confidence):
            variable.trace_add("write", self.schedule_preview)
        fit_window(self, (850, 790), minimum_size=(520, 440), parent=parent)
        self.update_idletasks()
        self.load_current()
        self.render()
        self.grab_set()

    def settings(self):
        return CropSettings.from_dict(dict(
            scale_factor=round(self.scale.get(), 3),
            shift_factor=round(self.shift.get(), 3),
            offset_x_factor=round(self.offset_x.get(), 3),
            aspect_ratio=self.ratio.get(),
            detection_confidence=round(self.confidence.get(), 2),
        ))

    def schedule_preview(self, *_):
        if self._loading:
            return
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(60, self.render)

    def navigate(self, step):
        if self.assets:
            self.store_current()
            self.index = (self.index + step) % len(self.assets)
            self.load_current()
            self.render()

    def next_unmarked(self):
        if not self.assets:
            self.caption.configure(text="请先扫描照片，再查找未标记人脸。")
            return
        self.store_current()
        settings = self.global_settings()
        for step in range(1, len(self.assets) + 1):
            index = (self.index + step) % len(self.assets)
            asset = self.assets[index]
            # An explicitly hidden inset is already a user decision.
            if settings.photos.get(settings.key(asset), {}).get('hidden'):
                continue
            subject = detail_features(asset, settings)
            if not subject or not subject.face:
                self.index = index
                self.load_current()
                self.render()
                self.caption.configure(text=f"{index+1}/{len(self.assets)}  ·  {asset.stem}  ·  待补选人脸")
                return
        self.caption.configure(text="没有待补选的人脸：已标记和手动隐藏的照片会自动跳过。")

    def reset(self):
        self.scale.set(1)
        self.shift.set(0)
        self.offset_x.set(0)
        self.ratio.set("124:150")

    def render(self):
        if self._pending:
            self.after_cancel(self._pending)
            self._pending = None
        self.store_current()
        self._image_rect = None
        self._crop_rect = None
        self._current_head = None
        self.canvas.delete("all")
        self.photos = []
        if not self.assets:
            self.caption.configure(text="扫描照片后可预览；现在可先保存全局置信度。")
            return
        asset = self.assets[self.index]
        mode = "手动选人脸" if self.manual_mode.get() else "拖动绿框调整裁切"
        self.caption.configure(text=(
            f"{self.index+1}/{len(self.assets)}  ·  {asset.stem}  ·  G{asset.group_id:03d}"
            f"  ·  范围 {self.scale.get():.2f}  ·  {mode}"
        ))
        try:
            canvas_width = max(240, self.canvas.winfo_width())
            canvas_height = max(100, self.canvas.winfo_height())
            padding = 10
            inset_width = min(150, max(105, canvas_width // 4))
            preview_width = max(80, canvas_width - inset_width - padding * 3)
            preview_height = max(80, canvas_height - padding * 2)
            preview_x = padding + preview_width / 2
            final_x = canvas_width - padding - inset_width / 2
            preview_path = ensure_preview(asset)
            with Image.open(preview_path) as source:
                original = ImageOps.exif_transpose(source).convert("RGB")
            subject = detail_features(asset, self.global_settings())
            self.candidates = detect(cv2.cvtColor(np.asarray(original), cv2.COLOR_RGB2BGR), self.confidence.get())
            marked = original.copy()
            for candidate in self.candidates:
                x,y,w,h = candidate.box
                ImageDraw.Draw(marked).rectangle((x,y,x+w,y+h), outline="#3399ff", width=max(2, original.width//300))
            if subject and subject.face and subject.head:
                bounds = crop_bounds(original.size, subject.head, self.settings())
                self._current_head = subject.head
                crop = face_crop(original, subject.face, subject.head, self.settings())
                tile_size = (min(124, inset_width), min(150, max(60, canvas_height - 44)))
                tile = Image.new("RGB", tile_size, "white")
                crop = ImageOps.contain(crop, tile_size)
                tile.paste(crop, ((tile.width-crop.width)//2, (tile.height-crop.height)//2))
                self.photos.append(ImageTk.PhotoImage(tile, master=self))
                self.canvas.create_image(final_x, canvas_height / 2, image=self.photos[-1])
                self.canvas.create_text(final_x, max(10, (canvas_height - tile.height) / 2 - 12), text="最终小窗 · 124×150")
            else:
                self.canvas.create_text(final_x, canvas_height / 2, text="未检测到可靠人脸\n本张不显示小窗", justify="center")
            preview = ImageOps.contain(marked, (preview_width, preview_height))
            self._image_rect = (preview_x-preview.width/2, canvas_height/2-preview.height/2, preview.width, preview.height, original.width, original.height)
            self.photos.append(ImageTk.PhotoImage(preview, master=self))
            self.canvas.create_image(preview_x, canvas_height / 2, image=self.photos[-1])
            if subject and subject.face and subject.head:
                ix, iy, dw, dh, iw, ih = self._image_rect
                left, top, right, bottom = bounds
                self._crop_rect = (
                    ix + left / iw * dw,
                    iy + top / ih * dh,
                    ix + right / iw * dw,
                    iy + bottom / ih * dh,
                )
                self.canvas.create_rectangle(
                    *self._crop_rect,
                    outline="#00aa66",
                    width=3,
                    tags="crop-outline",
                )
        except (OSError, ValueError) as exc:
            self.canvas.create_text(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, text=f"预览不可用：{exc}")

    def global_settings(self):
        return CropSettings(detection_confidence=round(self.confidence.get(), 2), photos=deepcopy(self.edits))

    def store_current(self):
        if not self.assets or self._loading:
            return
        key = self.global_settings().key(self.assets[self.index])
        entry = self.edits.setdefault(key, {})
        value = self.settings()
        entry.update(
            scale_factor=value.scale_factor,
            shift_factor=value.shift_factor,
            offset_x_factor=value.offset_x_factor,
            aspect_ratio=value.aspect_ratio,
        )

    def load_current(self):
        if not self.assets:
            return
        value = self.global_settings().for_asset(self.assets[self.index])
        self._loading = True
        self.scale.set(value.scale_factor)
        self.shift.set(value.shift_factor)
        self.offset_x.set(value.offset_x_factor)
        self.ratio.set(value.aspect_ratio)
        self._loading = False

    def current_entry(self):
        return self.edits.setdefault(self.global_settings().key(self.assets[self.index]), {})

    def auto_face(self):
        if self.assets:
            entry = self.current_entry()
            entry.pop('manual_face', None)
            entry.pop('hidden', None)
            self.render()

    def hide_face(self):
        if self.assets:
            self.current_entry()['hidden'] = True
            self.render()

    def pointer_down(self, event):
        if not self._image_rect:
            return
        x,y,w,h,_,_ = self._image_rect
        if not (x <= event.x <= x+w and y <= event.y <= y+h):
            return
        if self.manual_mode.get():
            self._drag = ("manual", event.x, event.y)
            return
        if self._crop_rect:
            left, top, right, bottom = self._crop_rect
            if left <= event.x <= right and top <= event.y <= bottom:
                self._drag = (
                    "crop", event.x, event.y,
                    self.offset_x.get(), self.shift.get(), self._crop_rect,
                )
                self.canvas.itemconfigure("crop-outline", state="hidden")

    def pointer_move(self, event):
        if not self._drag:
            return
        self.canvas.delete('drag')
        if self._drag[0] == "manual":
            _, ax, ay = self._drag
            self.canvas.create_rectangle(ax, ay, event.x, event.y, outline='#ff9900', width=2, tags='drag')
        else:
            dx, dy = self._clamped_crop_delta(event.x, event.y)
            left, top, right, bottom = self._drag[5]
            self.canvas.create_rectangle(
                left + dx, top + dy, right + dx, bottom + dy,
                outline='#00aa66', width=3, tags='drag',
            )

    def pointer_up(self, event):
        if not self._drag or not self._image_rect:
            return
        if self._drag[0] == "crop":
            _, ax, ay, original_x, original_y, _ = self._drag
            dx, dy = self._clamped_crop_delta(event.x, event.y)
            self._drag = None
            self.canvas.delete('drag')
            if self._current_head and self._image_rect:
                _, _, display_width, display_height, image_width, image_height = self._image_rect
                base_h = self._current_head[3] * image_height
                if base_h > 0:
                    self._loading = True
                    self.offset_x.set(original_x + dx / display_width * image_width / base_h)
                    self.shift.set(original_y + dy / display_height * image_height / base_h)
                    self._loading = False
            self.render()
            return
        _, ax, ay = self._drag
        self._drag = None
        self.canvas.delete('drag')
        x,y,w,h,iw,ih = self._image_rect
        bx,by = max(x,min(x+w,event.x)),max(y,min(y+h,event.y))
        box = None
        if abs(bx-ax) >= 8 and abs(by-ay) >= 8:
            box = [(min(ax,bx)-x)/w, (min(ay,by)-y)/h, abs(bx-ax)/w, abs(by-ay)/h]
        elif abs(bx-ax) < 8 and abs(by-ay) < 8:
            px,py = (ax-x)/w*iw,(ay-y)/h*ih
            hits = [f for f in self.candidates if f.box[0] <= px <= f.box[0]+f.box[2] and f.box[1] <= py <= f.box[1]+f.box[3]]
            if hits:
                face = max(hits, key=lambda f:f.score)
                fx,fy,fw,fh = face.box
                left,top = max(0,fx/iw),max(0,fy/ih)
                box = [left,top,min(1,(fx+fw)/iw)-left,min(1,(fy+fh)/ih)-top]
        if box:
            entry = self.current_entry()
            entry['manual_face'] = box
            entry.pop('hidden', None)
            self.render()

    def _clamped_crop_delta(self, event_x, event_y):
        """Keep a dragged crop frame inside the displayed source image."""
        if not self._drag or self._drag[0] != "crop" or not self._image_rect:
            return 0.0, 0.0
        _, start_x, start_y, _, _, crop = self._drag
        image_x, image_y, image_w, image_h, _, _ = self._image_rect
        left, top, right, bottom = crop
        dx = max(image_x - left, min(event_x - start_x, image_x + image_w - right))
        dy = max(image_y - top, min(event_y - start_y, image_y + image_h - bottom))
        return dx, dy

    def mouse_wheel(self, event):
        if not self.assets or not self._image_rect:
            return "break"
        image_x, image_y, image_w, image_h, _, _ = self._image_rect
        if not (image_x <= event.x <= image_x + image_w and image_y <= event.y <= image_y + image_h):
            return "break"
        direction = getattr(event, "delta", 0)
        if not direction:
            direction = 120 if getattr(event, "num", 0) == 4 else -120
        factor = .92 if direction > 0 else 1.08
        self.scale.set(max(.6, min(2.0, round(self.scale.get() * factor, 3))))
        return "break"

    def save(self):
        try:
            self.store_current()
            self.on_save(self.global_settings())
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.destroy()

    def destroy(self):
        if self._pending:
            self.after_cancel(self._pending)
            self._pending = None
        super().destroy()
