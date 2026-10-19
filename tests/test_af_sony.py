"""Synthetic Sony parser tests plus transcribed public-pair metadata only.

No source photos or third-party implementation are bundled. The transcribed
fixture is not evidence of optical focus quality or camera-LCD calibration.
"""
from copy import deepcopy

import pytest

from ai_cull_assistant import af_sony as sony


def tags(**changes):
    # DPReview ILCE-6600 JPEG 6703887858 / ARW 2195875488, metadata values only.
    raw = {
        "IFD0:Make": "SONY", "IFD0:Model": "ILCE-6600", "IFD0:Orientation": 8,
        "IFD1:Copy1:Orientation": 8,
        "Sony:FocusMode": 3, "Sony:Copy1:FocusMode": 3,
        "Sony:AFAreaMode": 14, "Sony:AFAreaModeSetting": 0,
        "Sony:FocusLocation": "6000 4000 3928 1700",
        "Sony:FullImageSize": "6000 4000",
        "Sony:SonyImageWidth": 6000, "Sony:SonyImageHeight": 4000,
        "ExifIFD:ExifImageWidth": 6000, "ExifIFD:ExifImageHeight": 4000,
        "File:ImageWidth": 6000, "File:ImageHeight": 4000,
        "ExifIFD:DigitalZoomRatio": 1, "Sony:AutoPortraitFramed": 0,
    }
    raw.update(changes)
    return raw


def rejected(raw):
    result = sony.diagnostics(raw)
    coordinates = result["coordinates"]
    assert coordinates["status"] == "unknown"
    assert coordinates["oriented_center_xy_hypothesis"] is None
    assert coordinates["raw_bbox_xywh_hypothesis"] is None
    assert coordinates["oriented_bbox_xywh_hypothesis"] is None
    assert coordinates["coordinate_frame_validated"] is False
    assert coordinates["focus_confirmed"] is None
    assert coordinates["usable_for_subject_selection"] is False
    assert coordinates["usable_for_keep_reject"] is False
    return result


def test_transcribed_real_fixture_retains_playback_semantics_and_point_only():
    raw = tags()
    before = deepcopy(raw)
    result = sony.diagnostics(raw)
    assert raw == before
    assert sony.matches_camera(raw)
    camera = result["camera"]
    assert camera["vendor"] == "sony"
    assert camera["display_profile"] == sony.PROFILE == "sony_ilce_6600_focus_location_v1"
    assert camera["is_panasonic"] is False
    assert camera["real_camera_validated"] is False
    assert result["mode"]["focus_mode_name"] == "AF-C"
    assert result["mode"]["FocusMode"]["raw_fields"] == {"Sony:FocusMode": 3, "Sony:Copy1:FocusMode": 3}
    assert result["mode"]["FocusMode"]["instance_resolution"] == "agreeing_model_scoped_focus_mode_instances"
    assert result["mode"]["AFAreaMode"]["raw_value"] == 14
    assert result["mode"]["single_area_candidate"] is True
    assert "playback" in result["mode"]["candidate_meaning"]
    coordinates = result["coordinates"]
    assert coordinates["status"] == "not_validated"
    assert coordinates["meaning"] == "camera_playback_focus_location"
    assert coordinates["reference_frame_size"] == [6000, 4000]
    assert coordinates["center"]["value"] == pytest.approx([3928 / 6000, 1700 / 4000])
    assert coordinates["center"]["raw_fields"] == {"Sony:FocusLocation": "6000 4000 3928 1700"}
    assert coordinates["oriented_center_xy_hypothesis"] == pytest.approx([.425, .3453333333333333])
    assert coordinates["area_size"]["reason"] == "missing"
    assert coordinates["area_size"]["value"] is None
    assert coordinates["raw_bbox_xywh_hypothesis"] is None
    assert coordinates["oriented_bbox_xywh_hypothesis"] is None
    assert coordinates["focus_confirmed"] is None
    assert coordinates["usable_for_subject_selection"] is False
    assert coordinates["usable_for_keep_reject"] is False
    assert result["dimensions"]["status"] == "not_validated"
    assert result["dimensions"]["camera_original_authenticated"] is False
    assert result["subject_association"]["person_id"] is None


