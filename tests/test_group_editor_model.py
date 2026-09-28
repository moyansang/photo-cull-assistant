from datetime import datetime
from pathlib import Path
from threading import Event, Lock, get_ident
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image

from ai_cull_assistant.group_editor import (
    GroupEditor,
    ThumbnailLoader,
    ellipsize_text,
    grouped_assets,
    load_thumbnail,
    visible_group_range,
)
from ai_cull_assistant.models import PhotoAsset


def make_asset(stem: str, group_id: int):
    path = Path(f'/tmp/{stem}.jpg')
    return PhotoAsset(
        stem=stem,
        display_path=path,
        primary_path=path,
        raw_path=None,
        jpg_path=path,
        captured_at=datetime(2026, 1, 1),
        ext='.jpg',
        group_id=group_id,
    )


def test_grouped_assets_returns_groups_in_photo_order():
    assets = [make_asset('A', 1), make_asset('B', 1), make_asset('C', 2)]

    groups = grouped_assets(assets)

    assert [(gid, [a.stem for a in members]) for gid, members in groups] == [
        (1, ['A', 'B']),
        (2, ['C']),
    ]


def test_large_group_editor_only_renders_rows_near_viewport():
    row_height = GroupEditor.ROW_H
    indexes = visible_group_range(450, viewport_top=row_height * 220, viewport_height=820, row_height=row_height)

    assert 218 <= indexes.start <= 220
    assert indexes.stop - indexes.start < 20
    assert 220 in indexes


def test_long_photo_names_are_ellipsized_in_the_middle():
    text = 'P1100245_中文与English_非常长的文件名'

    fitted = ellipsize_text(text, lambda value: len(value) * 10, 120)

    assert len(fitted) <= 12
    assert fitted.startswith('P1100')
    assert fitted.endswith('文件名')
    assert '…' in fitted


def test_group_editor_fixed_actions_remain_visible_at_900_by_600(tmp_path, monkeypatch):
    from ai_cull_assistant import window_layout
    monkeypatch.setattr(window_layout, 'work_area_for',
                        lambda _widget: window_layout.WorkArea(0, 0, 900, 600))
    path = tmp_path / 'preview.jpg'
    Image.new('RGB', (160, 120), 'white').save(path)
    assets = [make_asset(chr(ord('A') + index), 1) for index in range(8)]
    for asset in assets:
        asset.preview_path = path
        asset.primary_path = path
    root = tk.Tk()
    try:
        editor = GroupEditor(root, assets, lambda: None)
        root.update()
        wanted = {'从所选照片拆分', '与上一组合并', '与下一组合并'}
        buttons = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button) and child.cget('text') in wanted:
                    buttons.append(child)
                walk(child)

        walk(editor)
        assert {button.cget('text') for button in buttons} == wanted
        for button in buttons:
            assert button.winfo_viewable()
            assert button.winfo_rooty() + button.winfo_height() <= editor.winfo_rooty() + editor.winfo_height()
        assert editor.rows_canvas.winfo_height() >= 80
        assert editor.detail_canvas.winfo_viewable()
        assert editor.rows_canvas.winfo_rootx() < editor.detail_canvas.winfo_rootx()
        assert editor.detail_canvas.winfo_height() >= 250
        editor._redraw_detail()
        first_box = editor.detail_canvas.bbox('detail:A')
        second_box = editor.detail_canvas.bbox('detail:B')
        fifth_box = editor.detail_canvas.bbox('detail:E')
        assert first_box is not None and second_box is not None and fifth_box is not None
        assert first_box[1] == second_box[1]
        assert fifth_box[1] > first_box[1]
        assert editor.detail_canvas.cget('yscrollcommand')

        editor.close_page()
        root.update()

        host = ttk.Frame(root)
        host.pack(fill='both', expand=True)
        embedded_assets = [make_asset('EA', 1), make_asset('EB', 1)]
        for asset in embedded_assets:
            asset.preview_path = path
            asset.primary_path = path
        embedded = GroupEditor(host, embedded_assets, lambda: None, embedded=True)
        embedded.pack(fill='both', expand=True)
        root.update()

        assert embedded.master is host
        assert embedded.winfo_toplevel() is root
        assert not hasattr(embedded, '_page_chrome')
        assert embedded.winfo_manager() == 'pack'

        embedded_assets[1].group_id = 2
        embedded.on_activate()
        assert [group_id for group_id, _members in embedded._groups] == [1, 2]
        assert [asset.stem for asset in embedded._members_by_group[2]] == ['EB']

        embedded.close_page()
        assert embedded.winfo_exists()
        assert embedded.winfo_manager() == ''
    finally:
        root.destroy()


