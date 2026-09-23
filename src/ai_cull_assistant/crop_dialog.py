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
from .group_face_assist import (AssistImage, collect_targets, reference_box_from_entry,
                                asset_signature, normalized_box_ok)
from .group_face_assist_dialog import GroupFaceAssistDialog
from .subject import face_crop, detail_features, detail_features_list
from .preview import ensure_preview
from .ui_help import install_control_help, install_page_chrome
from .ui_style import apply_page, set_button_style, COLORS
from .window_layout import fit_window


def same_face_box(first, second):
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


def apply_face_box(entry, selected_boxes, box, preview_version):
    """Single write path for a user-confirmed face box.

    Shared by the manual pointer-drawing route and the group assist accept so
    both keep the long-standing field format: an untouched photo stores the
    legacy ``manual_face``, any photo that already tracks participants appends
    to ``selected_faces``, and both stamp ``preview_version`` from the live
    preview.
    """
    if not selected_boxes and 'selected_faces' not in entry:
        entry['manual_face'] = [float(value) for value in box]
    else:
        faces = [tuple(value) for value in selected_boxes]
        if not any(same_face_box(current, box) for current in faces):
            faces.append(tuple(box))
        entry['selected_faces'] = [list(current) for current in faces]
        entry.pop('manual_face', None)
    if preview_version:
        entry['preview_version'] = preview_version
    entry.pop('hidden', None)