@pytest.mark.parametrize("orientation, center, box", [
    (1, [.25, .6], [.2, .5, .1, .2]),
    (2, [.75, .6], [.7, .5, .1, .2]),
    (3, [.75, .4], [.7, .3, .1, .2]),
    (4, [.25, .4], [.2, .3, .1, .2]),
    (5, [.6, .25], [.5, .2, .2, .1]),
    (6, [.4, .25], [.3, .2, .2, .1]),
    (7, [.4, .75], [.3, .7, .2, .1]),
    (8, [.6, .75], [.5, .7, .2, .1]),
])
def test_all_eight_orientations_transform_location_and_recorded_frame_once(orientation, center, box):
    result = sony.diagnostics(tags(**{
        "IFD0:Orientation": orientation, "IFD1:Copy1:Orientation": orientation,
        "Sony:FocusLocation": "6000 4000 1500 2400", "Sony:FocusFrameSize": "600 800 257",
    }))
    coordinates = result["coordinates"]
    assert coordinates["status"] == "not_validated"
    assert coordinates["center"]["value"] == [.25, .6]
    assert coordinates["area_size"]["value"] == [.1, .2]
    assert coordinates["area_size"]["validity"] == 257
    assert coordinates["raw_bbox_xywh_hypothesis"] == pytest.approx([.2, .5, .1, .2])
    assert coordinates["oriented_center_xy_hypothesis"] == pytest.approx(center)
    assert coordinates["oriented_bbox_xywh_hypothesis"] == pytest.approx(box)


@pytest.mark.parametrize("make, model", [
    ("Sony", "ILCE-6600"), (" sony ", "ILCE-6600"), ("SONY", "ILCE-6600"),
])
def test_identity_supported_make_case_but_exact_model(make, model):
    assert sony.matches_camera(tags(**{"IFD0:Make": make, "IFD0:Model": model}))


@pytest.mark.parametrize("make, model", [
    ("SONY", "ILCE-6700"), ("SONY", "ILCE-7M3"), ("SONY", "DSC-RX100M7"),
    ("SONY", "ilce-6600"), ("SONY", "ILCE-6600 "), ("Canon", "ILCE-6600"),
    ("Sony Corporation", "ILCE-6600"), (None, "ILCE-6600"), ("SONY", None),
])
def test_camera_allowlist_fails_closed(make, model):
    raw = tags(**{"IFD0:Make": make, "IFD0:Model": model})
    assert sony.matches_camera(raw) is False
    assert rejected(raw)["camera"]["display_profile"] is None


def test_identity_does_not_fall_back_to_thumbnail_or_unqualified_tags():
    raw = tags()
    del raw["IFD0:Make"], raw["IFD0:Model"]
    raw.update({"IFD1:Make": "SONY", "IFD1:Model": "ILCE-6600", "Make": "SONY", "Model": "ILCE-6600"})
    assert sony.matches_camera(raw) is False
    rejected(raw)


def test_matching_identity_instances_allowed_conflicts_rejected():
    raw = tags(**{"IFD0:Copy1:Make": "SONY", "IFD0:Copy1:Model": "ILCE-6600"})
    assert sony.matches_camera(raw)
    raw["IFD0:Copy1:Model"] = "ILCE-6700"
    assert sony.matches_camera(raw) is False
    assert rejected(raw)["camera"]["model_issue"] == "ambiguous_instances"


@pytest.mark.parametrize("mode, name", [(2, "AF-S"), (3, "AF-C"), (4, "AF-A")])
def test_sony_numeric_modes_are_not_panasonic_modes(mode, name):
    result = sony.diagnostics(tags(**{"Sony:FocusMode": mode, "Sony:Copy1:FocusMode": str(mode)}))
    assert result["mode"]["focus_mode_name"] == name
    assert result["coordinates"]["status"] == "not_validated"


@pytest.mark.parametrize("mode, reason", [(0, "manual_focus"), (6, "direct_manual_focus"), (7, "focus_mode_ambiguous_instances")])
def test_manual_dmf_and_non_ilce_af_d_fail_closed(mode, reason):
    result = rejected(tags(**{"Sony:FocusMode": mode, "Sony:Copy1:FocusMode": mode}))
    assert reason in result["mode"]["reasons"]


@pytest.mark.parametrize("mode", [None, True, False, 1, 5, 8, 99, "AF-C", "3.0000000000000001", 3.5, float("inf"), float("nan"), 10**1000])
def test_unknown_or_invalid_focus_mode_is_not_autofocus(mode):
    raw = tags(**{"Sony:FocusMode": mode})
    del raw["Sony:Copy1:FocusMode"]
    result = rejected(raw)
    assert result["mode"]["FocusMode"]["raw_fields"] == {"Sony:FocusMode": mode}
    assert result["mode"]["single_area_candidate"] is False


