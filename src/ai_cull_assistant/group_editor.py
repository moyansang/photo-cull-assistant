from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageOps, ImageTk

from .grouping import merge_adjacent_groups, split_group_at
from .models import PhotoAsset


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


class GroupEditor(tk.Toplevel):
    ROW_H = 145
    ROW_THUMB = (105, 105)
    DETAIL_THUMB = (150, 125)

    def __init__(self, parent: tk.Misc, assets: list[PhotoAsset], on_change) -> None:
        super().__init__(parent)
        self.title("选片组编辑")
        self.geometry("1180x820")
        self.minsize(900, 620)
        self.assets = assets
        self.on_change = on_change
        self.selected_group_id: int | None = assets[0].group_id if assets else None
        self.selected_stem: str | None = None
        self._row_images: list[ImageTk.PhotoImage] = []
        self._detail_images: list[ImageTk.PhotoImage] = []
        self._build_ui()
        self._redraw_rows()
        self._redraw_detail()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(10, 10, 10, 6))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="从所选照片拆分", command=self._split_selected).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="与上一组合并", command=lambda: self._merge_neighbor(-1)).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="与下一组合并", command=lambda: self._merge_neighbor(1)).pack(side="left", padx=(0, 8))
        ttk.Label(toolbar, text="提示：先点组，再在下方点具体照片；人工修改会立即保存。") .pack(side="left", padx=12)
        self.status_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side="right")

        upper = ttk.Frame(self, padding=(10, 0, 10, 6))
        upper.pack(fill="both", expand=True)
        self.rows_canvas = tk.Canvas(upper, highlightthickness=0, background="#f4f4f4")
        ybar = ttk.Scrollbar(upper, orient="vertical", command=self.rows_canvas.yview)
        self.rows_canvas.configure(yscrollcommand=ybar.set)
        self.rows_canvas.pack(side="left", fill="both", expand=True)
        ybar.pack(side="right", fill="y")
        self.rows_canvas.bind("<MouseWheel>", self._on_mousewheel)

        detail_frame = ttk.LabelFrame(self, text="当前组全部照片（点击照片选择拆分位置）", padding=8)
        detail_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.detail_canvas = tk.Canvas(detail_frame, height=175, highlightthickness=0, background="white")
        xbar = ttk.Scrollbar(detail_frame, orient="horizontal", command=self.detail_canvas.xview)
        self.detail_canvas.configure(xscrollcommand=xbar.set)
        self.detail_canvas.pack(fill="x", expand=True)
        xbar.pack(fill="x")

    def _on_mousewheel(self, event) -> None:
        self.rows_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _redraw_rows(self) -> None:
        self.rows_canvas.delete("all")
        self._row_images.clear()
        groups = grouped_assets(self.assets)
        width = max(self.rows_canvas.winfo_width(), 1080)
        for row_index, (group_id, members) in enumerate(groups):
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
                photo = self._make_photo(asset, self.ROW_THUMB)
                if photo is not None:
                    self._row_images.append(photo)
                    self.rows_canvas.create_image(x, y0 + 10, anchor="nw", image=photo, tags=(tag, f"asset:{asset.stem}"))
                self.rows_canvas.create_text(x, y0 + 119, anchor="nw", text=asset.stem, font=("Segoe UI", 9), tags=(tag, f"asset:{asset.stem}"))
                x += 118
            if len(members) > max_preview:
                self.rows_canvas.create_text(x + 6, y0 + 54, anchor="nw", text=f"+{len(members) - max_preview}\n更多", font=("Segoe UI", 11, "bold"), tags=(tag,))

            self.rows_canvas.tag_bind(tag, "<Button-1>", lambda e, gid=group_id: self._select_group(gid))

        total_h = max(len(groups) * self.ROW_H, 1)
        self.rows_canvas.configure(scrollregion=(0, 0, width, total_h))
        self.status_var.set(f"共 {len(groups)} 组选片组")

    def _redraw_detail(self) -> None:
        self.detail_canvas.delete("all")
        self._detail_images.clear()
        if self.selected_group_id is None:
            return
        members = next((members for gid, members in grouped_assets(self.assets) if gid == self.selected_group_id), [])
        x = 10
        for asset in members:
            selected = asset.stem == self.selected_stem
            if selected:
                self.detail_canvas.create_rectangle(x - 4, 5, x + 158, 158, outline="#4b7bec", width=3)
            photo = self._make_photo(asset, self.DETAIL_THUMB)
            if photo is not None:
                self._detail_images.append(photo)
                item = self.detail_canvas.create_image(x, 10, anchor="nw", image=photo, tags=(f"detail:{asset.stem}",))
                self.detail_canvas.tag_bind(item, "<Button-1>", lambda e, stem=asset.stem: self._select_asset(stem))
            text = self.detail_canvas.create_text(x, 140, anchor="nw", text=asset.stem, font=("Segoe UI", 9), tags=(f"detail:{asset.stem}",))
            self.detail_canvas.tag_bind(text, "<Button-1>", lambda e, stem=asset.stem: self._select_asset(stem))
            x += 170
        self.detail_canvas.configure(scrollregion=(0, 0, max(x, 1), 170))

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
        self._redraw_rows()
        self._redraw_detail()

    @staticmethod
    def _make_photo(asset: PhotoAsset, box: tuple[int, int]) -> ImageTk.PhotoImage | None:
        if not asset.preview_path or not Path(asset.preview_path).exists():
            return None
        try:
            with Image.open(asset.preview_path) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                thumb = ImageOps.contain(img, box)
                return ImageTk.PhotoImage(thumb)
        except Exception:
            return None