def test_thumbnail_decode_returns_detached_bounded_pil_image(tmp_path, monkeypatch):
    preview = tmp_path / 'preview.jpg'
    Image.new('RGB', (200, 100), 'white').save(preview)
    asset = make_asset('A', 1)
    monkeypatch.setattr('ai_cull_assistant.preview.ensure_preview', lambda _asset: preview)

    thumb = load_thumbnail(asset, (50, 50))

    assert thumb is not None
    assert thumb.size == (50, 25)
    preview.unlink()
    assert thumb.getpixel((0, 0)) == (255, 255, 255)
    thumb.close()


def test_thumbnail_loader_runs_off_thread_and_bounds_pending_work():
    started = Event()
    release = Event()
    worker_threads: list[int] = []

    def blocking_worker(_asset, _box):
        worker_threads.append(get_ident())
        started.set()
        assert release.wait(2)
        return Image.new('RGB', (4, 4), 'white')

    loader = ThumbnailLoader(max_workers=1, max_pending=2, worker=blocking_worker)
    asset = make_asset('A', 1)
    keys = [(id(asset), (size, size)) for size in (10, 11, 12)]
    try:
        assert loader.request(keys[0], asset, (10, 10))
        assert started.wait(1)
        assert loader.request(keys[1], asset, (11, 11))
        assert not loader.request(keys[2], asset, (12, 12))
        assert loader.pending_count == 2
        assert worker_threads[0] != get_ident()

        loader.cancel_except({keys[0]})
        assert loader.pending_count == 1
        assert loader.request(keys[2], asset, (12, 12))
        assert loader.pending_count == 2
        release.set()

        ready = []
        deadline = time.monotonic() + 2
        while len(ready) < 2 and time.monotonic() < deadline:
            ready.extend(loader.pop_completed())
            time.sleep(.01)
        assert {key for key, _image in ready} == {keys[0], keys[2]}
        for _key, image in ready:
            assert image is not None
            image.close()
    finally:
        release.set()
        loader.close()


def test_thumbnail_loader_serializes_one_asset_while_other_assets_overlap():
    release = Event()
    different_assets_overlap = Event()
    guard = Lock()
    active: dict[int, int] = {}
    max_active: dict[int, int] = {}

    def tracked_worker(asset, _box):
        asset_key = id(asset)
        with guard:
            active[asset_key] = active.get(asset_key, 0) + 1
            max_active[asset_key] = max(max_active.get(asset_key, 0), active[asset_key])
            if sum(count > 0 for count in active.values()) >= 2:
                different_assets_overlap.set()
        assert release.wait(2)
        with guard:
            active[asset_key] -= 1
        return Image.new('RGB', (4, 4), 'white')

    loader = ThumbnailLoader(max_workers=2, max_pending=3, worker=tracked_worker)
    first = make_asset('A', 1)
    second = make_asset('B', 1)
    keys = [
        (id(first), (10, 10)),
        (id(first), (11, 11)),
        (id(second), (12, 12)),
    ]
    try:
        assert loader.request(keys[0], first, (10, 10))
        assert loader.request(keys[1], first, (11, 11))
        assert loader.request(keys[2], second, (12, 12))
        assert different_assets_overlap.wait(1)
        assert max_active[id(first)] == 1
        assert max_active[id(second)] == 1
        release.set()

        ready = []
        deadline = time.monotonic() + 2
        while len(ready) < 3 and time.monotonic() < deadline:
            ready.extend(loader.pop_completed())
            time.sleep(.01)
        assert {key for key, _image in ready} == set(keys)
        assert max_active[id(first)] == 1
        for _key, image in ready:
            assert image is not None
            image.close()
    finally:
        release.set()
        loader.close()


def test_adaptive_grid_enlarges_small_groups_and_scrolls_large_groups():
    from ai_cull_assistant.group_editor import detail_grid_layout
    pair = detail_grid_layout(840, 360, 2, 20)
    eight = detail_grid_layout(840, 360, 8, 20)
    bigger = detail_grid_layout(1100, 520, 2, 20)
    assert pair[0] == 2
    assert pair[3][0] > 300 and pair[3][1] > 250
    assert min(pair[3]) > min(eight[3])
    assert bigger[3][0] > pair[3][0] and bigger[3][1] > pair[3][1]
    many = detail_grid_layout(840, 360, 400, 20)
    assert min(many[3]) >= 120
    assert ((400 + many[0] - 1) // many[0]) * many[2] > 360
    for count in (0, 1, 2, 8, 400):
        columns, cell, row, box = detail_grid_layout(300, 200, count, 20)
        assert columns * cell <= 300
        assert box[0] < cell and box[1] < row
