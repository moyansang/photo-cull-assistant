"""Canon R3 AFInfo2 pure diagnostics: metadata-only and synthetic fixtures.

The public DPReview JPEG 2285684866 and CR3 4220304966 have different recorded
AF centers. These small transcriptions preserve that difference; no original
photographs, screenshot pixels, third-party parser or accuracy claims are used.
"""
from copy import deepcopy

import pytest

from ai_cull_assistant import af_canon as canon


ARRAYS = ("AFAreaWidths", "AFAreaHeights", "AFAreaXPositions", "AFAreaYPositions")
STATES = ("AFPointsSelected", "AFPointsInFocus")


def tags(count=3, valid=1, **changes):
    raw = {
        "IFD0:Make": "Canon", "IFD0:Model": "Canon EOS R3", "IFD0:Orientation": 1,
        "Canon:FocusMode": 1, "Canon:AFAreaMode": 22,
        "Canon:NumAFPoints": count, "Canon:ValidAFPoints": valid,
        "Canon:CanonImageWidth": 6000, "Canon:CanonImageHeight": 4000,
        "Canon:AFImageWidth": 6000, "Canon:AFImageHeight": 4000,
        "ExifIFD:ExifImageWidth": 6000, "ExifIFD:ExifImageHeight": 4000,
        "File:ImageWidth": 6000, "File:ImageHeight": 4000,
        "Canon:DigitalZoom": 0, "Canon:AspectRatio": 0,
        "Canon:ZoomSourceWidth": 0, "Canon:ZoomTargetWidth": 0,
        "Canon:CropLeftMargin": 0, "Canon:CropRightMargin": 0,
        "Canon:CropTopMargin": 0, "Canon:CropBottomMargin": 0,
        "Canon:CroppedImageWidth": 6000, "Canon:CroppedImageHeight": 4000,
        "Canon:CroppedImageLeft": 0, "Canon:CroppedImageTop": 0,
    }
    for name, first in zip(ARRAYS, (150, 150, 457, 831)):
        raw["Canon:" + name] = " ".join(map(str, [first] * valid + [0] * (count - valid)))
    for name in STATES:
        raw["Canon:" + name] = " ".join(map(str, [1] + [0] * (((count + 15) // 16) - 1)))
    raw.update(changes)
    return raw


def with_track4(raw):
    result = deepcopy(raw)
    for key, value in raw.items():
        if key.startswith("Canon:"):
            result["Track4:Copy1:" + key.split(":")[-1]] = deepcopy(value)
    return result


def rejected(raw):
    result = canon.diagnostics(raw)
    coordinates = result["coordinates"]
    assert coordinates["status"] == "unknown"
    assert coordinates["center"]["value"] is None
    assert coordinates["area_size"]["value"] is None
    assert coordinates["raw_bbox_xywh_hypothesis"] is None
    assert coordinates["oriented_center_xy_hypothesis"] is None
    assert coordinates["oriented_bbox_xywh_hypothesis"] is None
    assert coordinates["display_point_index"] is None
    assert coordinates["focus_confirmed"] is None
    assert coordinates["coordinate_frame_validated"] is False
    assert coordinates["usable_for_subject_selection"] is False
    assert coordinates["usable_for_keep_reject"] is False
    assert coordinates["display_records"] == []
    assert result["mode"]["display_records_candidate"] is False
    assert result["mode"]["single_area_candidate"] is False
    return result


def test_transcribed_real_jpeg_retains_full_arrays_and_independent_states():
    raw = tags(count=1053)
    raw.update({"XMP-crs:CropTop": 0, "XMP-crs:CropLeft": 0, "XMP-crs:CropBottom": 1,
                "XMP-crs:CropRight": 1, "XMP-crs:CropAngle": 0, "XMP-crs:CropConstrainToWarp": 0})
    before = deepcopy(raw)
    result = canon.diagnostics(raw)
    assert raw == before
    assert canon.matches_camera(raw)
    assert result["camera"]["display_profile"] == canon.PROFILE == "canon_eos_r3_afinfo2_v1"
    assert result["camera"]["vendor"] == "canon"
    assert result["camera"]["real_camera_validated"] is False
    assert result["mode"]["focus_mode_name"] == "AI Servo AF"
    assert result["mode"]["af_area_mode_name"] == "Whole Area AF"
    assert result["mode"]["single_area_candidate"] is True
    assert result["mode"]["display_records_candidate"] is True
    c = result["coordinates"]
    assert c["status"] == "not_validated"
    assert c["meaning"] == "camera_reported_AF_point"
    assert c["display_role"] == "selected_and_camera_reported_in_focus"
    assert c["reference_frame_size"] == [6000, 4000]
    assert c["center"]["value"] == pytest.approx([3457 / 6000, 1169 / 4000])
    assert c["area_size"]["value"] == [.025, .0375]
    assert c["raw_bbox_xywh_hypothesis"] == pytest.approx([3382 / 6000, 1094 / 4000, .025, .0375])
    assert c["valid_point_indices"] == [0]
    assert c["active_point_indices"] == [0]
    assert len(c["fields"]["AFAreaWidths"]["value"]) == 1053
    assert c["fields"]["AFAreaXPositions"]["raw_fields"] == {"Canon:AFAreaXPositions": raw["Canon:AFAreaXPositions"]}
    assert c["point_states"]["AFPointsInFocus"]["indices"] == [0]
    assert len(c["point_states"]["AFPointsSelected"]["value"]) == 66
    assert c["focus_confirmed"] is None
    assert c["usable_for_subject_selection"] is c["usable_for_keep_reject"] is False
    assert result["subject_association"]["person_id"] is None
    assert result["dimensions"]["camera_original_authenticated"] is False
    record, = c["display_records"]
    assert record["index"] == c["display_point_index"] == 0
    assert record["selected"] is record["camera_reported_in_focus"] is True
    assert record["display_role"] == c["display_role"]
    assert record["center_xy"] == c["center"]["value"]
    assert record["area_size"] == c["area_size"]["value"]
    for name in ("raw_bbox_xywh_hypothesis", "oriented_center_xy_hypothesis", "oriented_bbox_xywh_hypothesis"):
        assert record[name] == c[name]


def test_cr3_duplicate_consensus_preserves_distinct_raw_geometry_and_ignores_storage():
    raw = with_track4(tags(count=1053))
    for group in ("Canon:", "Track4:Copy1:"):
        raw[group + "AFAreaXPositions"] = " ".join(map(str, [449] + [0] * 1052))
        raw[group + "AFAreaYPositions"] = " ".join(map(str, [817] + [0] * 1052))
    raw.update({"Track3:Copy5:ImageWidth": 6192, "Track3:Copy5:ImageHeight": 4152,
                "Track2:Copy3:ImageWidth": 2004, "Track2:Copy3:ImageHeight": 1336,
                "Track4:Copy1:Orientation": 1})
    c = canon.diagnostics(raw)["coordinates"]
    assert c["status"] == "not_validated"
    assert c["center"]["value"] == pytest.approx([3449 / 6000, 1183 / 4000])
    assert c["fields"]["AFAreaXPositions"]["instance_resolution"] == "agreeing_canon_track4_instances"
    assert len(c["fields"]["AFAreaXPositions"]["raw_fields"]) == 2


@pytest.mark.parametrize("orientation,center,box", [
    (1, [.25, .6], [.2, .5, .1, .2]), (2, [.75, .6], [.7, .5, .1, .2]),
    (3, [.75, .4], [.7, .3, .1, .2]), (4, [.25, .4], [.2, .3, .1, .2]),
    (5, [.6, .25], [.5, .2, .2, .1]), (6, [.4, .25], [.3, .2, .2, .1]),
    (7, [.4, .75], [.3, .7, .2, .1]), (8, [.6, .75], [.5, .7, .2, .1]),
])
def test_all_eight_orientations(orientation, center, box):
    raw = tags(**{"IFD0:Orientation": orientation, "Track4:Orientation": orientation,
                  "Canon:AFAreaWidths": "600 0 0", "Canon:AFAreaHeights": "800 0 0",
                  "Canon:AFAreaXPositions": "-1500 0 0", "Canon:AFAreaYPositions": "-400 0 0"})
    c = canon.diagnostics(raw)["coordinates"]
    assert c["center"]["value"] == [.25, .6]
    assert c["raw_bbox_xywh_hypothesis"] == pytest.approx([.2, .5, .1, .2])
    assert c["oriented_center_xy_hypothesis"] == pytest.approx(center)
    assert c["oriented_bbox_xywh_hypothesis"] == pytest.approx(box)


@pytest.mark.parametrize("make,model", [("CANON", "Canon EOS R3"), (" Canon", "Canon EOS R3"),
    ("Canon", "EOS R3"), ("Canon", "Canon EOS R5"), ("Canon", "Canon EOS R3 "),
    ("Canon", "Canon EOS 5D Mark IV"), ("Canon", "PowerShot G7 X"), (None, "Canon EOS R3")])
def test_identity_exact_allowlist(make, model):
    raw = tags(**{"IFD0:Make": make, "IFD0:Model": model})
    assert not canon.matches_camera(raw)
    assert rejected(raw)["camera"]["display_profile"] is None


def test_identity_main_ifd_only_and_identical_identity_consensus():
    raw = tags(**{"IFD0:Copy1:Make": "Canon", "IFD0:Copy1:Model": "Canon EOS R3"})
    assert canon.matches_camera(raw)
    raw["IFD0:Copy1:Model"] = "Canon EOS R5"
    assert not canon.matches_camera(raw)
    rejected(raw)
    raw = tags()
    del raw["IFD0:Make"], raw["IFD0:Model"]
    raw.update({"Track4:Make": "Canon", "Track4:Model": "Canon EOS R3", "Make": "Canon", "Model": "Canon EOS R3"})
    assert not canon.matches_camera(raw)


@pytest.mark.parametrize("mode,name", [(0, "One-shot AF"), (1, "AI Servo AF"), (2, "AI Focus AF"),
    (256, "One-shot AF (Live View)"), (257, "AI Servo AF (Live View)"), (258, "AI Focus AF (Live View)")])
def test_eos_automatic_modes(mode, name):
    result = canon.diagnostics(tags(**{"Canon:FocusMode": mode, "Track4:FocusMode": str(mode)}))
    assert result["mode"]["focus_mode_name"] == name
    assert result["mode"]["automatic_focus_mode_allowed"] is True
    assert result["coordinates"]["status"] == "not_validated"


@pytest.mark.parametrize("mode", [None, True, False, -1, 3, 4, 5, 6, 16, 512, 519, 65536, "AI Servo", "1.00000000000000001", float("nan"), float("inf"), 10**1000])
def test_manual_unknown_and_other_camera_movie_modes_fail_closed(mode):
    result = rejected(tags(**{"Canon:FocusMode": mode}))
    assert result["mode"]["automatic_focus_mode_allowed"] is False


@pytest.mark.parametrize("field", ["FocusMode", "AFAreaMode"])
def test_missing_required_mode_does_not_infer_from_points(field):
    raw = tags()
    del raw["Canon:" + field]
    rejected(raw)


@pytest.mark.parametrize("area", [0, 3, 15, 18, 23, None, True, "Whole Area AF"])
def test_unknown_or_manual_area_mode_fails_closed(area):
    rejected(tags(**{"Canon:AFAreaMode": area}))


@pytest.mark.parametrize("name,value", [("NumAFPoints", 4), ("ValidAFPoints", 2), ("AFImageWidth", 5999),
    ("AFAreaWidths", "151 0 0"), ("AFAreaXPositions", "458 0 0"), ("AFPointsSelected", "2"),
    ("FocusMode", 0), ("AFAreaMode", 2), ("CanonImageHeight", 4001), ("DigitalZoom", 1)])
def test_conflicting_track4_instances_never_choose_first(name, value):
    rejected(tags(**{"Track4:Copy1:" + name: value}))


@pytest.mark.parametrize("name", ["NumAFPoints", "AFAreaWidths", "AFPointsInFocus", "FocusMode", "CanonImageWidth"])
def test_same_group_repeated_instances_fail_closed_even_if_equal(name):
    raw = tags()
    raw["Canon:Copy1:" + name] = raw["Canon:" + name]
    rejected(raw)


def test_track4_cannot_supply_missing_canon_record_field():
    raw = tags()
    raw["Track4:Copy1:AFAreaWidths"] = raw.pop("Canon:AFAreaWidths")
    rejected(raw)


@pytest.mark.parametrize("name,value", [("NumAFPoints", 0), ("NumAFPoints", -1), ("NumAFPoints", 65536),
    ("NumAFPoints", True), ("NumAFPoints", "3.0000000000000001"), ("ValidAFPoints", -1),
    ("ValidAFPoints", 4), ("ValidAFPoints", None), ("ValidAFPoints", 0)])
def test_invalid_counts(name, value):
    rejected(tags(**{"Canon:" + name: value}))


@pytest.mark.parametrize("name", ARRAYS)
@pytest.mark.parametrize("value", [None, "", "1 2", "1 2 3 4", "1 2 32768", "1 2 -32769", "1 2 nan",
    "1 2 1.0000000000000001", [1, 2, True], [1, 2, []], {"0": 150}])
def test_arrays_exact_length_signed_width_and_strict_numbers(name, value):
    rejected(tags(**{"Canon:" + name: value}))


@pytest.mark.parametrize("width,height,x,y", [(0, 150, 457, 831), (150, 0, 457, 831),
    (-1, 150, 457, 831), (150, -1, 457, 831), (32767, 150, 0, 0),
    (150, 150, 3000, 0), (150, 150, -3001, 0), (150, 150, 0, -2000),
    (150, 150, 0, 2001), (150, 150, 2999, 0), (150, 150, 0, 1999)])
def test_invalid_or_outside_point_geometry_is_not_clamped(width, height, x, y):
    raw = tags()
    for name, value in zip(ARRAYS, (width, height, x, y)):
        raw["Canon:" + name] = [value, 0, 0]
    rejected(raw)


def test_missing_arrays_do_not_use_singular_afinfo_or_primary_point_fallback():
    raw = tags(**{"Canon:AFAreaWidth": 150, "Canon:AFAreaHeight": 150, "Canon:PrimaryAFPoint": 0})
    del raw["Canon:AFAreaWidths"]
    rejected(raw)


def test_zero_size_entries_are_not_areas_and_valid_count_is_not_a_prefix_limit():
    raw = tags()
    for name, value in zip(ARRAYS, (150, 150, 457, 831)):
        raw["Canon:" + name] = [0, 0, value]
    raw["Canon:AFPointsInFocus"] = raw["Canon:AFPointsSelected"] = 4
    c = canon.diagnostics(raw)["coordinates"]
    assert c["status"] == "not_validated"
    assert c["valid_point_indices"] == c["active_point_indices"] == [2]
    assert c["display_point_index"] == 2
    assert c["center"]["value"] == pytest.approx([3457 / 6000, 1169 / 4000])


@pytest.mark.parametrize("valid", [1, 3])
def test_valid_count_must_equal_positive_paired_area_count(valid):
    raw = tags(valid=2)
    raw["Canon:ValidAFPoints"] = valid
    assert rejected(raw)["coordinates"]["reason"] == "valid_point_count_does_not_match_positive_areas"


@pytest.mark.parametrize("name", STATES)
@pytest.mark.parametrize("mask", [None, "", "1 0", "8", "4", "-1", "32768", "65535", "-32769", True, "0.0000000000001"])
def test_masks_are_exact_signed_word_arrays_not_index_lists(name, mask):
    rejected(tags(**{"Canon:" + name: mask}))


def test_signed_mask_high_bit_is_bit15_and_second_word_is_bit16():
    raw = tags(count=17, valid=17)
    raw["Canon:AFPointsInFocus"] = raw["Canon:AFPointsSelected"] = "-32768 0"
    c = canon.diagnostics(raw)["coordinates"]
    assert c["status"] == "not_validated"
    assert c["display_point_index"] == 15
    assert c["point_states"]["AFPointsInFocus"]["indices"] == [15]
    raw["Canon:AFPointsInFocus"] = raw["Canon:AFPointsSelected"] = "0 1"
    assert canon.diagnostics(raw)["coordinates"]["display_point_index"] == 16
    raw["Canon:AFPointsSelected"] = "0 2"
    result = rejected(raw)
    assert result["coordinates"]["point_states"]["AFPointsSelected"]["reason"] == "mask_has_nonzero_padding_bits"


def test_negative_all_bits_word_decodes_all16_without_becoming_negative_index():
    raw = tags(count=16, valid=16)
    raw["Canon:AFPointsInFocus"] = raw["Canon:AFPointsSelected"] = -1
    c = canon.diagnostics(raw)["coordinates"]
    assert c["point_states"]["AFPointsInFocus"]["indices"] == list(range(16))
    # All 16 active points are readable display evidence; none is promoted to
    # the old singleton fields or to an optical-sharpness conclusion.
    assert c["status"] == "not_validated"
    assert [record["index"] for record in c["display_records"]] == list(range(16))
    assert all(record["selected"] is record["camera_reported_in_focus"] is True for record in c["display_records"])
    assert c["display_point_index"] is None
    assert c["focus_confirmed"] is None


@pytest.mark.parametrize("name,key,other", [
    ("AFPointsInFocus", "camera_reported_in_focus", "selected"),
    ("AFPointsSelected", "selected", "camera_reported_in_focus"),
])
def test_mask_referencing_inactive_area_cannot_establish_per_point_false(name, key, other):
    c = rejected(tags(**{"Canon:" + name: 2}))["coordinates"]
    assert c["point_states"][name]["status"] == "unknown"
    assert c["point_states"][name]["reason"] == "mask_references_inactive_point"
    assert c["valid_points"][0][key] is None
    assert c["valid_points"][0][other] is True


def test_mask_zero_is_empty_selection_and_mask_one_is_point_zero():
    c = canon.diagnostics(tags(**{"Canon:AFPointsSelected": 0}))["coordinates"]
    assert c["display_point_index"] == 0
    assert c["point_states"]["AFPointsSelected"]["indices"] == []
    assert c["display_role"] == "camera_reported_in_focus"
    assert c["valid_points"][0]["selected"] is False
    c = canon.diagnostics(tags(**{"Canon:AFPointsInFocus": 0}))["coordinates"]
    assert c["display_role"] == "selected"
    assert c["valid_points"][0]["camera_reported_in_focus"] is False
    assert c["focus_confirmed"] is None


@pytest.mark.parametrize("missing,role,state_key", [("AFPointsSelected", "camera_reported_in_focus", "selected"),
    ("AFPointsInFocus", "selected", "camera_reported_in_focus")])
def test_missing_state_remains_unknown_with_other_unique_state(missing, role, state_key):
    raw = tags()
    del raw["Canon:" + missing]
    c = canon.diagnostics(raw)["coordinates"]
    assert c["status"] == "not_validated"
    assert c["display_role"] == role
    assert c["valid_points"][0][state_key] is None
    assert c["focus_confirmed"] is None


@pytest.mark.parametrize("missing", [True, False])
def test_no_active_state_never_picks_valid_area(missing):
    raw = tags()
    for name in STATES:
        if missing:
            del raw["Canon:" + name]
        else:
            raw["Canon:" + name] = 0
    assert rejected(raw)["coordinates"]["reason"] == "no_reported_active_AF_point"


@pytest.mark.parametrize("selected,in_focus", [(1, 2), (3, 1), (3, 3), (0, 3)])
def test_multiple_points_or_different_roles_are_preserved_without_first_largest_fallback(selected, in_focus):
    raw = tags(valid=2, **{"Canon:AFPointsSelected": selected, "Canon:AFPointsInFocus": in_focus,
                          "Canon:AFAreaWidths": "100 600 0", "Canon:AFAreaXPositions": "0 1000 0"})
    result = canon.diagnostics(raw)
    c = result["coordinates"]
    # A collection, rather than a guessed representative point, is now the
    # authorized display output. Existing singleton fields stay unknown.
    assert c["status"] == "not_validated"
    assert c["reason"] == "active_camera_AF_geometry_only"
    assert c["active_point_indices"] == [0, 1]
    assert len(c["valid_points"]) == 2
    assert [record["index"] for record in c["display_records"]] == [0, 1]
    assert result["mode"]["display_records_candidate"] is True
    assert result["mode"]["single_area_candidate"] is False
    assert c["display_point_index"] is c["display_role"] is None
    assert c["center"]["status"] == c["area_size"]["status"] == "unknown"
    assert c["center"]["value"] is c["area_size"]["value"] is None
    assert c["raw_bbox_xywh_hypothesis"] is c["oriented_center_xy_hypothesis"] is c["oriented_bbox_xywh_hypothesis"] is None
    assert c["focus_confirmed"] is None
    assert c["coordinate_frame_validated"] is c["usable_for_subject_selection"] is c["usable_for_keep_reject"] is False
    assert result["subject_association"]["status"] == "unknown"
    assert result["subject_association"]["person_id"] is None
    for record in c["display_records"]:
        is_selected, is_focused = bool(selected & (1 << record["index"])), bool(in_focus & (1 << record["index"]))
        assert record["selected"] is is_selected
        assert record["camera_reported_in_focus"] is is_focused
        assert record["display_role"] == ("selected_and_camera_reported_in_focus" if is_selected and is_focused
                                           else "camera_reported_in_focus" if is_focused else "selected")


def multi_tags(**changes):
    """Two disjoint active roles plus one positive-size but inactive grid area."""
    raw = tags(count=4, valid=3, **{
        "Canon:AFAreaWidths": "600 300 120 0", "Canon:AFAreaHeights": "800 400 200 0",
        "Canon:AFAreaXPositions": "-1500 1200 0 0", "Canon:AFAreaYPositions": "-400 1000 -1000 0",
        "Canon:AFPointsSelected": 1, "Canon:AFPointsInFocus": 2,
    })
    raw.update(changes)
    return raw


@pytest.mark.parametrize("orientation,centers,boxes", [
    (1, [[.25, .6], [.7, .25]], [[.2, .5, .1, .2], [.675, .2, .05, .1]]),
    (2, [[.75, .6], [.3, .25]], [[.7, .5, .1, .2], [.275, .2, .05, .1]]),
    (3, [[.75, .4], [.3, .75]], [[.7, .3, .1, .2], [.275, .7, .05, .1]]),
    (4, [[.25, .4], [.7, .75]], [[.2, .3, .1, .2], [.675, .7, .05, .1]]),
    (5, [[.6, .25], [.25, .7]], [[.5, .2, .2, .1], [.2, .675, .1, .05]]),
    (6, [[.4, .25], [.75, .7]], [[.3, .2, .2, .1], [.7, .675, .1, .05]]),
    (7, [[.4, .75], [.75, .3]], [[.3, .7, .2, .1], [.7, .275, .1, .05]]),
    (8, [[.6, .75], [.25, .3]], [[.5, .7, .2, .1], [.2, .275, .1, .05]]),
])
def test_multi_record_all_eight_orientations_use_independent_expected_geometry(orientation, centers, boxes):
    raw = multi_tags(**{"IFD0:Orientation": orientation, "Track4:Orientation": orientation})
    before = deepcopy(raw)
    result = canon.diagnostics(raw)
    assert raw == before
    c = result["coordinates"]
    assert c["status"] == "not_validated"
    assert c["valid_point_indices"] == [0, 1, 2]
    assert c["active_point_indices"] == [0, 1]
    records = c["display_records"]
    assert [record["index"] for record in records] == [0, 1]
    assert [(record["selected"], record["camera_reported_in_focus"], record["display_role"]) for record in records] == [
        (True, False, "selected"), (False, True, "camera_reported_in_focus")]
    for record, center, box, raw_center, raw_box in zip(records, centers, boxes,
            [[.25, .6], [.7, .25]], [[.2, .5, .1, .2], [.675, .2, .05, .1]]):
        assert record["center_xy"] == pytest.approx(raw_center)
        assert record["area_size"] == pytest.approx(raw_box[2:])
        assert record["raw_bbox_xywh_hypothesis"] == pytest.approx(raw_box)
        assert record["oriented_center_xy_hypothesis"] == pytest.approx(center)
        assert record["oriented_bbox_xywh_hypothesis"] == pytest.approx(box)
    assert c["valid_points"][2]["selected"] is c["valid_points"][2]["camera_reported_in_focus"] is False


def test_multi_records_noncontiguous_signed_words_preserve_indices_across_word_boundaries():
    raw = tags(count=34, valid=3)
    for name, value in zip(ARRAYS, (200, 300, 600, -400)):
        raw["Canon:" + name] = [value if index in {15, 16, 33} else 0 for index in range(34)]
    raw["Canon:AFPointsSelected"] = "-32768 1 0"
    raw["Canon:AFPointsInFocus"] = "0 1 2"
    result = canon.diagnostics(with_track4(raw))
    c = result["coordinates"]
    assert c["status"] == "not_validated"
    assert c["valid_point_indices"] == c["active_point_indices"] == [15, 16, 33]
    assert [record["index"] for record in c["display_records"]] == [15, 16, 33]
    assert [record["display_role"] for record in c["display_records"]] == [
        "selected", "selected_and_camera_reported_in_focus", "camera_reported_in_focus"]
    assert c["point_states"]["AFPointsSelected"]["indices"] == [15, 16]
    assert c["point_states"]["AFPointsInFocus"]["indices"] == [16, 33]
    assert result["mode"]["single_area_candidate"] is False
    assert len(c["display_records"]) <= c["fields"]["NumAFPoints"]["value"]


def test_display_collection_is_bounded_by_num_af_points_without_arbitrary_truncation():
    raw = tags(count=4097, valid=4097)
    for name in STATES:
        raw["Canon:" + name] = [-1] * 256 + [1]
    c = canon.diagnostics(raw)["coordinates"]
    assert c["status"] == "not_validated"
    assert len(c["display_records"]) == c["fields"]["NumAFPoints"]["value"] == 4097
    assert [record["index"] for record in c["display_records"]] == list(range(4097))


@pytest.mark.parametrize("missing,key,remaining,role", [
    ("AFPointsSelected", "selected", "AFPointsInFocus", "camera_reported_in_focus"),
    ("AFPointsInFocus", "camera_reported_in_focus", "AFPointsSelected", "selected"),
])
def test_multi_records_missing_state_remains_unknown(missing, key, remaining, role):
    raw = multi_tags(**{"Canon:" + remaining: 5})
    del raw["Canon:" + missing]
    c = canon.diagnostics(raw)["coordinates"]
    assert c["status"] == "not_validated"
    assert [record["index"] for record in c["display_records"]] == [0, 2]
    assert all(record[key] is None and record["display_role"] == role for record in c["display_records"])


@pytest.mark.parametrize("changes", [
    {"Canon:NumAFPoints": 3}, {"Canon:ValidAFPoints": 2},
    {"Canon:AFAreaWidths": "600 300 120"}, {"Canon:AFAreaHeights": "800 -1 200 0"},
    {"Canon:AFAreaXPositions": "-1500 3000 0 0"}, {"Canon:AFAreaYPositions": "-400 1900 -1000 0"},
    {"Canon:AFAreaWidths": "600 300 32768 0"}, {"Canon:AFAreaHeights": "800 400 True 0"},
    {"Canon:AFPointsSelected": 8}, {"Canon:AFPointsInFocus": 16},
    {"Canon:AFPointsSelected": "1 0"}, {"Canon:AFPointsInFocus": 32768},
    {"Track4:AFPointsSelected": 2}, {"Canon:Copy1:AFPointsInFocus": 2},
    {"Track4:AFAreaWidths": "600 301 120 0"}, {"Canon:Copy1:AFAreaHeights": "800 400 200 0"},
    {"Canon:FocusMode": 3}, {"Canon:AFAreaMode": 0},
    {"Canon:AFImageWidth": None}, {"Canon:CanonImageHeight": 3999},
    {"ExifIFD:ExifImageWidth": 5999}, {"Canon:DigitalZoom": 1},
    {"Canon:CropLeftMargin": 1}, {"XMP-crs:CropLeft": .1},
])
def test_multi_record_invalid_mutations_reject_entire_collection_without_valid_subset(changes):
    rejected(multi_tags(**changes))


@pytest.mark.parametrize("name", ["Canon:NumAFPoints", "Canon:ValidAFPoints", "Canon:AFAreaWidths",
    "Canon:AFImageWidth", "Canon:CanonImageHeight", "ExifIFD:ExifImageWidth", "Canon:FocusMode"])
def test_multi_record_missing_required_evidence_never_invents_frame_or_counts(name):
    raw = multi_tags()
    del raw[name]
    rejected(raw)


@pytest.mark.parametrize("orientation", [None, 0, 9, True])
def test_multi_records_unusable_orientation_leaves_every_oriented_hypothesis_unknown(orientation):
    raw = multi_tags(**{"IFD0:Orientation": orientation})
    result = canon.diagnostics(raw)
    assert result["orientation"]["status"] == "unknown"
    assert result["coordinates"]["status"] == "not_validated"
    assert len(result["coordinates"]["display_records"]) == 2
    for record in result["coordinates"]["display_records"]:
        assert record["oriented_center_xy_hypothesis"] is None
        assert record["oriented_bbox_xywh_hypothesis"] is None


def test_multi_record_conflicting_orientation_never_uses_one_recorded_value():
    result = canon.diagnostics(multi_tags(**{"Track4:Orientation": 6}))
    assert result["orientation"]["status"] == "unknown"
    assert len(result["coordinates"]["display_records"]) == 2
    assert all(record["oriented_center_xy_hypothesis"] is record["oriented_bbox_xywh_hypothesis"] is None
               for record in result["coordinates"]["display_records"])


@pytest.mark.parametrize("missing", [True, False])
def test_multi_valid_grid_without_active_mask_does_not_become_display_records(missing):
    raw = multi_tags()
    for name in STATES:
        if missing:
            del raw["Canon:" + name]
        else:
            raw["Canon:" + name] = 0
    assert rejected(raw)["coordinates"]["reason"] == "no_reported_active_AF_point"


def test_multi_record_diagnostics_never_opens_or_changes_source_file(tmp_path, monkeypatch):
    source = tmp_path / "original.CR3"
    source.write_bytes(b"synthetic-original-bytes; never parsed or rewritten")
    original = source.read_bytes()
    stat = source.stat()
    raw = multi_tags(SourceFile=str(source))
    before = deepcopy(raw)

    def unexpected_open(*args, **kwargs):
        raise AssertionError("metadata diagnostics must not open source files")

    # Import helpers before checking the purely in-memory diagnostic path.
    canon.diagnostics(raw)
    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", unexpected_open)
        patch.setattr("io.open", unexpected_open)
        assert len(canon.diagnostics(raw)["coordinates"]["display_records"]) == 2
    assert raw == before
    assert source.read_bytes() == original
    assert source.stat().st_mtime_ns == stat.st_mtime_ns


@pytest.mark.parametrize("name,value", [("AFImageWidth", 0), ("AFImageHeight", -1), ("AFImageWidth", True),
    ("CanonImageWidth", 5999), ("CanonImageHeight", 4001), ("DigitalZoom", 1), ("DigitalZoom", "None"),
    ("AspectRatio", 13), ("CropLeftMargin", 1), ("CropTopMargin", -1), ("CroppedImageWidth", 5999),
    ("CroppedImageLeft", 1), ("CroppedImageTop", 1), ("ZoomTargetWidth", 6000)])
def test_dimension_crop_zoom_conflicts(name, value):
    rejected(tags(**{"Canon:" + name: value}))


@pytest.mark.parametrize("name", ["Canon:CanonImageWidth", "ExifIFD:ExifImageWidth", "Canon:CropLeftMargin",
    "Canon:CroppedImageLeft", "Canon:ZoomTargetWidth"])
def test_incomplete_required_dimension_or_present_crop_record_fails_closed(name):
    raw = tags()
    del raw[name]
    rejected(raw)


@pytest.mark.parametrize("value", [5999, True, "6000.00000000000000001", None])
def test_exif_dimension_consensus_is_exact(value):
    rejected(tags(**{"ExifIFD:ExifImageWidth": value}))


def test_other_frame_dimensions_do_not_substitute_main_or_conflicting_exif():
    raw = tags(**{"SubIFD:ImageWidth": 6192, "SubIFD:ImageHeight": 4152,
                  "IFD1:ImageWidth": 160, "IFD1:ImageHeight": 120})
    assert canon.diagnostics(raw)["coordinates"]["status"] == "not_validated"
    raw["ExifIFD:Copy1:ExifImageWidth"] = 6000
    rejected(raw)


@pytest.mark.parametrize("changes", [
    {"IFD0:DefaultCropOrigin": "1 0", "IFD0:DefaultCropSize": "6000 4000"},
    {"IFD0:DefaultCropOrigin": "0 0"}, {"IFD0:DefaultUserCrop": "0 .1 1 1"},
    {"ExifIFD:DigitalZoomRatio": 2}, {"XMP-crs:HasCrop": False},
    {"XMP-crs:CropTop": 0},
])
def test_other_crop_evidence_missing_or_active_fails_closed(changes):
    rejected(tags(**changes))


@pytest.mark.parametrize("changes", [
    {"XMP-crs:CropLeft": .01}, {"XMP-crs:CropRight": .99}, {"XMP-crs:CropAngle": 1},
    {"XMP-crs:HasCrop": True}, {"XMP-crs:CropConstrainToWarp": 1},
    {"XMP-crs:Copy1:CropLeft": 0}, {"XMP-crs:CropBottom": "1.000000000000001"},
])
def test_xmp_full_record_must_be_known_full_frame_and_unambiguous(changes):
    raw = tags(**{"XMP-crs:CropTop": 0, "XMP-crs:CropLeft": 0, "XMP-crs:CropBottom": 1,
                  "XMP-crs:CropRight": 1, "XMP-crs:CropAngle": 0})
    raw.update(changes)
    rejected(raw)


@pytest.mark.parametrize("has_crop", [False, 0, "0", "False", "false"])
def test_complete_xmp_fullframe_crop_and_false_has_crop_are_consistent(has_crop):
    raw = tags(**{"XMP-crs:CropTop": 0, "XMP-crs:CropLeft": 0, "XMP-crs:CropBottom": 1,
                  "XMP-crs:CropRight": 1, "XMP-crs:CropAngle": 0, "XMP-crs:HasCrop": has_crop})
    assert canon.diagnostics(raw)["coordinates"]["status"] == "not_validated"


@pytest.mark.parametrize("source,value", [("Track4:Copy1:Orientation", 6), ("Track4:Orientation", True),
    ("Track4:Orientation", "1.000000000000001"), ("IFD0:Copy1:Orientation", 2),
    ("IFD1:Orientation", 8), ("IFD0:Orientation", 0)])
def test_orientation_conflicts_including_track4_prevent_display(source, value):
    result = canon.diagnostics(tags(**{source: value}))
    assert result["orientation"]["status"] == "unknown"
    assert result["coordinates"]["oriented_center_xy_hypothesis"] is None
    assert result["coordinates"]["oriented_bbox_xywh_hypothesis"] is None


def test_missing_ifd0_orientation_never_uses_track4_or_thumbnail_fallback():
    raw = tags(**{"Track4:Orientation": 1, "IFD1:Orientation": 1})
    del raw["IFD0:Orientation"]
    assert canon.diagnostics(raw)["orientation"]["status"] == "unknown"


def test_optional_settings_remain_settings_and_primary_point_is_not_fallback():
    raw = tags(**{"Canon:SubjectToDetect": 2, "Canon:EyeDetection": 1, "Canon:PrimaryAFPoint": 2,
                  "Canon:FocusContinuous": 0, "Canon:FocusBracketing": 0})
    result = canon.diagnostics(raw)
    assert result["mode"]["SubjectToDetect"]["raw_value"] == 2
    assert result["mode"]["EyeDetection"]["raw_value"] == 1
    assert result["coordinates"]["display_point_index"] == 0
    assert result["subject_association"]["person_id"] is None
    for name in STATES:
        del raw["Canon:" + name]
    rejected(raw)


def test_jpeg_dimension_evidence_reuses_consensus_and_crop_checks_without_authentication():
    raw = with_track4(tags())
    before = deepcopy(raw)
    result = canon.jpeg_dimension_evidence(raw, [6000, 4000])
    assert raw == before
    assert result["status"] == "not_validated"
    assert result["reason"] == "stored_and_recorded_dimensions_agree_only"
    assert result["camera_original_required"] is True
    assert result["camera_original_authenticated"] is False
    assert len(result["fields"]["CanonImageWidth"]["raw_fields"]) == 2
    assert result["fields"]["AFImageWidth"]["agrees_with_stored_size"] is True
    raw["Canon:CropLeftMargin"] = 1
    assert canon.jpeg_dimension_evidence(raw, [6000, 4000])["status"] == "unknown"


@pytest.mark.parametrize("size", [[4000, 6000], [6000, 3999], [True, 4000], [6000], None, [6000, float("nan")]])
def test_jpeg_dimension_evidence_rejects_wrong_stored_dimensions(size):
    assert canon.jpeg_dimension_evidence(tags(), size)["status"] == "unknown"


def test_public_exports_have_no_write_or_third_party_execution_path():
    assert "AFPointsSelected" in canon.READ_TAGS
    assert "HasCrop" in canon.READ_TAGS
    assert len(canon.READ_TAGS) == len(set(canon.READ_TAGS))