class CropDialog(tk.Toplevel):
    def __init__(self, parent, assets, settings, on_save):
        super().__init__(parent)
        self.title("检测/调整人脸框")
        self.transient(parent)
        self.assets = [a for a in assets if a.preview_path or a.primary_path]
        self.edits = deepcopy(settings.photos)
        self._original_photos = deepcopy(settings.photos)
        self._original_confidence = settings.detection_confidence
        self._assist_dialog = None
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
        self._last_confidence = max(.7, min(.95, float(settings.detection_confidence)))
        self.confidence = tk.StringVar(value=f"{self._last_confidence:.2f}")
        self.ratio = tk.StringVar(value=settings.aspect_ratio)
        self.person = tk.StringVar(value="自动主体")
        self._pending = None
        install_page_chrome(self, "faces")
        # Fixed footer and compact controls leave the preview all remaining
        # height.  This dialog intentionally has no outer scrolling surface.
        actions = ttk.Frame(self, padding=(16, 8))
        actions.pack(side="bottom", fill="x")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=6)
        ttk.Button(
            actions,
            text="重新扫描修改过的图片" if self.assets else "保存设置",
            command=self.save,
        ).pack(side="right")
        body = ttk.Frame(self, padding=(12, 6))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        controls = ttk.Frame(body)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(controls, text="全局置信度").pack(side="left")
        self.confidence_spinbox = ttk.Spinbox(
            controls, from_=.7, to=.95, increment=.01, textvariable=self.confidence,
            width=6, format="%.2f",
        )
        self.confidence_spinbox.pack(side="left", padx=(6, 18))
        self.confidence_spinbox.bind('<FocusOut>', self._commit_confidence)
        self.confidence_spinbox.bind('<Return>', self._commit_confidence)
        ttk.Label(controls, text="当前人物").pack(side="left")
        self.person_picker = ttk.Combobox(
            controls,
            textvariable=self.person,
            values=("自动主体",),
            state="readonly",
            width=13,
        )
        self.person_picker.pack(side="left", padx=(6, 18))
        self.person_picker.bind("<<ComboboxSelected>>", self.select_person)
        ttk.Label(controls, text="裁切比例").pack(side="left")
        self.ratio_picker = ttk.Combobox(
            controls, textvariable=self.ratio, values=("124:150", "1:1", "3:4"),
            state="readonly", width=10,
        )
        self.ratio_picker.pack(side="left", padx=(6, 0))
        self.caption = ttk.Label(body)
        self.caption.grid(row=1, column=0, sticky="ew", pady=(2, 4))
        self.canvas = tk.Canvas(body, width=1, height=400, background=COLORS["photo"], highlightthickness=0)
        self.canvas.grid(row=2, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", self.schedule_preview)
        self.canvas.bind('<ButtonPress-1>', self.pointer_down)
        self.canvas.bind('<B1-Motion>', self.pointer_move)
        self.canvas.bind('<ButtonRelease-1>', self.pointer_up)
        self.canvas.bind('<MouseWheel>', self.mouse_wheel)
        self.canvas.bind('<Button-4>', self.mouse_wheel)
        self.canvas.bind('<Button-5>', self.mouse_wheel)
        lower = ttk.Frame(body)
        lower.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        manual = ttk.Frame(lower)
        manual.pack(fill="x", pady=(0, 4))
        ttk.Button(manual, text="恢复本张自动选脸", command=self.auto_face).pack(side="left", padx=(0, 6))
        ttk.Button(manual, text="去除所有框选", command=self.clear_face_selection).pack(side="left", padx=6)
        navigation = ttk.Frame(lower)
        navigation.pack(fill="x")
        navigation.columnconfigure((0, 1, 2), weight=1, uniform="crop-actions")
        for column, (label, command) in enumerate((
            ("上一张", lambda: self.navigate(-1)),
            ("下一张", lambda: self.navigate(1)),
            ("重置当前裁切", self.reset),
            ("上一张未标记", self.previous_unmarked),
            ("下一张未标记", self.next_unmarked),
            ("补齐人脸", self.choose_assist_groups),
        )):
            ttk.Button(navigation, text=label, command=command).grid(
                row=column // 3, column=column % 3, sticky="ew", padx=3, pady=2)
        for variable in (self.scale, self.shift, self.offset_x, self.ratio):
            variable.trace_add("write", self.schedule_crop_preview)
        self.confidence.trace_add("write", self.schedule_preview)
        fit_window(self, (900, 680), minimum_size=(620, 520), parent=parent)
        self.update_idletasks()
        self.load_current()
        self.render()
        install_control_help(self, "faces")
        apply_page(self)
        self.grab_set()

    def settings(self):
        return CropSettings.from_dict(dict(
            scale_factor=round(self.scale.get(), 3),
            shift_factor=round(self.shift.get(), 3),
            offset_x_factor=round(self.offset_x.get(), 3),
            aspect_ratio=self.ratio.get(),
            detection_confidence=round(self._confidence_value(), 2),
        ))

    def _confidence_value(self):
        try:
            value = float(self.confidence.get())
        except (TypeError, ValueError, tk.TclError):
            return self._last_confidence
        if not np.isfinite(value):
            return self._last_confidence
        value = max(.7, min(.95, value))
        self._last_confidence = value
        return value

    def _commit_confidence(self, _event=None):
        self.confidence.set(f"{self._confidence_value():.2f}")
        return 'break' if _event is not None and getattr(_event, 'keysym', '') == 'Return' else None

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

    def previous_unmarked(self):
        self.next_unmarked(direction=-1)

    def next_unmarked(self, direction=1):
        if not self.assets:
            self.caption.configure(text="请先扫描照片，再查找未标记人脸。")
            return
        self.store_current()
        settings = self.global_settings()
        for step in range(1, len(self.assets) + 1):
            index = (self.index + direction * step) % len(self.assets)
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

    def choose_assist_groups(self):
        self.assist_group_faces()

    @staticmethod
    def _reference_box_for_entry(entry, active_face_key=None):
        selected = entry.get('selected_faces')
        if isinstance(selected, list) and selected:
            keyed = [(face_box_key(box), box) for box in selected]
            if active_face_key:
                box = next((box for key, box in keyed if key == active_face_key), None)
                return (box, 'ok') if box is not None else (None, 'missing')
            if len(keyed) == 1:
                return keyed[0][1], 'ok'
            return None, 'multiple'
        return reference_box_from_entry(entry)

    def assist_group_faces(self, group_ids=None):
        """Open the same-group assist window using the current photo as reference."""
        if self._assist_dialog is not None and self._assist_dialog.winfo_exists():
            self._assist_dialog.lift()
            return
        if self._loading:
            return
        if not self.assets:
            messagebox.showinfo("补齐人脸", "请先扫描照片。", parent=self)
            return
        self.store_current()
        settings = self.global_settings()
        asset = self.assets[self.index]
        key = settings.key(asset)
        entry = self.edits.get(key, {})
        box, state = self._reference_box_for_entry(entry, self._active_face_key)
        if state == 'multiple':
            messagebox.showinfo("补齐人脸", "请先在“当前人物”中选定要查找的人。", parent=self)
            return
        if box is None:
            messagebox.showinfo("补齐人脸", "请先在当前照片上画好或确认一个人脸框，再查找候选。", parent=self)
            return
        if not asset.preview_path or not Path(asset.preview_path).is_file():
            messagebox.showinfo("补齐人脸", "参考照片的预览不可读，无法进行匹配。", parent=self)
            return
        siblings = list(self.assets)
        targets = collect_targets(asset, siblings, self.edits, settings.key, allow_cross_group=True)
        if not targets:
            messagebox.showinfo("补齐人脸", "整个工作区没有需要补齐的人脸。", parent=self)
            return
        self._assist_assets = {settings.key(a): a for a in siblings}
        self._assist_signatures = {k: asset_signature(a) for k, a in self._assist_assets.items()}
        self._assist_preview_cache_dir = (Path(asset.preview_path).parent.parent
                                          if Path(asset.preview_path).parent.name in ('v04', 'v05')
                                          else Path(asset.preview_path).parent)
        self._assist_reference_face_key = face_box_key(box)
        reference = AssistImage(
            key=key, stem=asset.stem, preview_path=str(asset.preview_path))
        # The crop dialog owns an application-wide grab; hand it to the child
        # window and take it back when that window closes.
        self.grab_release()
        self._assist_dialog = GroupFaceAssistDialog(
            self, reference, tuple(box), targets,
            apply_items=self.apply_assist_proposals,
            on_close=self._assist_dialog_closed,
            detection_confidence=settings.detection_confidence,
            target_assets={key: deepcopy(value) for key, value in self._assist_assets.items()},
            preview_cache_dir=self._assist_preview_cache_dir,
            on_target_prepared=self._assist_target_prepared,
        )

    def _assist_target_prepared(self, key, preview_path):
        """Publish a worker-built preview only while its source is unchanged."""
        asset = getattr(self, '_assist_assets', {}).get(key)
        expected = getattr(self, '_assist_signatures', {}).get(key)
        if asset is None or expected is None:
            return
        if not preview_path or not Path(preview_path).is_file():
            return
        current = asset_signature(asset)
        if current[:3] != expected[:3]:
            return
        resolved = Path(preview_path).resolve()
        expected_preview = expected[3]
        expected_missing = len(expected_preview) == 2 and expected_preview[1] is None
        same_intended_path = expected_missing and expected_preview[0] != 'None' and (
            Path(expected_preview[0]).resolve() == resolved)
        cache_dir = getattr(self, '_assist_preview_cache_dir', None)
        inside_cache = False
        if expected_missing and expected_preview[0] == 'None' and cache_dir is not None:
            try:
                relative = resolved.relative_to(Path(cache_dir).resolve())
                inside_cache = relative.parts[:1] in (('v04',), ('v05',)) and resolved.stem == asset.stem
            except ValueError:
                pass
        if not (same_intended_path or inside_cache):
            return
        asset.preview_path = resolved
        self._assist_signatures[key] = asset_signature(asset)

    def _assist_dialog_closed(self):
        self._assist_dialog = None
        if not self._closed and self.winfo_exists():
            self.grab_set()

    def apply_assist_proposals(self, reference_key, reference_box, items):
        """Adopt confirmed candidates after re-checking the start snapshots."""
        current_box, state = self._reference_box_for_entry(
            self.edits.get(reference_key, {}), getattr(self, '_assist_reference_face_key', None))
        try:
            unchanged = state == 'ok' and [float(v) for v in current_box] == [
                float(v) for v in reference_box]
        except (TypeError, ValueError):
            unchanged = False
        reference_asset = self._assist_assets.get(reference_key)
        unchanged = (unchanged and reference_asset is not None
                     and asset_signature(reference_asset) == self._assist_signatures.get(reference_key))
        if not unchanged:
            messagebox.showwarning(
                "补齐人脸",
                "参考照片的人脸框已修改，本轮候选全部作废，未写入任何结果。",
                parent=self,
            )
            return 0, []
        applied = 0
        skipped = []
        for item in items:
            key = item['target_key']
            entry = self.edits.get(key, {})
            if json.dumps(entry, sort_keys=True, default=str) != item['snapshot']:
                skipped.append((item['stem'], "候选生成后该照片已被修改"))
                continue
            asset = getattr(self, '_assist_assets', {}).get(key)
            if asset is None:
                skipped.append((item['stem'], "未找到对应照片"))
                continue
            if (asset_signature(asset) != self._assist_signatures.get(key)
                    or asset.group_id != self._assist_signatures[key][0]):
                skipped.append((item['stem'], "照片、预览或分组已改变"))
                continue
            if not normalized_box_ok(item['box']):
                skipped.append((item['stem'], "候选框无效"))
                continue
            apply_face_box(self.edits.setdefault(key, {}), [], item['box'],
                           self._preview_version(asset))
            applied += 1
        if applied:
            self.load_current()
            self.render()
        return applied, skipped

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
                self.canvas.create_text(final_x, max(10, (canvas_height - tile.height) / 2 - 12), text=inset_label, fill=COLORS['photo_text'])
            else:
                self.canvas.create_text(final_x, canvas_height / 2, text="未检测到可靠人脸\n本张不显示小窗", justify="center", fill=COLORS['photo_text'])
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
            self.canvas.create_text(self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2, text=f"预览不可用：{exc}", fill=COLORS['photo_text'])

    def global_settings(self):
        return CropSettings(detection_confidence=round(self._confidence_value(), 2), photos=deepcopy(self.edits))

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

    def clear_face_selection(self):
        if not self.assets:
            return
        self.store_current()
        entry = self.current_entry()
        for name in ('manual_face', 'face_crops', 'hidden'):
            entry.pop(name, None)
        entry['selected_faces'] = []
        self._active_face_key = None
        self._person_dirty = False
        self._crop_selected = False
        self.load_current()
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
            apply_face_box(entry, self._selected_boxes, box,
                           self._preview_version(self.assets[self.index]))
            if 'selected_faces' in entry:
                self._active_face_key = face_box_key(box)
                self._person_dirty = False
            self.load_current()
            self.render()

    @staticmethod
    def _same_face(first, second):
        return same_face_box(first, second)

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

    @staticmethod
    def _meaningful_entry(entry):
        # Same filter the app uses to decide whether a photo really changed,
        # so neutral defaults never count as a face modification.
        return {k: v for k, v in entry.items()
                if (k != 'offset_x_factor' or v != 0) and (k != 'preview_version' or v != 'v04')}

    def _changed_face_assets(self, settings):
        return [
            asset for asset in self.assets
            if self._meaningful_entry(self._original_photos.get(settings.key(asset), {}))
            != self._meaningful_entry(settings.photos.get(settings.key(asset), {}))
            or settings.detection_confidence != self._original_confidence
        ]

    def save(self):
        try:
            self.store_current()
            settings = self.global_settings()
            changed = self._changed_face_assets(settings)
            reviewed = sum(1 for asset in changed if getattr(asset, 'ai_focus_result', None) is not None)
            if reviewed and not messagebox.askyesno(
                "保存人脸修改",
                f"将保存 {len(changed)} 张照片的人脸修改，其中 {reviewed} 张已有 AI 清晰度复核结论。"
                "保存后这些照片需重新复核；本次不会自动调用 API。确定保存？",
                parent=self,
            ):
                return
            self.on_save(settings)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self.destroy()

    def destroy(self):
        self._closed = True
        if self._assist_dialog is not None:
            self._assist_dialog.destroy()
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