def test_missing_focus_mode_is_not_guessed_from_tracking_or_location():
    raw = tags()
    del raw["Sony:FocusMode"], raw["Sony:Copy1:FocusMode"]
    assert rejected(raw)["mode"]["FocusMode"]["issue"] == "missing"


@pytest.mark.parametrize("field, value", [
    ("Sony:AFAreaMode", 255), ("Sony:Copy1:AFAreaMode", 14),
    ("Sony:AFAreaMode", "Tracking"), ("Sony:AFAreaModeSetting", True),
    ("Sony:Copy1:AFAreaModeSetting", 0),
])
def test_optional_area_mode_manual_contradiction_or_malformed_instances_block(field, value):
    assert rejected(tags(**{field: value}))["mode"]["single_area_candidate"] is False


def test_area_modes_are_optional_and_never_used_as_location_fallback():
    raw = tags()
    del raw["Sony:AFAreaMode"], raw["Sony:AFAreaModeSetting"]
    assert sony.diagnostics(raw)["coordinates"]["status"] == "not_validated"


@pytest.mark.parametrize("value", [0, 2, 4, 6, 99, "3.0000000000000001", True])
def test_conflicting_focus_mode_instances_fail_closed(value):
    result = rejected(tags(**{"Sony:Copy1:FocusMode": value}))
    assert result["mode"]["FocusMode"]["issue"] == "ambiguous_instances"


@pytest.mark.parametrize("value", [
    None, "", "6000 4000 3928", "6000 4000 3928 1700 1", "n/a", "0 0 0 0",
    "-6000 4000 3928 1700", "6000 0 3928 1700", "65536 4000 3928 1700",
    "6000 4000 -1 1700", "6000 4000 6000 1700", "6000 4000 3928 4000",
    "6000 4000 65535 65535", "6000 4000 nan 1700", "6000 4000 inf 1700",
    "6000 4000 3928.1 1700", "6000.0000000000000001 4000 3928 1700",
    [6000, 4000, True, 1700], [6000, 4000, [3928], 1700], {"x": 3928, "y": 1700},
])
def test_bad_location_bounds_shapes_and_nonfinite_values_are_never_clamped(value):
    result = rejected(tags(**{"Sony:FocusLocation": value}))
    assert result["coordinates"]["center"]["value"] is None


@pytest.mark.parametrize("location", ["6000 4000 0 0", "6000 4000 5999 3999", [6000, 4000, 3928, 1700]])
def test_valid_pixel_limits_and_array_encoding(location):
    assert sony.diagnostics(tags(**{"Sony:FocusLocation": location}))["coordinates"]["status"] == "not_validated"


def test_missing_location_does_not_fall_back_to_face_or_flexible_spot():
    raw = tags(**{"Sony:FlexibleSpotPosition": "3928 1700", "Sony:Face1Position": "770 2700 2288 2288"})
    del raw["Sony:FocusLocation"]
    assert rejected(raw)["coordinates"]["center"]["reason"] == "missing"


def test_center_fallback_remains_explicitly_ambiguous_even_with_valid_frame_size():
    result = rejected(tags(**{"Sony:FocusLocation": "6000 4000 3000 2000", "Sony:FocusFrameSize": "600 800 257"}))
    center = result["coordinates"]["center"]
    assert center["value"] == [.5, .5]
    assert center["fallback_possible"] is True
    assert center["reason"] == "camera_center_fallback_ambiguous"


@pytest.mark.parametrize("x, y", [(3000, 2000), (3001, 2001), (3000, 2001), (3001, 2000)])
def test_odd_dimension_center_fallback_accounts_for_integer_rounding(x, y):
    raw = tags(**{
        "Sony:FocusLocation": [6001, 4001, x, y], "Sony:FullImageSize": "6001 4001",
        "Sony:SonyImageWidth": 6001, "Sony:SonyImageHeight": 4001,
        "ExifIFD:ExifImageWidth": 6001, "ExifIFD:ExifImageHeight": 4001,
        "File:ImageWidth": 6001, "File:ImageHeight": 4001,
    })
    assert rejected(raw)["coordinates"]["center"]["fallback_possible"] is True


