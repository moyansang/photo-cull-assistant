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
from .window_layout import fit_window, scrollable_body


class CropDialog(tk.Toplevel):
    def __init__(self, parent, assets, settings, on_save):
        super().__init__(parent)
        self.title("检查／调整人脸框")
        self.transient(parent)
        self.assets = [a for a in assets if a.preview_path]
        self.edits = deepcopy(settings.photos)
        self._loading = False
        self._drag = None
        self._image_rect = None
        self.candidates = []
        self.index = 0
        self.on_save = on_save
        self.scale = tk.DoubleVar(value=settings.scale_factor)
        self.shift = tk.DoubleVar(value=settings.shift_factor)
        self.confidence = tk.DoubleVar(value=settings.detection_confidence)
        self.ratio = tk.StringVar(value=settings.aspect_ratio)
        self._pending = None
        # Reserve the action footer before allocating the scrollable content.
        actions = ttk.Frame(self, padding=(16, 8))
        actions.pack(side="bottom", fill="x")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(actions, text="保存并重新生成联系表" if self.assets else "保存设置", command=self.save).pack(side="right")
        body = scrollable_body(self, padding=16)
        ttk.Label(
            body,
            text="小窗大小保持不变；范围增大可多留头发，负偏移向上，正偏移向下。",
            wraplength=460,
        ).pack(fill="x", anchor="w")
        for label, variable, start, end in (("置信度（全局）", self.confidence, .7, .95), ("裁切范围", self.scale, .6, 2), ("上下偏移", self.shift, -.5, .5)):
            row = ttk.Frame(body)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=12).pack(side="left")
            ttk.Scale(row, from_=start, to=end, variable=variable, length=300).pack(
                side="left", fill="x", expand=True
            )
            value = ttk.Label(row, width=8)
            value.pack(side="left", padx=12)
            variable.trace_add("write", lambda *_, v=variable, widget=value: widget.configure(text=f"{v.get():.2f}"))
            value.configure(text=f"{variable.get():.2f}")
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
        ttk.Label(body, text="蓝框：检测候选；点击选择。没有候选时，拖框圈住脸部。裁切参数仅影响当前照片。").pack()
        manual = ttk.Frame(body)
        manual.pack(pady=4)
        ttk.Button(manual, text="恢复本张自动选脸", command=self.auto_face).pack(side="left", padx=6)
        ttk.Button(manual, text="隐藏本张小窗", command=self.hide_face).pack(side="left", padx=6)
        navigation = ttk.Frame(body)
        navigation.pack(pady=8)
        ttk.Button(navigation, text="上一张", command=lambda: self.navigate(-1)).pack(side="left", padx=6)
        ttk.Button(navigation, text="下一张", command=lambda: self.navigate(1)).pack(side="left", padx=6)
        ttk.Button(navigation, text="下一张未标记", command=self.next_unmarked).pack(side="left", padx=6)
        ttk.Button(navigation, text="重置本张裁切", command=self.reset).pack(side="left", padx=6)
        for variable in (self.scale, self.shift, self.ratio, self.confidence):
            variable.trace_add("write", self.schedule_preview)
        fit_window(self, (850, 790), minimum_size=(520, 440), parent=parent)
        self.update_idletasks()
        self.load_current()
        self.render()
        self.grab_set()

    def settings(self):
        return CropSettings.from_dict(dict(scale_factor=round(self.scale.get(), 2), shift_factor=round(self.shift.get(), 2), aspect_ratio=self.ratio.get(), detection_confidence=round(self.confidence.get(), 2)))

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
        self.ratio.set("124:150")

    def render(self):
        if self._pending:
            self.after_cancel(self._pending)
            self._pending = None
        self.store_current()
        self._image_rect = None
        self.canvas.delete("all")
        self.photos = []
        if not self.assets:
            self.caption.configure(text="扫描照片后可预览；现在可先保存全局置信度。")
            return
        asset = self.assets[self.index]
        self.caption.configure(text=f"{self.index+1}/{len(self.assets)}  ·  {asset.stem}  ·  G{asset.group_id:03d}")
        try:
            canvas_width = max(240, self.canvas.winfo_width())
            canvas_height = max(100, self.canvas.winfo_height())
            padding = 10
            inset_width = min(150, max(105, canvas_width // 4))
            preview_width = max(80, canvas_width - inset_width - padding * 3)
            preview_height = max(80, canvas_height - padding * 2)
            preview_x = padding + preview_width / 2
            final_x = canvas_width - padding - inset_width / 2
            with Image.open(asset.preview_path) as source:
                original = ImageOps.exif_transpose(source).convert("RGB")
            subject = detail_features(asset, self.global_settings())
            self.candidates = detect(cv2.cvtColor(np.asarray(original), cv2.COLOR_RGB2BGR), self.confidence.get())
            marked = original.copy()
            for candidate in self.candidates:
                x,y,w,h = candidate.box
                ImageDraw.Draw(marked).rectangle((x,y,x+w,y+h), outline="#3399ff", width=max(2, original.width//300))
            if subject and subject.face and subject.head:
                bounds = crop_bounds(original.size, subject.head, self.settings())
                ImageDraw.Draw(marked).rectangle(bounds, outline="#00dd88", width=max(2, original.width // 250))
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
        entry.update(scale_factor=value.scale_factor, shift_factor=value.shift_factor, aspect_ratio=value.aspect_ratio)

    def load_current(self):
        if not self.assets:
            return
        value = self.global_settings().for_asset(self.assets[self.index])
        self._loading = True
        self.scale.set(value.scale_factor)
        self.shift.set(value.shift_factor)
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
        if self._image_rect:
            x,y,w,h,_,_ = self._image_rect
            if x <= event.x <= x+w and y <= event.y <= y+h:
                self._drag = (event.x, event.y)

    def pointer_move(self, event):
        if self._drag:
            self.canvas.delete('drag')
            self.canvas.create_rectangle(*self._drag, event.x,event.y, outline='#ff9900', width=2, tags='drag')

    def pointer_up(self, event):
        if not self._drag or not self._image_rect:
            return
        ax,ay = self._drag
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
