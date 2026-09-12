import tkinter as tk

import pytest

import ai_cull_assistant.ai_api_dialog as dialog_module
from ai_cull_assistant.ai_api_dialog import ApiConfigDialog


@pytest.fixture(scope="module")
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk is unavailable: {exc}")
    root.geometry("900x700+100+100")
    root.update()
    yield root
    if root.winfo_exists():
        root.destroy()


def _click_preset(dialog: ApiConfigDialog, preset_id: str) -> None:
    """Select an item through Tk's posted combobox list, including mouse release."""
    box = dialog.preset_box
    box.event_generate(
        "<Button-1>",
        x=max(2, box.winfo_width() - 5),
        y=max(2, box.winfo_height() // 2),
    )
    dialog.update()

    popdown = str(box.tk.call("ttk::combobox::PopdownWindow", str(box)))
    listbox = f"{popdown}.f.l"
    wanted = dialog._preset_names[preset_id]
    values = list(box.cget("values"))
    index = values.index(wanted)
    x, y, width, height = map(int, box.tk.call(listbox, "bbox", index))
    for sequence in ("<Motion>", "<ButtonPress-1>", "<ButtonRelease-1>"):
        box.tk.call(
            "event",
            "generate",
            listbox,
            sequence,
            "-x",
            x + width // 2,
            "-y",
            y + height // 2,
        )
    dialog.update()


def test_deepseek_combobox_click_keeps_dialog_open_and_does_not_refit(tmp_path, tk_root, monkeypatch):
    dialog = ApiConfigDialog(tk_root, tmp_path)
    dialog.update()
    geometry = dialog.geometry()
    fit_calls = []
    monkeypatch.setattr(dialog_module, "fit_window", lambda *args, **kwargs: fit_calls.append((args, kwargs)))
    try:
        _click_preset(dialog, "deepseek-vision")

        assert dialog.winfo_exists()
        assert not dialog._closed
        assert dialog._preset_id() == "deepseek-vision"
        assert dialog.geometry() == geometry
        assert fit_calls == []

        dialog.key_entry.focus_set()
        dialog.key_entry.event_generate("<KeyPress>", keysym="a")
        dialog.update()
        assert dialog.key_var.get() == "a"
    finally:
        if dialog.winfo_exists():
            dialog.destroy()