@pytest.mark.parametrize("name, value", [
    ("FocusLocation", "6000 4000 3928 1700"), ("FocusFrameSize", "600 800 257"),
    ("FullImageSize", "6000 4000"), ("SonyImageWidth", 6000),
])
def test_identical_coordinate_and_dimension_instances_are_still_ambiguous(name, value):
    result = rejected(tags(**{"Sony:" + name: value, "Sony:Copy2:" + name: value}))
    assert result["coordinates"]["usable_for_subject_selection"] is False


@pytest.mark.parametrize("value", [
    None, "600 800", "600 800 257 1", "600 800 0", "0 800 257", "600 0 257",
    "-1 800 257", "7000 800 257", "600 5000 257", "600 800 -1", "600 800 65536",
    "600 800 nan", "600.5 800 257", "600 800 1.0000000000000001", [600, 800, True], "n/a",
])
def test_frame_requires_three_valid_unsigned_integer_fields(value):
    assert rejected(tags(**{"Sony:FocusFrameSize": value}))["coordinates"]["area_size"]["value"] is None


@pytest.mark.parametrize("validity", [1, 257])
def test_nonzero_documented_frame_validity_retained(validity):
    result = sony.diagnostics(tags(**{"Sony:FocusFrameSize": [600, 800, validity]}))
    assert result["coordinates"]["area_size"]["validity"] == validity
    assert result["coordinates"]["raw_bbox_xywh_hypothesis"] is not None


def test_valid_size_but_box_crossing_boundary_is_not_cropped():
    result = rejected(tags(**{"Sony:FocusLocation": "6000 4000 100 200", "Sony:FocusFrameSize": "600 800 257"}))
    assert result["coordinates"]["reason"] == "area_extends_outside_frame"
    assert result["coordinates"]["center"]["value"] == pytest.approx([100 / 6000, 200 / 4000])


@pytest.mark.parametrize("value", ["4000 6000", "3000 2000", "0 0", "6000", "6000 4000 1", "nan 4000", [True, 4000], "6000.0000000000000001 4000"])
def test_full_image_size_must_agree_without_another_height_width_swap(value):
    rejected(tags(**{"Sony:FullImageSize": value}))


def test_full_image_size_required_even_when_other_dimensions_agree():
    raw = tags()
    del raw["Sony:FullImageSize"]
    assert rejected(raw)["dimensions"]["reason"] == "full_image_size_missing"


@pytest.mark.parametrize("field, value", [
    ("ExifIFD:ExifImageWidth", 3000), ("Sony:SonyImageHeight", 3999),
    ("File:ImageWidth", 4000), ("ExifIFD:ExifImageWidth", True),
    ("Sony:SonyImageWidth", "6000.0000000000000001"), ("File:ImageHeight", 0),
    ("ExifIFD:Copy1:ExifImageWidth", 6000),
])
def test_optional_dimension_pairs_if_present_must_be_valid_and_consistent(field, value):
    rejected(tags(**{field: value}))


def test_incomplete_rendered_dimension_pair_fails_closed():
    raw = tags()
    del raw["ExifIFD:ExifImageHeight"]
    rejected(raw)


def test_storage_and_maximum_dimensions_do_not_replace_reference_dimensions():
    result = sony.diagnostics(tags(**{
        "SubIFD:ImageWidth": 6048, "SubIFD:ImageHeight": 4024,
        "Sony:SonyImageWidthMax": 6024, "Sony:SonyImageHeightMax": 4024,
    }))
    assert result["coordinates"]["reference_frame_size"] == [6000, 4000]
    assert result["coordinates"]["status"] == "not_validated"


def raw_tags():
    raw = tags(**{
        "SubIFD:ImageWidth": 6048, "SubIFD:ImageHeight": 4024,
        "SubIFD:DefaultCropOrigin": "12 12", "SubIFD:DefaultCropSize": "6000 4000",
        "SubIFD:SonyCropTopLeft": "12 12", "SubIFD:SonyCropSize": "6000 4000",
    })
    del raw["File:ImageWidth"], raw["File:ImageHeight"]
    return raw


def test_transcribed_raw_sensor_border_crop_does_not_shift_playback_location():
    result = sony.diagnostics(raw_tags())
    assert result["dimensions"]["status"] == "not_validated"
    assert result["coordinates"]["center"]["value"] == pytest.approx([3928 / 6000, 1700 / 4000])
    assert result["coordinates"]["reference_frame_size"] == [6000, 4000]


