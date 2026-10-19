"""Exact Nikon Z5II 0402 diagnostics; portable numeric metadata fixtures only.

The AF values below transcribe the public DPReview Nikon Z5II pair referenced
in the research report. The JPEG was retouched in camera. No source photograph,
third-party code, external executable or camera-display proof is included.
"""
from copy import deepcopy

import pytest

from ai_cull_assistant import af_nikon as nikon


def tags(**changes):
    raw = {
        "IFD0:Make": "NIKON CORPORATION", "IFD0:Model": "NIKON Z5_2", "IFD0:Orientation": 1,
        "Nikon:AFInfo2Version": "0402", "Nikon:FocusMode": "AF-C  ",
        "Nikon:AFDetectionMethod": 2, "Nikon:AFAreaMode": 207,
        "Nikon:AFCoordinatesAvailable": 1,
        "Nikon:AFImageWidth": 6048, "Nikon:AFImageHeight": 4032,
        "Nikon:AFAreaXPosition": 3048, "Nikon:AFAreaYPosition": 1671,
        "Nikon:AFAreaWidth": 204, "Nikon:AFAreaHeight": 204, "Nikon:FocusResult": 1,
        "Nikon:AFPointsUsed": " ".join(["00"] * 38),
        "Nikon:RetouchHistory": 0, "Nikon:AutoDistortionControl": 0,
        "Nikon:RetouchNEFProcessing": -1,
        "Nikon:CropHiSpeed": "11 6064 4040 6064 4040 0 0",
        "Nikon:CropArea": "8 4 6048 4032",
        "SubIFD1:ImageWidth": 6064, "SubIFD1:ImageHeight": 4040,
    }
    raw.update(changes)
    return raw


def jpeg_tags(**changes):
    raw = tags()
    for name in ("Nikon:CropArea", "SubIFD1:ImageWidth", "SubIFD1:ImageHeight"):
        del raw[name]
    raw.update({"File:ImageWidth": 6048, "File:ImageHeight": 4032,
                "ExifIFD:ExifImageWidth": 6048, "ExifIFD:ExifImageHeight": 4032,
                "Nikon:ImageBoundary": "0 0 6048 4032"})
    raw.update(changes)
    return raw


def rejected(raw):
    result = nikon.diagnostics(raw)
    c = result["coordinates"]
    assert c["status"] == "unknown"
    assert c["center"]["value"] is c["area_size"]["value"] is None
    assert c["raw_bbox_xywh_hypothesis"] is c["oriented_center_xy_hypothesis"] is None
    assert c["oriented_bbox_xywh_hypothesis"] is None
    assert c["focus_confirmed"] is None
    assert c["coordinate_frame_validated"] is False
    assert c["usable_for_subject_selection"] is c["usable_for_keep_reject"] is False
    assert result["mode"]["single_area_candidate"] is False
    return result


def test_transcribed_nef_geometry_uses_output_reference_not_storage_or_crop_offset():
    raw = tags()
    before = deepcopy(raw)
    result = nikon.diagnostics(raw)
    assert raw == before
    assert nikon.matches_camera(raw)
    assert result["camera"]["display_profile"] == nikon.PROFILE == "nikon_z5_2_afinfo2_0402_v1"
    assert result["camera"]["vendor"] == "nikon"
    assert result["camera"]["real_camera_validated"] is False
    assert result["mode"]["focus_mode_name"] == "AF-C"
    assert result["mode"]["af_area_mode_name"] == "3D-tracking"
    assert result["mode"]["af_detection_method_name"] == "Hybrid"
    assert result["mode"]["single_area_candidate"] is True
    c = result["coordinates"]
    assert c["status"] == "not_validated"
    assert c["reference_frame_size"] == [6048, 4032]
    assert c["center"]["value"] == pytest.approx([3048/6048, 1671/4032])
    assert c["area_size"]["value"] == pytest.approx([204/6048, 204/4032])
    assert c["raw_bbox_xywh_hypothesis"] == pytest.approx([2946/6048, 1569/4032, 204/6048, 204/4032])
    assert c["oriented_center_xy_hypothesis"] == c["center"]["value"]
    assert c["focus_confirmed"] is None
    assert c["usable_for_subject_selection"] is c["usable_for_keep_reject"] is False
    assert c["AFPointsUsed"]["raw_value"] == raw["Nikon:AFPointsUsed"]
    assert result["subject_association"]["person_id"] is None
    assert result["focus_result"]["last_af_operation_reported_focus"] is True


