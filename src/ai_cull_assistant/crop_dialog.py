import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageDraw, ImageOps, ImageTk

from .crop_settings import CropSettings, crop_bounds
from .subject import asset_features, face_crop


class CropDialog(tk.Toplevel):
    def __init__(self, parent, assets, settings, on_save):
        super().__init__(parent)
        self.title("人脸细节设置")
        self.geometry("850x680")
        self.resizable(False, False)
        self.transient(parent)
        self.assets = [a for a in assets if a.preview_path]
        self.index = 0
        self.on_save = on_save
        self.scale = tk.DoubleVar(value=settings.scale_factor)
        self.shift = tk.DoubleVar(value=settings.shift_factor)
        self.ratio = tk.StringVar(value=settings.aspect_ratio)
        self._pending = None
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="小窗大小保持不变；范围增大可多留头发，负偏移向上，正偏移向下。").pack(anchor="w")
        for label, variable, start, end in (("裁切范围", self.scale, .6, 2), ("上下偏移", self.shift, -.5, .5)):
            row = ttk.Frame(body)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=12).pack(side="left")
            ttk.Scale(row, from_=start, to=end, variable=variable, length=530).pack(side="left")
            value = ttk.Label(row, width=8)
            value.pack(side="left", padx=12)
            variable.trace_add("write", lambda *_, v=variable, widget=value: widget.configure(text=f"{v.get():.2f}"))
            value.configure(text=f"{variable.get():.2f}")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="裁切比例", width=12).pack(side="left")
        ttk.Combobox(row, textvariable=self.ratio, values=("124:150", "1:1", "3:4"), state="readonly", width=16).pack(side="left")
        ttk.Label(row, text="默认 / 正方形 / 竖向 3:4").pack(side="left", padx=16)
        self.caption = ttk.Label(body)
        self.caption.pack(pady=(12, 4))
        self.canvas = tk.Canvas(body, width=810, height=400, background="#eeeeee", highlightthickness=0)
        self.canvas.pack()
        navigation = ttk.Frame(body)
        navigation.pack(pady=8)
        ttk.Button(navigation, text="上一张", command=lambda: self.navigate(-1)).pack(side="left", padx=6)
        ttk.Button(navigation, text="下一张", command=lambda: self.navigate(1)).pack(side="left", padx=6)
        ttk.Button(navigation, text="恢复默认", command=self.reset).pack(side="left", padx=6)
        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=4)
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(actions, text="保存并重新生成联系表" if self.assets else "保存设置", command=self.save).pack(side="right")
        for variable in (self.scale, self.shift, self.ratio):
            variable.trace_add("write", self.schedule_preview)
        self.render()
        self.grab_set()

    def settings(self):
        return CropSettings.from_dict(dict(scale_factor=round(self.scale.get(), 2), shift_factor=round(self.shift.get(), 2), aspect_ratio=self.ratio.get()))

    def schedule_preview(self, *_):
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(60, self.render)

    def navigate(self, step):
        if self.assets:
            self.index = (self.index + step) % len(self.assets)
            self.render()

    def reset(self):
        self.scale.set(1)
        self.shift.set(0)
        self.ratio.set("124:150")

    def render(self):
        if self._pending:
            self.after_cancel(self._pending)
            self._pending = None
        self.canvas.delete("all")
        self.photos = []
        if not self.assets:
            self.caption.configure(text="扫描照片后可预览；现在可先保存全局设置。")
            return
        asset = self.assets[self.index]
        self.caption.configure(text=f"{self.index+1}/{len(self.assets)}  ·  {asset.stem}  ·  G{asset.group_id:03d}")
        try:
            with Image.open(asset.preview_path) as source:
                original = ImageOps.exif_transpose(source).convert("RGB")
            subject = asset_features(asset)
            marked = original.copy()
            if subject and subject.face and subject.head:
                bounds = crop_bounds(original.size, subject.head, self.settings())
                ImageDraw.Draw(marked).rectangle(bounds, outline="#00dd88", width=max(2, original.width // 250))
                crop = face_crop(original, subject.face, subject.head, self.settings())
                tile = Image.new("RGB", (124, 150), "white")
                crop = ImageOps.contain(crop, (124, 150))
                tile.paste(crop, ((124-crop.width)//2, (150-crop.height)//2))
                self.photos.append(ImageTk.PhotoImage(tile, master=self))
                self.canvas.create_image(710, 200, image=self.photos[-1])
                self.canvas.create_text(710, 105, text="最终小窗 · 124×150")
            else:
                self.canvas.create_text(710, 200, text="未检测到可靠人脸\n本张不显示小窗", justify="center")
            self.photos.append(ImageTk.PhotoImage(ImageOps.contain(marked, (600, 380)), master=self))
            self.canvas.create_image(305, 200, image=self.photos[-1])
        except (OSError, ValueError) as exc:
            self.canvas.create_text(405, 200, text=f"预览不可用：{exc}")

    def save(self):
        try:
            self.on_save(self.settings())
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.destroy()

    def destroy(self):
        if self._pending:
            self.after_cancel(self._pending)
            self._pending = None
        super().destroy()