def test_zero_crop_origin_still_checks_known_storage_bounds():
    raw = raw_tags()
    raw.update({"SubIFD:DefaultCropOrigin": "0 0", "SubIFD:SonyCropTopLeft": "0 0", "SubIFD:ImageWidth": 5999})
    assert rejected(raw)["dimensions"]["reason"] == "crop_extends_outside_storage_frame"


@pytest.mark.parametrize("field, value", [
    ("SubIFD:DefaultCropSize", "5900 3900"), ("SubIFD:SonyCropTopLeft", "13 12"),
    ("SubIFD:DefaultCropOrigin", "-1 12"), ("SubIFD:SonyCropSize", "6000 4000 1"),
    ("SubIFD:DefaultCropOrigin", "100 100"), ("SubIFD:ImageWidth", 6011),
    ("SubIFD:Copy1:DefaultCropOrigin", "12 12"), ("SubIFD:Copy1:ImageWidth", 6048),
    ("IFD0:DefaultUserCrop", "0 0 .9 1"), ("ExifIFD:DigitalZoomRatio", 2),
    ("ExifIFD:DigitalZoomRatio", 0), ("ExifIFD:DigitalZoomRatio", True),
    ("ExifIFD:DigitalZoomRatio", "1.0000000000000001"), ("Sony:AutoPortraitFramed", 1),
    ("ExifIFD:Copy1:DigitalZoomRatio", 1),
])
def test_unexplained_crop_zoom_or_ambiguous_storage_fails_closed(field, value):
    raw = raw_tags()
    raw[field] = value
    assert rejected(raw)["dimensions"]["status"] == "unknown"


@pytest.mark.parametrize("missing", ["SubIFD:ImageWidth", "SubIFD:DefaultCropSize", "SubIFD:SonyCropTopLeft"])
def test_incomplete_sensor_crop_evidence_is_not_guessed(missing):
    raw = raw_tags()
    del raw[missing]
    rejected(raw)


def test_full_default_user_crop_is_accepted_only_as_unvalidated_metadata_agreement():
    result = sony.diagnostics(tags(**{"IFD0:DefaultUserCrop": "0 0 1 1"}))
    assert result["coordinates"]["status"] == "not_validated"
    assert result["dimensions"]["coordinate_frame_validated"] is False


@pytest.mark.parametrize("orientation", [0, 9, True, "Rotate 270 CW", "8.0000000000000001"])
def test_bad_orientation_never_produces_oriented_geometry(orientation):
    result = sony.diagnostics(tags(**{"IFD0:Orientation": orientation, "IFD1:Copy1:Orientation": orientation}))
    assert result["orientation"]["status"] == "unknown"
    assert result["coordinates"]["oriented_center_xy_hypothesis"] is None
    assert result["coordinates"]["oriented_bbox_xywh_hypothesis"] is None


def test_thumbnail_cannot_supply_main_orientation_and_conflicts_are_retained():
    raw = tags()
    del raw["IFD0:Orientation"]
    assert sony.diagnostics(raw)["orientation"]["status"] == "unknown"
    raw["IFD0:Orientation"] = 1
    result = sony.diagnostics(raw)
    assert result["orientation"]["reason"] == "conflicting_orientation_frames"
    assert result["coordinates"]["oriented_center_xy_hypothesis"] is None


def test_agreeing_main_orientation_duplicates_only_resolve_exif_math():
    result = sony.diagnostics(tags(**{"IFD0:Copy1:Orientation": 8}))
    assert result["orientation"]["status"] == "not_validated"
    assert result["orientation"]["coordinate_frame_validated"] is False
    assert result["orientation"]["instance_resolution"] == "agreeing_valid_main_frame_instances"


def test_panasonic_raw_orientation_is_not_a_sony_main_frame_source():
    raw = tags(**{"PanasonicRaw:Orientation": 8})
    result = sony.diagnostics(raw)
    assert result["orientation"]["status"] == "unknown"
    assert result["coordinates"]["oriented_center_xy_hypothesis"] is None


def test_empty_input_is_safe_and_side_effect_free():
    result = rejected({})
    assert result["camera"]["display_profile"] is None
    assert result["mode"]["single_area_candidate"] is False
    assert result["dimensions"]["status"] == "unknown"