@pytest.mark.parametrize("orientation,center,box", [
    (1, [.25, .6], [.2, .5, .1, .2]), (2, [.75, .6], [.7, .5, .1, .2]),
    (3, [.75, .4], [.7, .3, .1, .2]), (4, [.25, .4], [.2, .3, .1, .2]),
    (5, [.6, .25], [.5, .2, .2, .1]), (6, [.4, .25], [.3, .2, .2, .1]),
    (7, [.4, .75], [.3, .7, .2, .1]), (8, [.6, .75], [.5, .7, .2, .1]),
])
def test_all_eight_orientations(orientation, center, box):
    raw = tags(**{"Nikon:AFImageWidth": 6000, "Nikon:AFImageHeight": 4000,
                  "Nikon:AFAreaXPosition": 1500, "Nikon:AFAreaYPosition": 2400,
                  "Nikon:AFAreaWidth": 600, "Nikon:AFAreaHeight": 800,
                  "IFD0:Orientation": orientation})
    c = nikon.diagnostics(raw)["coordinates"]
    assert c["center"]["value"] == [.25, .6]
    assert c["oriented_center_xy_hypothesis"] == pytest.approx(center)
    assert c["oriented_bbox_xywh_hypothesis"] == pytest.approx(box)


@pytest.mark.parametrize("make,model", [("Nikon", "NIKON Z5_2"), ("NIKON CORPORATION ", "NIKON Z5_2"),
    ("NIKON CORPORATION", "NIKON Z 5_2"), ("NIKON CORPORATION", "NIKON Z5_2 "),
    ("NIKON CORPORATION", "NIKON Z 9"), ("NIKON CORPORATION", "NIKON Z50_2"),
    ("NIKON CORPORATION", "NIKON D850"), (None, "NIKON Z5_2")])
def test_exact_camera_identity(make, model):
    raw = tags(**{"IFD0:Make": make, "IFD0:Model": model})
    assert not nikon.matches_camera(raw)
    assert rejected(raw)["camera"]["display_profile"] is None


def test_identity_consensus_is_only_for_exact_main_ifd_strings():
    raw = tags(**{"IFD0:Copy1:Make": "NIKON CORPORATION", "IFD0:Copy1:Model": "NIKON Z5_2"})
    assert nikon.matches_camera(raw)
    raw["IFD0:Copy1:Model"] = "NIKON Z 9"
    rejected(raw)
    raw = tags()
    raw["Nikon:Make"] = raw.pop("IFD0:Make")
    assert not nikon.matches_camera(raw)


@pytest.mark.parametrize("version", [None, True, 402, 402.0, "402", "0401", "0400", "0300", "0402 ", "0402\x00", "00402"])
def test_version_must_be_exact_string(version):
    assert rejected(tags(**{"Nikon:AFInfo2Version": version}))["schema"]["status"] == "unknown"


@pytest.mark.parametrize("mode", ["AF-S", "AF-S  ", "AF-C", "AF-C  ", "AF-A", "AF-A  "])
def test_documented_photo_auto_modes(mode):
    result = nikon.diagnostics(tags(**{"Nikon:FocusMode": mode}))
    assert result["coordinates"]["status"] == "not_validated"
    assert result["mode"]["FocusMode"]["raw_value"] == mode
    if mode.startswith("AF-A"):
        assert result["focus_result"]["status"] == "unknown"
        assert result["focus_result"]["last_af_operation_reported_focus"] is None


