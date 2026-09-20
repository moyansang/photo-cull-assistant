from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from collections import OrderedDict
from pathlib import Path
import cv2
import numpy as np
from .yunet import detect
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageDraw, ImageOps, ImageTk

from .crop_settings import CropSettings, crop_bounds, face_box_key
from .subject import face_crop, detail_features, detail_features_list
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
        self._crop_selected = False
        self._current_head = None
        self._prepared = OrderedDict()
        self._image_cache = OrderedDict()
        self._detection_cache = OrderedDict()
        self._cache_lock = threading.Lock()
        from .scan_resources import capture_resource_snapshot
        available = capture_resource_snapshot().available_memory_bytes or 1024**3
        self._image_budget = max(32 * 1024**2, min(256 * 1024**2, available // 32))
        self._loading_image_key = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='face-preview')
        self._future = None
        self._wanted = None
        self._poll_token = None
        self._closed = False
        self.candidates = []
        self._selected_boxes = []
        self._active_face_key = None
        self._person_dirty = False
        self.index = 0
        self.on_save = on_save
        self.scale = tk.DoubleVar(value=settings.scale_factor)
        self.shift = tk.DoubleVar(value=settings.shift_factor)
        self.offset_x = tk.DoubleVar(value=settings.offset_x_factor)
        self.confidence = tk.DoubleVar(value=settings.detection_confidence)
        self.ratio = tk.StringVar(value=settings.aspect_ratio)
        self.person = tk.StringVar(value="自动主体")
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
            text="点击蓝框加入人物，重新点击其绿色或橙色检测框可移除；拖拽可补框漏检人脸。点击裁切框空白处后可移动，滚轮可细调范围。",
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
        person_row = ttk.Frame(body)
        person_row.pack(fill="x", pady=4)
        ttk.Label(person_row, text="当前人物", width=12).pack(side="left")
        self.person_picker = ttk.Combobox(
            person_row,
            textvariable=self.person,
            values=("自动主体",),
            state="readonly",
            width=16,
        )
        self.person_picker.pack(side="left")
        self.person_picker.bind("<<ComboboxSelected>>", self.select_person)
        ttk.Label(person_row, text="多人照片可逐人设置范围、位置和比例").pack(side="left", padx=16)
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
        ttk.Label(body, text="蓝框：候选。绿框：已选人物。橙框：当前人物。裁切框点中后变黄；多人照片的设置彼此独立。").pack()
        manual = ttk.Frame(body)
        manual.pack(pady=4)
        ttk.Button(manual, text="恢复本张自动选脸", command=self.auto_face).pack(side="left", padx=6)
        ttk.Button(manual, text="隐藏本张小窗", command=self.hide_face).pack(side="left", padx=6)
        navigation = ttk.Frame(body)
        navigation.pack(pady=8)
        ttk.Button(navigation, text="上一张", command=lambda: self.navigate(-1)).pack(side="left", padx=6)
        ttk.Button(navigation, text="下一张", command=lambda: self.navigate(1)).pack(side="left", padx=6)
        ttk.Button(navigation, text="下一张未标记", command=self.next_unmarked).pack(side="left", padx=6)
        ttk.Button(navigation, text="重置当前裁切", command=self.reset).pack(side="left", padx=6)
        for variable in (self.scale, self.shift, self.offset_x, self.ratio):
            variable.trace_add("write", self.schedule_crop_preview)
        self.confidence.trace_add("write", self.schedule_preview)
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

    def schedule_crop_preview(self, *_):
        if self._loading:
            return
        if self._active_face_key:
            self._person_dirty = True
        self.schedule_preview()

    def select_person(self, *_):
        if self._loading or not self.assets:
            return
        self.store_current()
        selected = self.current_entry().get('selected_faces')
        if not isinstance(selected, list) or not selected:
            self._active_face_key = None
        else:
            index = max(0, min(self.person_picker.current(), len(selected) - 1))
            self._active_face_key = face_box_key(selected[index])
        self._crop_selected = False
        self.load_current()
        self.render()

    @staticmethod
    def _remember(cache, key, value, limit):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    @staticmethod
    def _source_key(asset):
        path = Path(asset.primary_path)
        st = path.stat()
        version = Path(asset.preview_path).parent.name if asset.preview_path else 'v05'
        return (str(path), st.st_size, st.st_mtime_ns, version)

    def _cached_image(self, key):
        with self._cache_lock:
            value = self._image_cache.get(key)
            if value is not None:
                self._image_cache.move_to_end(key)
            return value

    def _load_preview_image(self, asset):
        # Worker owns the copied asset and PIL data. It never reads Tk variables.
        path = Path(asset.preview_path) if asset.preview_path else None
        if path is None or not path.is_file():
            path = Path(ensure_preview(asset))
        image_key = self._source_key(asset)
        original = self._cached_image(image_key)
        if original is None:
            with Image.open(path) as image:
                original = ImageOps.exif_transpose(image).convert('RGB')
            with self._cache_lock:
                if self._closed:
                    return image_key, original
                self._image_cache[image_key] = original
                while len(self._image_cache) > 1 and sum(i.width*i.height*3 for i in self._image_cache.values()) > self._image_budget:
                    self._image_cache.popitem(last=False)
        return image_key, original

    def _prepare_preview(self, asset, settings):
        image_key, original = self._load_preview_image(asset)
        entry = settings.photos.get(settings.key(asset), {})
        if 'selected_faces' in entry:
            subjects = detail_features_list(asset, settings)
        else:
            subject = detail_features(asset, settings)
            subjects = [subject] if subject else []
        detection_key = (image_key, settings.detection_confidence)
        with self._cache_lock:
            candidates = self._detection_cache.get(detection_key)
        if candidates is None:
            candidates = detect(cv2.cvtColor(np.asarray(original), cv2.COLOR_RGB2BGR),
                                settings.detection_confidence)
            with self._cache_lock:
                if not self._closed:
                    self._remember(self._detection_cache, detection_key, candidates, 512)
        return image_key, subjects, candidates, asset

    def _prepared_preview(self, asset, settings):
        entry = settings.photos.get(settings.key(asset), {})
        # Crop geometry is painted using the cached source; moving the box must
        # not trigger image loading or inference again.
        selection = {k: entry[k] for k in ('selected_faces', 'manual_face', 'hidden') if k in entry}
        image_key = self._source_key(asset)
        key = (self.index, image_key, settings.detection_confidence, json.dumps(selection, sort_keys=True))
        if isinstance(self._prepared.get(key), Exception):
            raise ValueError(str(self._prepared[key]))
        if key in self._prepared and self._cached_image(image_key) is not None:
            self._wanted = None
            self._prepared.move_to_end(key)
            value = self._prepared[key]
            if isinstance(value, Exception):
                raise ValueError(str(value))
            _, subjects, candidates, resolved = value
            original = self._cached_image(image_key)
            asset.preview_path = resolved.preview_path
            asset.subject_features = resolved.subject_features
            asset.subject_checked = resolved.subject_checked
            asset.subject_confidence = resolved.subject_confidence
            return original, subjects, candidates
        self._wanted = (key, deepcopy(asset), settings)
        self._start_preview_work()
        return None

    def _start_preview_work(self):
        if self._closed or self._future is not None or self._wanted is None:
            return
        key, asset, settings = self._wanted
        self._wanted = None
        self._future = (key, self._executor.submit(self._prepare_preview, asset, settings))
        self._poll_token = self.after(25, self._poll_preview)

    def _poll_preview(self):
        self._poll_token = None
        if self._closed or self._future is None:
            return
        key, future = self._future
        if not future.done():
            try:
                image_key = self._source_key(self.assets[self.index])
            except OSError:
                image_key = None
            if image_key is not None and self._loading_image_key != image_key and self._cached_image(image_key) is not None:
                self.render()
            self._poll_token = self.after(25, self._poll_preview)
            return
        self._future = None
        try:
            value = future.result()
        except Exception as exc:
            value = exc
        if key[0] != 'prefetch':
            self._remember(self._prepared, key, value, 512)
        # Render only the currently selected photo/settings. A late previous
        # result can warm the cache but cannot replace the current selection.
        self.render()
        if self._wanted is not None and self._wanted[0] in self._prepared:
            self._wanted = None
        self._start_preview_work()
        self._prefetch_nearby()

    def _prefetch_nearby(self):
        if self._closed or self._future is not None or self._wanted is not None:
            return
        if getattr(self, '_prefetch_anchor', None) != self.index:
            self._prefetch_anchor = self.index
            self._prefetch_attempted = set()
        settings = self.global_settings()
        indices = [self.index + 1, self.index - 1]
        for step in range(1, len(self.assets)):
            index = (self.index + step) % len(self.assets)
            asset = self.assets[index]
            entry = settings.photos.get(settings.key(asset), {})
            subject = asset.subject_features
            located = bool(entry.get('selected_faces')) if 'selected_faces' in entry else bool(
                entry.get('manual_face') or (subject and (subject.head or subject.face)))
            if not entry.get('hidden') and not located:
                indices.append(index)
                break
        for index in indices:
            if not 0 <= index < len(self.assets) or index in self._prefetch_attempted:
                continue
            self._prefetch_attempted.add(index)
            try:
                if self._cached_image(self._source_key(self.assets[index])) is not None:
                    continue
            except OSError:
                continue
            self._future = (('prefetch', index), self._executor.submit(
                self._load_preview_image, deepcopy(self.assets[index])))
            self._poll_token = self.after(25, self._poll_preview)
            break

    @staticmethod
    def _preview_version(asset):
        if not asset.preview_path:
            return None
        version = Path(asset.preview_path).parent.name
        return version if version in ('v04', 'v05') else None

    def navigate(self, step):
        if self.assets:
            self.store_current()
            self.index = (self.index + step) % len(self.assets)
            self._crop_selected = False
            self._active_face_key = None
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
            entry = settings.photos.get(settings.key(asset), {})
            # An explicitly hidden inset is already a user decision.
            if entry.get('hidden'):
                continue
            # The scan already stores subject features on each asset.  Re-running
            # detail_features here decodes and detects every intervening photo,
            # causing a long freeze when the search wraps around a large set.
            subject = getattr(asset, 'subject_features', None)
            if 'selected_faces' in entry:
                # An explicit empty list is a user-visible "nobody selected"
                # state and must remain discoverable as missing.
                located = bool(entry.get('selected_faces'))
            else:
                located = bool(
                    entry.get('manual_face')
                    or (subject and (getattr(subject, 'head', None) or getattr(subject, 'face', None)))
                )
            if not located:
                self.index = index
                self._crop_selected = False
                self._active_face_key = None
                self.load_current()
                self.render()
                self.caption.configure(text=f"{index+1}/{len(self.assets)}  ·  {asset.stem}  ·  待补选人脸")
                return
        self.caption.configure(text="没有待补选的人脸：已标记和手动隐藏的照片会自动跳过。")

    def reset(self):
        self._crop_selected = False
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
        mode = "拖动黄框调整位置" if self._crop_selected else "拖拽补框；点裁切框空白处可移动"
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
            current_settings = self.global_settings()
            entry = current_settings.photos.get(current_settings.key(asset), {})
            prepared = self._prepared_preview(asset, current_settings)
            if prepared is None:
                self.candidates = []
                image_key = self._source_key(asset)
                cached = self._cached_image(image_key)
                if cached is not None:
                    shown = ImageOps.contain(cached, (preview_width, preview_height))
                    photo = ImageTk.PhotoImage(shown)
                    self.photos.append(photo)
                    self.canvas.create_image(preview_x, canvas_height / 2, image=photo)
                    self._loading_image_key = image_key
                return
            original, subjects, candidates = prepared
            self._selected_boxes = [
                tuple(item.face) for item in subjects
                if item and getattr(item, 'face', None)
            ]
            selected_keys = [face_box_key(box) for box in self._selected_boxes]
            if 'selected_faces' in entry and selected_keys:
                if self._active_face_key not in selected_keys:
                    self._active_face_key = selected_keys[0]
                active_index = selected_keys.index(self._active_face_key)
                subject = subjects[active_index]
                picker_values = tuple(f"人物 {number}" for number in range(1, len(selected_keys) + 1))
                self.person_picker.configure(values=picker_values, state="readonly")
                self._loading = True
                self.person.set(picker_values[active_index])
                self._loading = False
            else:
                self._active_face_key = None
                subject = subjects[0] if subjects else None
                picker_value = "未选择人物" if 'selected_faces' in entry else "自动主体"
                self.person_picker.configure(values=(picker_value,), state="disabled")
                self._loading = True
                self.person.set(picker_value)
                self._loading = False
            self.candidates = candidates
            marked = original.copy()
            painter = ImageDraw.Draw(marked)
            for candidate in self.candidates:
                x,y,w,h = candidate.box
                normalized = (x/original.width, y/original.height, w/original.width, h/original.height)
                selected_index = next(
                    (index for index, box in enumerate(self._selected_boxes, 1) if self._same_face(box, normalized)),
                    None,
                )
                if not selected_index:
                    painter.rectangle(
                        (x, y, x + w, y + h),
                        outline="#3399ff",
                        width=max(2, original.width // 300),
                    )
            # Draw persisted boxes independently from current detector output so
            # manually added people and candidates lost at a new confidence
            # threshold remain visible and numbered.
            for selected_index, box in enumerate(self._selected_boxes, 1):
                x, y, w, h = box
                bounds = (
                    x * original.width,
                    y * original.height,
                    (x + w) * original.width,
                    (y + h) * original.height,
                )
                color = "#ff9900" if face_box_key(box) == self._active_face_key else "#00aa66"
                painter.rectangle(bounds, outline=color, width=max(3, original.width // 300))
                painter.text((bounds[0] + 3, bounds[1] + 3), str(selected_index), fill=color)
            head = (getattr(subject, 'head', None) or getattr(subject, 'face', None)) if subject else None
            if head:
                bounds = crop_bounds(original.size, head, self.settings())
                self._current_head = head
                crop = face_crop(original, getattr(subject, 'face', None), head, self.settings())
                tile_size = (min(124, inset_width), min(150, max(60, canvas_height - 44)))
                tile = Image.new("RGB", tile_size, "white")
                crop = ImageOps.contain(crop, tile_size)
                tile.paste(crop, ((tile.width-crop.width)//2, (tile.height-crop.height)//2))
                self.photos.append(ImageTk.PhotoImage(tile, master=self))
                self.canvas.create_image(final_x, canvas_height / 2, image=self.photos[-1])
                inset_label = "最终小窗 · 124×150" if getattr(subject, 'face', None) else "头部定位，清晰度待确认"
                self.canvas.create_text(final_x, max(10, (canvas_height - tile.height) / 2 - 12), text=inset_label)
            else:
                self.canvas.create_text(final_x, canvas_height / 2, text="未检测到可靠人脸\n本张不显示小窗", justify="center")
            selected_count = len(self._selected_boxes)
            if 'selected_faces' in entry:
                current_person = f"人物 {selected_keys.index(self._active_face_key) + 1}" if self._active_face_key in selected_keys else "未选择人物"
                self.caption.configure(text=(
                    f"{self.index+1}/{len(self.assets)}  ·  {asset.stem}  ·  G{asset.group_id:03d}"
                    f"  ·  已选 {selected_count} 人  ·  当前 {current_person}"
                    f"  ·  范围 {self.scale.get():.2f}  ·  {mode}"
                ))
            preview = ImageOps.contain(marked, (preview_width, preview_height))
            self._image_rect = (preview_x-preview.width/2, canvas_height/2-preview.height/2, preview.width, preview.height, original.width, original.height)
            self.photos.append(ImageTk.PhotoImage(preview, master=self))
            self.canvas.create_image(preview_x, canvas_height / 2, image=self.photos[-1])
            if head:
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
                    outline="#ffb000" if self._crop_selected else "#00aa66",
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
        if 'selected_faces' in entry:
            selected_keys = {
                face_box_key(box) for box in entry.get('selected_faces', [])
                if face_box_key(box)
            }
            if self._active_face_key in selected_keys and self._person_dirty:
                entry.setdefault('face_crops', {})[self._active_face_key] = {
                    'scale_factor': value.scale_factor,
                    'shift_factor': value.shift_factor,
                    'offset_x_factor': value.offset_x_factor,
                    'aspect_ratio': value.aspect_ratio,
                }
                self._person_dirty = False
        else:
            entry.update(
                scale_factor=value.scale_factor,
                shift_factor=value.shift_factor,
                offset_x_factor=value.offset_x_factor,
                aspect_ratio=value.aspect_ratio,
            )
        if entry.get('manual_face') or 'selected_faces' in entry:
            preview_version = self._preview_version(self.assets[self.index])
            if preview_version:
                entry['preview_version'] = preview_version

    def load_current(self):
        if not self.assets:
            return
        settings = self.global_settings()
        asset = self.assets[self.index]
        entry = settings.photos.get(settings.key(asset), {})
        selected = entry.get('selected_faces')
        if isinstance(selected, list) and selected:
            keys = [face_box_key(box) for box in selected]
            if self._active_face_key not in keys:
                self._active_face_key = keys[0]
            box = selected[keys.index(self._active_face_key)]
            value = settings.for_face(asset, box)
        else:
            self._active_face_key = None
            value = settings.for_asset(asset)
        self._loading = True
        self.scale.set(value.scale_factor)
        self.shift.set(value.shift_factor)
        self.offset_x.set(value.offset_x_factor)
        self.ratio.set(value.aspect_ratio)
        self._loading = False
        self._person_dirty = False

    def current_entry(self):
        return self.edits.setdefault(self.global_settings().key(self.assets[self.index]), {})

    def auto_face(self):
        if self.assets:
            self._crop_selected = False
            entry = self.current_entry()
            entry.pop('manual_face', None)
            entry.pop('selected_faces', None)
            entry.pop('face_crops', None)
            entry.pop('preview_version', None)
            entry.pop('hidden', None)
            self._active_face_key = None
            self._person_dirty = False
            self.load_current()
            self.render()

    def hide_face(self):
        if self.assets:
            self.store_current()
            self._crop_selected = False
            self.current_entry()['hidden'] = True
            self.render()

    def pointer_down(self, event):
        if not self._image_rect:
            return
        x,y,w,h,_,_ = self._image_rect
        if not (x <= event.x <= x+w and y <= event.y <= y+h):
            return
        candidate = self._candidate_at(event.x, event.y)
        if candidate is not None:
            self._drag = ("candidate", event.x, event.y, candidate)
            return
        if self._crop_rect:
            left, top, right, bottom = self._crop_rect
            if left <= event.x <= right and top <= event.y <= bottom:
                self._crop_selected = True
                self._drag = (
                    "crop", event.x, event.y,
                    self.offset_x.get(), self.shift.get(), self._crop_rect,
                )
                self.canvas.itemconfigure("crop-outline", outline="#ffb000")
                return
        self._crop_selected = False
        self.canvas.itemconfigure("crop-outline", outline="#00aa66")
        self._drag = ("manual", event.x, event.y)

    def pointer_move(self, event):
        if not self._drag:
            return
        if self._drag[0] == "candidate":
            return
        if self._drag[0] == "manual":
            self.canvas.delete('drag')
            _, ax, ay = self._drag
            self.canvas.create_rectangle(ax, ay, event.x, event.y, outline='#ff9900', width=2, tags='drag')
        else:
            dx, dy = self._clamped_crop_delta(event.x, event.y)
            left, top, right, bottom = self._drag[5]
            self.canvas.coords(
                'crop-outline', left + dx, top + dy, right + dx, bottom + dy,
            )

    def pointer_up(self, event):
        if not self._drag or not self._image_rect:
            return
        if self._drag[0] == "candidate":
            _, start_x, start_y, box = self._drag
            self._drag = None
            if abs(event.x - start_x) < 8 and abs(event.y - start_y) < 8:
                self._toggle_selected(box)
                self.render()
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
                    if self._active_face_key and (dx or dy):
                        self._person_dirty = True
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
            self.store_current()
            entry = self.current_entry()
            if not self._selected_boxes and 'selected_faces' not in entry:
                # Preserve the long-standing single-manual-face representation
                # when this is the only participant in an untouched photo.
                entry['manual_face'] = box
            else:
                selected = list(self._selected_boxes)
                if not any(self._same_face(current, box) for current in selected):
                    selected.append(tuple(box))
                entry['selected_faces'] = [list(current) for current in selected]
                entry.pop('manual_face', None)
                self._active_face_key = face_box_key(box)
                self._person_dirty = False
            preview_version = self._preview_version(self.assets[self.index])
            if preview_version:
                entry['preview_version'] = preview_version
            entry.pop('hidden', None)
            self.load_current()
            self.render()

    @staticmethod
    def _same_face(first, second):
        try:
            ax, ay, aw, ah = map(float, first)
            bx, by, bw, bh = map(float, second)
        except (TypeError, ValueError):
            return False
        left, top = max(ax, bx), max(ay, by)
        right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        intersection = max(0.0, right - left) * max(0.0, bottom - top)
        union = aw * ah + bw * bh - intersection
        return intersection / max(union, 1e-9) >= .45

    def _candidate_at(self, event_x, event_y):
        if not self._image_rect:
            return None
        image_x, image_y, display_w, display_h, image_w, image_h = self._image_rect
        px = (event_x - image_x) / display_w * image_w
        py = (event_y - image_y) / display_h * image_h
        hits = [
            face for face in self.candidates
            if face.box[0] <= px <= face.box[0] + face.box[2]
            and face.box[1] <= py <= face.box[1] + face.box[3]
        ]
        if not hits:
            return None
        face = max(hits, key=lambda item: item.score)
        x, y, w, h = face.box
        return (x / image_w, y / image_h, w / image_w, h / image_h)

    def _toggle_selected(self, box):
        self.store_current()
        selected = list(self._selected_boxes)
        match = next(
            (index for index, current in enumerate(selected) if self._same_face(current, box)),
            None,
        )
        if match is None:
            selected.append(tuple(box))
            self._active_face_key = face_box_key(box)
        else:
            removed = selected.pop(match)
            removed_key = face_box_key(removed)
            if self._active_face_key == removed_key:
                self._active_face_key = face_box_key(selected[min(match, len(selected) - 1)]) if selected else None
        entry = self.current_entry()
        entry['selected_faces'] = [list(current) for current in selected]
        entry.pop('manual_face', None)
        entry.pop('hidden', None)
        face_crops = entry.get('face_crops')
        if isinstance(face_crops, dict):
            valid_keys = {face_box_key(current) for current in selected}
            entry['face_crops'] = {
                key: value for key, value in face_crops.items() if key in valid_keys
            }
            if not entry['face_crops']:
                entry.pop('face_crops')
        self._person_dirty = False
        preview_version = self._preview_version(self.assets[self.index])
        if preview_version:
            entry['preview_version'] = preview_version
        self.load_current()

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
        step = -.02 if direction > 0 else .02
        self.scale.set(max(.6, min(2.0, round(self.scale.get() + step, 3))))
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
        self._closed = True
        if self._poll_token:
            self.after_cancel(self._poll_token)
            self._poll_token = None
        self._wanted = None
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._future = None
        self._prepared.clear()
        with self._cache_lock:
            self._image_cache.clear()
            self._detection_cache.clear()
        if self._pending:
            self.after_cancel(self._pending)
            self._pending = None
        super().destroy()
