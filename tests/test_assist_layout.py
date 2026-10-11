"""Exercise the actual face-assist window with crowded DPI/window combinations."""
import time
import tkinter as tk
from tkinter import ttk

import pytest
from PIL import Image

from ai_cull_assistant.group_face_assist import AssistImage, AssistTarget
from ai_cull_assistant.group_face_assist_dialog import GroupFaceAssistDialog
from ai_cull_assistant.ui_style import COLORS


def settle(root):
    for _ in range(15):
        root.update()
        time.sleep(.01)


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@pytest.mark.parametrize('size,dpi', [((836, 536), 96), ((836, 660), 192), ((960, 660), 144),
                                     ((1000, 700), 192), ((960, 600), 192), ((1280, 760), 120),
                                     ((1800, 960), 192)])
def test_assist_photo_list_actions_and_tabs_remain_usable(tmp_path, monkeypatch, size, dpi):
    root = tk.Tk()
    original = root.tk.call('tk', 'scaling')
    root.tk.call('tk', 'scaling', dpi / 72)
    monkeypatch.setattr(GroupFaceAssistDialog, '_start_worker', lambda self: None)
    path = tmp_path / 'portrait.jpg'
    Image.new('RGB', (600, 900), '#7190A0').save(path)
    targets = [AssistTarget(str(i), f'photo-{i}', str(path)) for i in range(20)]
    dialog = None
    try:
        dialog = GroupFaceAssistDialog(root, AssistImage('ref', 'reference', str(path)),
                                       (.3, .2, .3, .3), targets, lambda _: None)
        dialog.geometry(f'{size[0]}x{size[1]}+0+0')
        settle(root)
        # At least two complete candidate rows remain visible on opening.
        rowheight = int(ttk.Style(root).lookup('Assist.Treeview', 'rowheight'))
        assert dialog.tree.winfo_height() >= rowheight * 2 + 25
        assert dialog.preview.winfo_width() >= 180
        assert dialog.preview.winfo_height() >= 100
        assert dialog.preview.cget('background') == COLORS['bg']
        assert dialog.reference_preview.cget('background') == COLORS['bg']
        assert dialog.inset.cget('background') == COLORS['bg']
        # Every visible action keeps its full natural size and is inside the window.
        for widget in descendants(dialog):
            if isinstance(widget, ttk.Button) and widget.winfo_viewable():
                assert widget.winfo_width() >= widget.winfo_reqwidth(), widget.cget('text')
                assert widget.winfo_rootx() >= dialog.winfo_rootx()
                assert widget.winfo_rootx() + widget.winfo_width() <= dialog.winfo_rootx() + dialog.winfo_width(), widget.cget('text')
                assert widget.winfo_rooty() + widget.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height(), widget.cget('text')
        # Narrow layouts expose both reference management and candidate details via tabs.
        if dialog._compact:
            dialog._preview_tabs.select(dialog._details)
        settle(root)
        assert dialog.reference_preview.winfo_viewable()
        assert dialog._details.winfo_manager() == ('notebook' if dialog._compact else 'grid')
        dialog._details.select(1)
        settle(root)
        assert dialog.candidate_picker.winfo_viewable()
        # Returning to a wide window restores a separate details rail, without losing selection.
        dialog.geometry('1900x950+0+0')
        settle(root)
        assert not dialog._compact
        assert dialog.tree.selection() == ('0',)
    finally:
        if dialog is not None:
            dialog.destroy()
        root.tk.call('tk', 'scaling', original)
        root.destroy()