@pytest.mark.parametrize("mode", [None, 1, True, "MF", "M", "MANUAL", "AF-F", "", "af-c", " AF-C", "AF-C\t"])
def test_manual_unknown_or_video_focus_modes_do_not_supply_geometry(mode):
    rejected(tags(**{"Nikon:FocusMode": mode}))


@pytest.mark.parametrize("mode", [192, 193, 195, 196, 197, 204, 205, 206, 207, 208])
def test_exact_schema_area_modes(mode):
    assert nikon.diagnostics(tags(**{"Nikon:AFAreaMode": mode}))["coordinates"]["status"] == "not_validated"


@pytest.mark.parametrize("mode", [None, True, 0, 8, 194, 198, 199, 203, 209, "207.00000000000001"])
def test_other_nikon_schema_area_modes_are_not_borrowed(mode):
    rejected(tags(**{"Nikon:AFAreaMode": mode}))


@pytest.mark.parametrize("field,value", [("AFCoordinatesAvailable", 0), ("AFCoordinatesAvailable", 2),
    ("AFCoordinatesAvailable", True), ("AFCoordinatesAvailable", "1.00000000000000001"),
    ("AFDetectionMethod", 0), ("AFDetectionMethod", 1), ("AFDetectionMethod", 3),
    ("AFDetectionMethod", "2.00000000000000001")])
def test_availability_and_detection_must_be_explicit(field, value):
    rejected(tags(**{"Nikon:" + field: value}))


@pytest.mark.parametrize("field", ["AFInfo2Version", "FocusMode", "AFAreaMode", "AFCoordinatesAvailable",
    "AFDetectionMethod", "AFImageWidth", "AFImageHeight", "AFAreaXPosition", "AFAreaYPosition"])
def test_required_fields_missing_or_wrong_group_fail_closed(field):
    raw = tags()
    value = raw.pop("Nikon:" + field)
    rejected(raw)
    raw["ExifIFD:" + field] = value
    rejected(raw)


@pytest.mark.parametrize("field", ["AFInfo2Version", "FocusMode", "AFAreaMode", "AFCoordinatesAvailable",
    "AFDetectionMethod", "AFImageWidth", "AFImageHeight", "AFAreaXPosition", "AFAreaYPosition", "AFAreaWidth", "AFAreaHeight"])
def test_same_group_duplicates_are_ambiguous_even_when_equal(field):
    raw = tags()
    raw["Nikon:Copy1:" + field] = raw["Nikon:" + field]
    rejected(raw)
    raw["Nikon:Copy1:" + field] = "conflicting"
    rejected(raw)


@pytest.mark.parametrize("field", ["AFImageWidth", "AFImageHeight", "AFAreaXPosition", "AFAreaYPosition", "AFAreaWidth", "AFAreaHeight"])
@pytest.mark.parametrize("value", [None, True, False, -1, 65536, "1.000000000000001", float("nan"), float("inf"), [], {}, "", 10**1000])
def test_exact_finite_unsigned_integers(field, value):
    rejected(tags(**{"Nikon:" + field: value}))


@pytest.mark.parametrize("field", ["AFImageWidth", "AFImageHeight", "AFAreaWidth", "AFAreaHeight"])
def test_present_zero_dimensions_never_create_a_point_only_fallback(field):
    rejected(tags(**{"Nikon:" + field: 0}))


@pytest.mark.parametrize("x,y,width,height", [(6048, 1671, 204, 204), (3048, 4032, 204, 204),
    (100, 1671, 204, 204), (3048, 100, 204, 204), (6000, 1671, 204, 204), (3048, 4000, 204, 204)])
