import pytest

from ai_cull_assistant.window_layout import WorkArea, calculate_window_geometry


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
