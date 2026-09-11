import pytest
import tkinter as tk

from ai_cull_assistant.window_layout import ScrollableFrame, WorkArea, calculate_window_geometry


def test_preferred_size_is_centered_when_it_fits():
    geometry = calculate_window_geometry((1180, 820), WorkArea(0, 0, 1920, 1040), margin=20)

    assert (geometry.width, geometry.height, geometry.x, geometry.y) == (1180, 820, 370, 110)
    assert geometry.tk_value() == "1180x820+370+110"


def test_large_window_is_bounded_by_available_work_area():
    geometry = calculate_window_geometry((1200, 900), WorkArea(100, 40, 800, 560), margin=16)

    assert (geometry.width, geometry.height, geometry.x, geometry.y) == (768, 528, 116, 56)


def test_minimum_never_forces_window_outside_small_work_area():
    geometry = calculate_window_geometry(
        (300, 200),
        WorkArea(-1280, 0, 450, 350),
        minimum_size=(500, 400),
        margin=20,
    )

    assert (geometry.width, geometry.height, geometry.x, geometry.y) == (410, 310, -1260, 20)
    assert geometry.tk_value() == "410x310-1260+20"


def test_invalid_work_area_is_rejected():
    with pytest.raises(ValueError):
        calculate_window_geometry((800, 600), WorkArea(0, 0, 0, 600))


def test_scrollable_frame_exposes_both_axes_for_oversized_content():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk is unavailable: {exc}")
    try:
        root.geometry("240x160")
        surface = ScrollableFrame(root)
        surface.pack(fill="both", expand=True)
        oversized = tk.Frame(surface.content, width=600, height=400)
        oversized.grid()
        oversized.grid_propagate(False)
        root.update_idletasks()

        assert surface.h_scrollbar.winfo_manager() == "grid"
        assert surface.v_scrollbar.winfo_manager() == "grid"
        assert tuple(map(int, surface.canvas.cget("scrollregion").split()))[2:] == (600, 400)
    finally:
        root.destroy()