def test_point_and_all_area_edges_must_fit_without_clamping(x, y, width, height):
    rejected(tags(**dict(zip(["Nikon:AFAreaXPosition", "Nikon:AFAreaYPosition", "Nikon:AFAreaWidth", "Nikon:AFAreaHeight"], [x, y, width, height]))))


def test_area_size_absent_pair_allows_point_only_and_partial_pair_does_not():
    raw = tags()
    del raw["Nikon:AFAreaWidth"]
    rejected(raw)
    del raw["Nikon:AFAreaHeight"]
    c = nikon.diagnostics(raw)["coordinates"]
    assert c["status"] == "not_validated"
    assert c["center"]["value"] is not None
    assert c["area_size"]["reason"] == "missing"
    assert c["raw_bbox_xywh_hypothesis"] is c["oriented_bbox_xywh_hypothesis"] is None
    assert c["oriented_center_xy_hypothesis"] is not None


@pytest.mark.parametrize("mode", [197, 207])
def test_auto_and_tracking_zero_coordinates_mean_no_determined_target(mode):
    raw = tags(**{"Nikon:AFAreaMode": mode, "Nikon:AFAreaXPosition": 0,
                  "Nikon:AFAreaYPosition": 0, "Nikon:AFPointsUsed": "ff " * 38})
    del raw["Nikon:AFAreaWidth"], raw["Nikon:AFAreaHeight"]
    assert rejected(raw)["coordinates"]["reason"] == "auto_or_tracking_has_no_determined_target"


@pytest.mark.parametrize("value,expected", [(0, False), (1, True), (2, None), (None, None),
    (True, None), ("1.0000000000000001", None), ("unknown", None)])
def test_focus_result_does_not_gate_geometry_or_establish_optical_sharpness(value, expected):
    result = nikon.diagnostics(tags(**{"Nikon:FocusResult": value}))
    assert result["coordinates"]["status"] == "not_validated"
    assert result["focus_result"]["last_af_operation_reported_focus"] is expected
    assert result["focus_result"]["focus_confirmed"] is None
    assert result["focus_result"]["usable_for_keep_reject"] is False


def test_missing_or_duplicate_focus_result_never_infers_success():
    raw = tags()
    del raw["Nikon:FocusResult"]
    result = nikon.diagnostics(raw)
    assert result["focus_result"]["reason"] == "missing"
    assert result["focus_result"]["last_af_operation_reported_focus"] is None
    raw.update({"Nikon:FocusResult": 1, "Nikon:Copy1:FocusResult": 1})
    result = nikon.diagnostics(raw)
    assert result["focus_result"]["issue"] == "ambiguous_instances"
    assert result["focus_result"]["last_af_operation_reported_focus"] is None


def test_orientation_consensus_and_thumbnail_conflict_preserve_unoriented_metadata_only():
    raw = tags(**{"IFD0:Copy1:Orientation": "1", "IFD1:Orientation": 1})
    assert nikon.diagnostics(raw)["orientation"]["status"] == "not_validated"
    raw["IFD1:Orientation"] = 8
    result = nikon.diagnostics(raw)
    assert result["orientation"]["status"] == "unknown"
    assert result["coordinates"]["status"] == "not_validated"
    assert result["coordinates"]["oriented_center_xy_hypothesis"] is None
    del raw["IFD0:Orientation"], raw["IFD0:Copy1:Orientation"]
    assert nikon.diagnostics(raw)["orientation"]["status"] == "unknown"


def test_native_nef_crop_evidence_uses_embedded_output_size_without_sensor_offset():
    raw = tags()
    before = deepcopy(raw)
    evidence = nikon.raw_native_frame_evidence(raw, [6048, 4032])
    assert raw == before
    assert evidence["status"] == "not_validated"
    assert evidence["camera_original_required"] is True
    assert evidence["camera_original_authenticated"] is False
    assert evidence["coordinate_frame_validated"] is False
    assert evidence["reference_frame_size"] == [6048, 4032]
    assert evidence["storage_crop_xywh"] == [8, 4, 6048, 4032]
    assert evidence["af_coordinate_offset_applied"] == [0, 0]
    assert evidence["frame_policy"] == "native_NEF_output_crop_no_AF_offset"
    assert nikon.jpeg_dimension_evidence(raw, [6048, 4032])["status"] == "unknown"


def test_same_jpeg_dimension_evidence_requires_explicit_unretouched_record():
    raw = jpeg_tags()
    good = nikon.jpeg_dimension_evidence(raw, [6048, 4032])
    assert good["status"] == "not_validated"
    assert good["reason"] == "stored_and_recorded_dimensions_agree_only"
    assert good["camera_original_required"] is True
    assert good["camera_original_authenticated"] is False
    raw.update({"Nikon:RetouchHistory": 19, "Nikon:RetouchNEFProcessing": 1})
    assert nikon.diagnostics(raw)["coordinates"]["status"] == "not_validated"
    assert nikon.jpeg_dimension_evidence(raw, [6048, 4032])["reason"] == "retouched_image_mapping_not_established"
    assert nikon.raw_native_frame_evidence(raw, [6048, 4032])["status"] == "unknown"


@pytest.mark.parametrize("field,value", [("Nikon:CropArea", "8 4 6047 4032"),
    ("Nikon:CropArea", "17 4 6048 4032"), ("Nikon:CropArea", "8 9 6048 4032"),
    ("Nikon:CropArea", "8 4 6048"), ("Nikon:CropArea", "8 4 6048.00000000000001 4032"),
    ("SubIFD1:ImageWidth", 6048), ("SubIFD1:ImageHeight", 4032),
    ("SubIFD1:Copy1:ImageWidth", 6064), ("Nikon:Copy1:CropArea", "8 4 6048 4032"),
    ("Nikon:CropHiSpeed", "2 6064 4040 6064 4040 0 0"),
    ("Nikon:CropHiSpeed", "11 6064 4040 6000 4000 0 0"),
    ("Nikon:CropHiSpeed", "11 6064 4040 6064 4040 1 0"),
    ("Nikon:CropHiSpeed", "11 6064 4040"), ("Nikon:Copy1:CropHiSpeed", "11 6064 4040 6064 4040 0 0")])
def test_native_crop_sensor_and_image_area_provenance_fail_closed(field, value):
    assert nikon.raw_native_frame_evidence(tags(**{field: value}), [6048, 4032])["status"] == "unknown"


@pytest.mark.parametrize("field", ["Nikon:CropArea", "Nikon:CropHiSpeed", "SubIFD1:ImageWidth",
    "SubIFD1:ImageHeight", "Nikon:RetouchHistory", "Nikon:AutoDistortionControl", "Nikon:AFInfo2Version"])
def test_missing_native_crop_or_processing_evidence_is_not_guessed(field):
    raw = tags()
    del raw[field]
    assert nikon.raw_native_frame_evidence(raw, [6048, 4032])["status"] == "unknown"


@pytest.mark.parametrize("helper,fixture", [(nikon.raw_native_frame_evidence, tags), (nikon.jpeg_dimension_evidence, jpeg_tags)])
@pytest.mark.parametrize("size", [[6064, 4040], [4032, 6048], [1620, 1080], [0, 0], [6048],
    [6048, True], ["6048.00000000000001", 4032], None])
def test_helpers_require_actual_native_unoriented_output_size(helper, fixture, size):
    assert helper(fixture(), size)["status"] == "unknown"


@pytest.mark.parametrize("helper,fixture", [(nikon.raw_native_frame_evidence, tags), (nikon.jpeg_dimension_evidence, jpeg_tags)])
@pytest.mark.parametrize("field,value", [("Nikon:RetouchHistory", 1), ("Nikon:RetouchHistory", "0 19"),
    ("Nikon:RetouchHistory", "0.00000000000000001"), ("Nikon:RetouchHistory", []),
    ("Nikon:AutoDistortionControl", 1), ("Nikon:AutoDistortionControl", 2),
    ("Nikon:RetouchNEFProcessing", 1), ("Nikon:Copy1:RetouchHistory", 0),
    ("Nikon:Copy1:AutoDistortionControl", 0), ("Nikon:ImageBoundary", "1 0 6048 4032"),
    ("ExifIFD:DigitalZoomRatio", 2), ("XMP-crs:CropTop", .01), ("XMP-crs:HasCrop", True),
    ("XMP-crs:CropConstrainToWarp", 1), ("XMP-crs:LensProfileEnable", 1),
    ("SubIFD1:DefaultUserCrop", "0 0 1 .9"), ("Nikon:AFInfo2Version", 402)])
def test_helpers_reject_known_retouch_warp_crop_or_ambiguous_state(helper, fixture, field, value):
    assert helper(fixture(**{field: value}), [6048, 4032])["status"] == "unknown"


def test_native_crop_must_be_explicit_evidenced_frame_not_merely_plausible_bounds():
    raw = tags()
    evidence = nikon.raw_native_frame_evidence(raw, [6048, 4032])
    assert evidence["status"] == "not_validated"
    assert evidence["af_coordinate_offset_applied"] == [0, 0]
    raw.update({"SubIFD1:DefaultCropOrigin": "8 4", "SubIFD1:DefaultCropSize": "6048 4032"})
    assert nikon.raw_native_frame_evidence(raw, [6048, 4032])["status"] == "not_validated"
    raw["SubIFD1:DefaultCropOrigin"] = "7 3"
    assert nikon.raw_native_frame_evidence(raw, [6048, 4032])["status"] == "unknown"


@pytest.mark.parametrize("crop", ["0 0 6048 4032", "7 3 6048 4032", "9 5 6048 4032", "16 8 6048 4032"])
def test_other_in_bounds_crop_origins_are_not_validated_native_mappings(crop):
    raw = tags(**{"Nikon:CropArea": crop})
    assert nikon.raw_native_frame_evidence(raw, [6048, 4032])["reason"] == "raw_native_crop_not_supported"


@pytest.mark.parametrize("storage", [[6048, 4032], [6064, 4048], [6072, 4040], [6100, 4100]])
def test_other_consistent_sensor_rasters_are_not_supported_native_frames(storage):
    raw = tags(**{"SubIFD1:ImageWidth": storage[0], "SubIFD1:ImageHeight": storage[1],
                  "Nikon:CropHiSpeed": [11, *storage, *storage, 0, 0]})
    assert nikon.raw_native_frame_evidence(raw, [6048, 4032])["reason"] == "raw_storage_frame_not_supported"


@pytest.mark.parametrize("field", ["File:ImageWidth", "File:ImageHeight", "ExifIFD:ExifImageWidth", "ExifIFD:ExifImageHeight"])
def test_jpeg_output_dimensions_are_required_unique_and_must_agree(field):
    raw = jpeg_tags()
    del raw[field]
    assert nikon.jpeg_dimension_evidence(raw, [6048, 4032])["status"] == "unknown"
    raw[field] = 6064
    assert nikon.jpeg_dimension_evidence(raw, [6048, 4032])["status"] == "unknown"
    raw = jpeg_tags()
    group, name = field.split(":")
    raw[group + ":Copy1:" + name] = raw[field]
    assert nikon.jpeg_dimension_evidence(raw, [6048, 4032])["status"] == "unknown"


def test_retouch_array_zeroes_are_checked_not_coerced_to_truthy():
    raw = tags(**{"Nikon:RetouchHistory": "0 0 0"})
    assert nikon.raw_native_frame_evidence(raw, [6048, 4032])["status"] == "not_validated"
    raw["Nikon:RetouchHistory"] = "0 " * 11
    assert nikon.raw_native_frame_evidence(raw, [6048, 4032])["status"] == "unknown"
