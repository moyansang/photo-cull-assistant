"""Read-only Canon EOS R3 AFInfo2 diagnostics; never a sharpness decision.

Input is ExifTool ``-n -G1:4 -a`` JSON, not camera bytes. AFInfo2 arrays
contain NumAFPoints signed 16-bit entries. ValidAFPoints is a count, not an
index boundary. This narrow profile requires that many positive-size areas;
zero-size entries are inactive. Selected/in-focus fields are signed 16-bit BIT
WORDS, not point indices.
Display records contain only the union of selected and camera-reported in-focus
points, with independent three-state flags. Multiple records never imply one
representative point, subject association, or measured optical sharpness.
EOS coordinates originate at the image center, X right and Y up. This profile
uses AFImageWidth/Height, never a CR3 sensor/storage raster or preview size.

Definitions: official ExifTool Canon.pm, AFInfo/AFInfo2, CameraSettings,
AspectInfo and AFConfig, pinned revision 2200871d9cef988051d2a99d67df3bda6cbb30a8.
https://github.com/exiftool/exiftool/blob/2200871d9cef988051d2a99d67df3bda6cbb30a8/lib/Image/ExifTool/Canon.pm
No third-party parser code or source photographs are included.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Callable


PROFILE = "canon_eos_r3_afinfo2_v1"
READ_TAGS = (
    "FocusMode", "AFAreaMode", "NumAFPoints", "ValidAFPoints",
    "CanonImageWidth", "CanonImageHeight", "AFImageWidth", "AFImageHeight",
    "AFAreaWidths", "AFAreaHeights", "AFAreaXPositions", "AFAreaYPositions",
    "AFPointsInFocus", "AFPointsSelected", "PrimaryAFPoint",
    "SubjectToDetect", "EyeDetection", "FocusContinuous", "FocusBracketing",
    "AFConfigTool", "OrientationLinkedAF", "InitialAFPointInServo",
    "DigitalZoom", "ZoomSourceWidth", "ZoomTargetWidth", "AspectRatio",
    "CropLeftMargin", "CropRightMargin", "CropTopMargin", "CropBottomMargin",
    "CroppedImageWidth", "CroppedImageHeight", "CroppedImageLeft", "CroppedImageTop",
    "CropTop", "CropLeft", "CropBottom", "CropRight", "CropAngle", "HasCrop",
    "CropConstrainToWarp", "DefaultCropOrigin", "DefaultCropSize", "DefaultUserCrop",
    "DigitalZoomRatio",
)
_CANON_GROUPS = {"Canon", "Track4"}
_ARRAY_TAGS = ("AFAreaWidths", "AFAreaHeights", "AFAreaXPositions", "AFAreaYPositions")
_STATE_TAGS = ("AFPointsSelected", "AFPointsInFocus")
_GEOMETRY_TAGS = ("NumAFPoints", "ValidAFPoints", "AFImageWidth", "AFImageHeight", *_ARRAY_TAGS, *_STATE_TAGS)
_FOCUS_NAMES = {
    0: "One-shot AF", 1: "AI Servo AF", 2: "AI Focus AF",
    3: "Manual Focus (3)", 4: "Single", 5: "Continuous", 6: "Manual Focus (6)",
    16: "Pan Focus", 256: "One-shot AF (Live View)", 257: "AI Servo AF (Live View)",
    258: "AI Focus AF (Live View)", 512: "Movie Snap Focus", 519: "Movie Servo AF",
}
# Still-photo EOS AF semantics only. PowerShot Single/Continuous and movie modes
# are not silently generalized to this profile, even though the table lists them.
_AUTO_FOCUS_MODES = {0, 1, 2, 256, 257, 258}
_AREA_NAMES = {
    0: "Off (Manual Focus)", 1: "AF Point Expansion (surround)", 2: "Single-point AF",
    4: "Auto", 5: "Face Detect AF", 6: "Face + Tracking", 7: "Zone AF",
    8: "AF Point Expansion (4 point)", 9: "Spot AF", 10: "AF Point Expansion (8 point)",
    11: "Flexizone Multi (49 point)", 12: "Flexizone Multi (9 point)",
    13: "Flexizone Single", 14: "Large Zone AF", 16: "Large Zone AF (vertical)",
    17: "Large Zone AF (horizontal)", 19: "Flexible Zone AF 1", 20: "Flexible Zone AF 2",
    21: "Flexible Zone AF 3", 22: "Whole Area AF",
}
_SETTING_TAGS = ("PrimaryAFPoint", "SubjectToDetect", "EyeDetection", "FocusContinuous",
                 "FocusBracketing", "AFConfigTool", "OrientationLinkedAF", "InitialAFPointInServo")
_DIMENSION_TAGS = {
    "CanonImageWidth", "CanonImageHeight", "AFImageWidth", "AFImageHeight",
    "ImageWidth", "ImageHeight", "ExifImageWidth", "ExifImageHeight", "DigitalZoom",
    "DigitalZoomRatio", "ZoomSourceWidth", "ZoomTargetWidth", "AspectRatio",
    "CropLeftMargin", "CropRightMargin", "CropTopMargin", "CropBottomMargin",
    "CroppedImageWidth", "CroppedImageHeight", "CroppedImageLeft", "CroppedImageTop",
    "CropTop", "CropLeft", "CropBottom", "CropRight", "CropAngle", "HasCrop",
    "CropConstrainToWarp", "DefaultCropOrigin", "DefaultCropSize", "DefaultUserCrop",
}


def _int(value: Any, minimum: int = 0, maximum: int = 65535) -> int | None:
    """Exact range checks before int conversion; reject bools and rounded strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
        if (not number.is_finite() or number != number.to_integral_value()
                or not minimum <= number <= maximum):
            return None
        return int(number)
    except (ValueError, InvalidOperation, OverflowError):
        return None


def _array(value: Any, count: int, minimum: int = -32768, maximum: int = 32767) -> list[int] | None:
    parts = value.split() if isinstance(value, str) else value
    if not isinstance(parts, (list, tuple)):
        parts = [parts] if count == 1 else None
    if parts is None or len(parts) != count:
        return None
    values = [_int(item, minimum, maximum) for item in parts]
    return None if any(item is None for item in values) else values


def _fields(raw: dict[str, Any], name: str, groups: set[str]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key.rsplit(":", 1)[-1] == name
            and any(group in groups for group in key.split(":")[:-1])}


def _field(raw: dict[str, Any], name: str, parse: Callable = _int,
           groups: set[str] | None = None) -> dict[str, Any]:
    """Only Canon plus one agreeing CR3 Track4 copy may use field consensus.

    Canon is mandatory: a Track4-only value never fills a hole in the Canon
    record. Same-group duplicates remain ambiguous, even if numerically equal.
    Every decoded source is preserved, including malformed/conflicting copies.
    """
    canon = groups is None
    fields = _fields(raw, name, _CANON_GROUPS if canon else groups)
    result = {"raw_fields": fields,
              "raw_value": next(iter(fields.values())) if len(fields) == 1 else None,
              "value": None, "issue": "missing", "instance_resolution": None}
    if not fields:
        return result
    if canon:
        sources = [[group for group in key.split(":")[:-1] if group in _CANON_GROUPS] for key in fields]
        if (any(len(source) != 1 for source in sources)
                or sum(source == ["Canon"] for source in sources) != 1
                or sum(source == ["Track4"] for source in sources) > 1):
            result["issue"] = "ambiguous_or_unsupported_instances"
            return result
    elif len(fields) != 1:
        result["issue"] = "ambiguous_instances"
        return result
    values = [parse(value) for value in fields.values()]
    if any(value is None for value in values):
        result["issue"] = "invalid_value"
    elif any(value != values[0] for value in values):
        result["issue"] = "conflicting_instances"
    else:
        result.update(value=values[0], issue=None,
                      instance_resolution="agreeing_canon_track4_instances" if len(fields) > 1 else "single_instance")
        if len(fields) > 1:
            result["raw_value"] = values[0]
    return result


def _identity(raw: dict[str, Any]) -> tuple[Any, str | None, Any, str | None]:
    from .af_metadata import _unique_tag
    make, make_issue = _unique_tag(raw, "Make", {"IFD0"}, identical_identity_ok=True)
    model, model_issue = _unique_tag(raw, "Model", {"IFD0"}, identical_identity_ok=True)
    return make, make_issue, model, model_issue


def matches_camera(raw: dict[str, Any]) -> bool:
    make, make_issue, model, model_issue = _identity(raw)
    return make_issue is None and model_issue is None and make == "Canon" and model == "Canon EOS R3"


def _mode(raw: dict[str, Any], supported: bool) -> dict[str, Any]:
    focus, area = _field(raw, "FocusMode"), _field(raw, "AFAreaMode")
    reasons = []
    if not supported:
        reasons.append("unsupported_or_unknown_camera")
    if focus["issue"]:
        reasons.append("focus_mode_" + focus["issue"])
    elif focus["value"] in {3, 6}:
        reasons.append("manual_focus")
    elif focus["value"] not in _AUTO_FOCUS_MODES:
        reasons.append("unknown_or_unsupported_focus_mode")
    if area["issue"]:
        reasons.append("af_area_mode_" + area["issue"])
    elif area["value"] == 0:
        reasons.append("manual_af_area_mode")
    elif area["value"] not in _AREA_NAMES:
        reasons.append("unknown_af_area_mode")
    result = {
        "status": "unknown" if reasons else "not_validated", "reasons": reasons,
        "FocusMode": focus, "AFAreaMode": area,
        "focus_mode_name": _FOCUS_NAMES.get(focus["value"]),
        "af_area_mode_name": _AREA_NAMES.get(area["value"]),
        "automatic_focus_mode_allowed": not reasons, "single_area_candidate": False,
        "display_records_candidate": False,
        "candidate_meaning": "one_unambiguous_camera_reported_AF_point; not_an_AF_mode_or_subject_claim",
        "display_records_meaning": "active_camera_reported_AF_points; not_optical_sharpness_or_subject_claims",
        "settings_meaning": "camera_settings_only; not_detected_subject_eye_or_focus_success",
    }
    for name in _SETTING_TAGS:
        result[name] = _field(raw, name, lambda value: _int(value, -2147483648, 2147483647))
    return result


def _dimensions(raw: dict[str, Any], frame: list[int] | None) -> dict[str, Any]:
    result = {"status": "unknown", "reason": "missing_or_invalid_af_frame",
              "reference_frame_size": frame, "coordinate_frame_validated": False,
              "camera_original_authenticated": False,
              "limitation": "Matching metadata cannot exclude edits or crop-and-resize",
              "raw_fields": {key: value for key, value in raw.items()
                             if key.rsplit(":", 1)[-1] in _DIMENSION_TAGS}, "fields": {}}

    def get(name: str, parse: Callable = _int, groups: set[str] | None = None) -> dict[str, Any]:
        value = _field(raw, name, parse, groups)
        result["fields"][name] = value
        return value

    def reject(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result

    if frame is None:
        return result
    # CanonImage and ExifImage describe the recorded output frame. Both must
    # agree. Track3 storage and Track2 previews are retained but never substituted.
    for width, height, group, required in (
        ("CanonImageWidth", "CanonImageHeight", None, True),
        ("ExifImageWidth", "ExifImageHeight", {"ExifIFD"}, True),
        ("ImageWidth", "ImageHeight", {"File"}, False),
        ("ImageWidth", "ImageHeight", {"IFD0"}, False),
    ):
        x, y = get(width, groups=group), get(height, groups=group)
        if not required and x["issue"] == y["issue"] == "missing":
            continue
        if x["issue"] or y["issue"] or min(x["value"], y["value"]) <= 0:
            return reject("recorded_dimensions_missing_invalid_or_ambiguous")
        if [x["value"], y["value"]] != frame:
            return reject("recorded_dimensions_mismatch")
    for name, expected, group in (
        ("DigitalZoom", 0, None), ("AspectRatio", 0, None),
        ("DigitalZoomRatio", 1, {"ExifIFD"}),
    ):
        field = get(name, groups=group)
        if field["issue"] != "missing" and (field["issue"] or field["value"] != expected):
            return reject("crop_or_zoom_unknown_or_active")
    zoom = [get(name) for name in ("ZoomSourceWidth", "ZoomTargetWidth")]
    if any(field["issue"] != "missing" for field in zoom):
        if any(field["issue"] for field in zoom) or zoom[0]["value"] != zoom[1]["value"]:
            return reject("zoom_dimensions_missing_invalid_or_different")
    margins = [get(name) for name in ("CropLeftMargin", "CropRightMargin", "CropTopMargin", "CropBottomMargin")]
    if any(field["issue"] != "missing" for field in margins):
        if any(field["issue"] or field["value"] != 0 for field in margins):
            return reject("crop_margins_missing_invalid_or_active")
    crop = [get(name, lambda value: _int(value, 0, 4294967295)) for name in
            ("CroppedImageWidth", "CroppedImageHeight", "CroppedImageLeft", "CroppedImageTop")]
    if any(field["issue"] != "missing" for field in crop):
        if any(field["issue"] for field in crop) or [field["value"] for field in crop] != [*frame, 0, 0]:
            return reject("cropped_image_missing_invalid_or_not_full_frame")
    defaults = [get(name, lambda value: _array(value, 2, 0, 4294967295), {"IFD0", "SubIFD"})
                for name in ("DefaultCropOrigin", "DefaultCropSize")]
    if any(field["issue"] != "missing" for field in defaults):
        if any(field["issue"] for field in defaults) or [field["value"] for field in defaults] != [[0, 0], frame]:
            return reject("default_crop_missing_invalid_or_not_full_frame")
    user_crop = get("DefaultUserCrop", lambda value: _array(value, 4, 0, 1), {"IFD0", "SubIFD"})
    if user_crop["issue"] != "missing" and (user_crop["issue"] or user_crop["value"] != [0, 0, 1, 1]):
        return reject("user_crop_unknown_or_active")
    # Adobe crop metadata is separate evidence. A partial crop record cannot
    # establish that the existing dimensions still describe the original image.
    xmp_names = ("CropTop", "CropLeft", "CropBottom", "CropRight", "CropAngle")
    xmp = [get(name, lambda value: _int(value, -360, 360), {"XMP-crs"}) for name in xmp_names]
    has_crop = get("HasCrop", _xmp_boolean, {"XMP-crs"})
    constrain = get("CropConstrainToWarp", groups={"XMP-crs"})
    if any(field["issue"] != "missing" for field in [*xmp, has_crop, constrain]):
        if (any(field["issue"] for field in xmp)
                or [field["value"] for field in xmp] != [0, 0, 1, 1, 0]
                or (has_crop["issue"] != "missing" and (has_crop["issue"] or has_crop["value"] != 0))
                or (constrain["issue"] != "missing" and (constrain["issue"] or constrain["value"] != 0))):
            return reject("xmp_crop_missing_invalid_or_active")
    result.update(status="not_validated", reason="recorded_reference_dimensions_agree_only")
    return result


def _xmp_boolean(value: Any) -> int | None:
    # XMP boolean -n values may be bools, 0/1 or False/True strings.
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and value.casefold() in {"false", "true"}:
        return int(value.casefold() == "true")
    return _int(value, 0, 1)


def jpeg_dimension_evidence(raw: dict[str, Any], size: list[int]) -> dict[str, Any]:
    """Bind the same JPEG's stored size to the exact profile's recorded frame.

    This is metadata consistency evidence, never original-image authentication.
    It deliberately reuses the crop and narrow Canon/Track4 consensus checks.
    """
    result = {"status": "unknown", "reason": "required_dimensions_missing_ambiguous_or_mismatched",
              "fields": {}, "camera_original_required": True, "camera_original_authenticated": False,
              "limitation": "Matching dimensions cannot rule out same-size edits or crop-and-resize"}
    stored_size = _array(size, 2, 1, 65535)
    agrees = matches_camera(raw) and stored_size is not None
    for name, group, axis in (
        ("CanonImageWidth", None, 0), ("CanonImageHeight", None, 1),
        ("AFImageWidth", None, 0), ("AFImageHeight", None, 1),
        ("ExifImageWidth", {"ExifIFD"}, 0), ("ExifImageHeight", {"ExifIFD"}, 1),
    ):
        field = _field(raw, name, groups=group)
        valid = field["issue"] is None and stored_size is not None and field["value"] == stored_size[axis]
        result["fields"][name] = {**field, "agrees_with_stored_size": valid}
        agrees = agrees and valid
    dimensions = _dimensions(raw, stored_size)
    result["dimension_diagnostic"] = dimensions
    if agrees and dimensions["status"] == "not_validated":
        result.update(status="not_validated", reason="stored_and_recorded_dimensions_agree_only")
    return result


def _point_state(raw: dict[str, Any], name: str, count: int | None) -> dict[str, Any]:
    field = _field(raw, name, lambda value: _array(value, (count + 15) // 16) if count else None)
    result = {**field, "status": "unknown", "reason": field["issue"] or "invalid_point_counts",
              "indices": None, "word_bits": 16,
              "meaning": "selected_AF_points" if name == "AFPointsSelected" else "camera_reported_in_focus_AF_points"}
    if field["issue"] or count is None:
        return result
    indices = [word_index * 16 + bit for word_index, word in enumerate(field["value"])
               for bit in range(16) if (word & 0xffff) & (1 << bit)]
    result["indices"] = indices
    if any(index >= count for index in indices):
        result["reason"] = "mask_has_nonzero_padding_bits"
    else:
        result.update(status="not_validated", reason="decoded_camera_bit_words_only")
    return result


def diagnostics(raw: dict[str, Any]) -> dict[str, Any]:
    """Return isolated display hypotheses, retaining every input AF instance."""
    from .af_metadata import (_orientation_diagnostic, orient_normalized_box_hypothesis,
                              orient_normalized_point_hypothesis)
    make, make_issue, model, model_issue = _identity(raw)
    supported = matches_camera(raw)
    camera = {"make": make, "model": model, "make_issue": make_issue, "model_issue": model_issue,
              "vendor": "canon", "display_profile": PROFILE if supported else None,
              "identity_policy": "exact_main_IFD_Canon_and_Canon_EOS_R3_strings_must_agree",
              "real_camera_validated": False, "is_panasonic": False, "is_sony": False,
              "is_canon": make == "Canon"}
    orientation = _orientation_diagnostic(raw)
    orientation["source_policy"] = "IFD0_only; all_recorded_orientations_including_Track4_must_agree"
    if any("IFD0" not in key.split(":")[:-1] for key in orientation["main_frame_fields"]):
        orientation.update(status="unknown", reason="unexpected_main_orientation_source")
    modes = _mode(raw, supported)
    fields = {name: _field(raw, name) for name in _GEOMETRY_TAGS[:4]}
    count, valid = fields["NumAFPoints"]["value"], fields["ValidAFPoints"]["value"]
    counts_ok = count is not None and count > 0 and valid is not None and 0 <= valid <= count
    frame = [fields[name]["value"] for name in ("AFImageWidth", "AFImageHeight")]
    if any(value is None or value <= 0 for value in frame):
        frame = None
    for name in _ARRAY_TAGS:
        fields[name] = _field(raw, name, lambda value: _array(value, count) if count else None)
    states = {name: _point_state(raw, name, count if counts_ok else None)
              for name in _STATE_TAGS}
    dimensions = _dimensions(raw, frame)
    unknown_geometry = {"status": "unknown", "reason": "no_unambiguous_active_point", "value": None}
    coordinates = {
        "status": "unknown", "reason": "unsupported_or_unknown_camera",
        "meaning": "camera_reported_AF_point", "display_role": None,
        "reference_frame_size": frame, "raw_fields": {key: value for key, value in raw.items()
            if key.rsplit(":", 1)[-1] in _GEOMETRY_TAGS and any(group in _CANON_GROUPS for group in key.split(":")[:-1])},
        "fields": fields, "point_states": states, "valid_points": [], "valid_point_indices": [], "active_point_indices": [],
        "display_records": [],
        "display_point_index": None, "center": dict(unknown_geometry), "area_size": dict(unknown_geometry),
        "raw_bbox_xywh_hypothesis": None, "oriented_center_xy_hypothesis": None,
        "oriented_bbox_xywh_hypothesis": None, "coordinate_frame_validated": False,
        "focus_confirmed": None, "usable_for_subject_selection": False, "usable_for_keep_reject": False,
        "limitation": "Camera AF state is not optical sharpness, an eye coordinate or a selected person",
    }
    result = {"camera": camera, "orientation": orientation, "mode": modes,
              "coordinates": coordinates, "dimensions": dimensions,
              "subject_association": {"status": "unknown", "person_id": None,
                  "reason": "camera_AF_point_does_not_establish_subject_or_focus_quality"}}

    def reject(reason: str) -> dict[str, Any]:
        coordinates["reason"] = reason
        return result

    if not supported:
        return result
    if not counts_ok:
        return reject("point_counts_missing_invalid_or_conflicting")
    if frame is None:
        return reject("missing_invalid_or_conflicting_af_frame")
    if any(fields[name]["issue"] for name in _ARRAY_TAGS):
        return reject("point_arrays_missing_invalid_or_conflicting")
    # ValidAFPoints is only a count in the official definition. For this narrow
    # R3 profile, require a consistent count of positive paired sizes, wherever
    # they occur. Do not assume valid entries are contiguous or choose an area
    # because it is first/largest. Zero/zero entries remain inactive metadata.
    for index in range(count):
        width, height, x, y = [fields[name]["value"][index] for name in _ARRAY_TAGS]
        if width == height == 0:
            continue
        cx, cy = frame[0] / 2 + x, frame[1] / 2 - y
        if width <= 0 or height <= 0 or not 0 <= cx < frame[0] or not 0 <= cy < frame[1]:
            return reject("valid_point_geometry_outside_frame_or_nonpositive")
        if cx - width / 2 < 0 or cy - height / 2 < 0 or cx + width / 2 > frame[0] or cy + height / 2 > frame[1]:
            return reject("valid_point_area_extends_outside_frame")
        center, size = [cx / frame[0], cy / frame[1]], [width / frame[0], height / frame[1]]
        box = [(cx - width / 2) / frame[0], (cy - height / 2) / frame[1], *size]
        point = {"index": index, "raw_center_xy": [x, y], "raw_area_size": [width, height],
                 "center_xy": center, "area_size": size, "raw_bbox_xywh_hypothesis": box,
                 "selected": None, "camera_reported_in_focus": None, "focus_confirmed": None}
        coordinates["valid_points"].append(point)
    valid_indices = {point["index"] for point in coordinates["valid_points"]}
    coordinates["valid_point_indices"] = sorted(valid_indices)
    if len(valid_indices) != valid:
        return reject("valid_point_count_does_not_match_positive_areas")
    for state in states.values():
        if state["status"] == "not_validated" and any(index not in valid_indices for index in state["indices"]):
            state.update(status="unknown", reason="mask_references_inactive_point")
    # A mask referencing an inactive area cannot establish even a negative
    # state for another point. Populate per-point flags only after validation.
    for point in coordinates["valid_points"]:
        for name, key in (("AFPointsSelected", "selected"), ("AFPointsInFocus", "camera_reported_in_focus")):
            if states[name]["status"] == "not_validated":
                point[key] = point["index"] in states[name]["indices"]
    if any(state["status"] != "not_validated" and state["issue"] != "missing" for state in states.values()):
        return reject("point_state_invalid_or_conflicting")
    active = sorted({index for state in states.values() if state["status"] == "not_validated"
                     for index in state["indices"]})
    coordinates["active_point_indices"] = active
    if not active:
        return reject("no_reported_active_AF_point")
    if modes["status"] != "not_validated":
        return reject("focus_mode_not_supported")
    if dimensions["status"] != "not_validated":
        return reject(dimensions["reason"])
    # Only explicitly selected and/or camera-reported in-focus points become
    # display records. Positive-size inactive grid entries remain diagnostics.
    # All geometry and masks must pass before publishing any of the collection.
    points_by_index = {point["index"]: point for point in coordinates["valid_points"]}
    for index in active:
        point = points_by_index[index]
        role = ("selected_and_camera_reported_in_focus" if point["selected"] and point["camera_reported_in_focus"]
                else "camera_reported_in_focus" if point["camera_reported_in_focus"] else "selected")
        record = {"index": index, "selected": point["selected"],
                  "camera_reported_in_focus": point["camera_reported_in_focus"], "display_role": role,
                  "center_xy": point["center_xy"], "area_size": point["area_size"],
                  "raw_bbox_xywh_hypothesis": point["raw_bbox_xywh_hypothesis"],
                  "oriented_center_xy_hypothesis": None, "oriented_bbox_xywh_hypothesis": None}
        if orientation["status"] == "not_validated":
            value = orientation["exif_value"]
            record["oriented_center_xy_hypothesis"] = orient_normalized_point_hypothesis(point["center_xy"], value)
            record["oriented_bbox_xywh_hypothesis"] = orient_normalized_box_hypothesis(point["raw_bbox_xywh_hypothesis"], value)
        coordinates["display_records"].append(record)
    coordinates.update(status="not_validated", reason="active_camera_AF_geometry_only")
    modes["display_records_candidate"] = True
    if len(active) != 1:
        # Legacy singleton consumers must never silently choose the first point.
        return result
    point = coordinates["display_records"][0]
    coordinates.update(status="not_validated", reason="unambiguous_camera_AF_geometry_only",
                       display_role=point["display_role"], display_point_index=point["index"],
                       center={"status": "not_validated", "reason": "EOS_center_origin_geometry_only", "value": point["center_xy"]},
                       area_size={"status": "not_validated", "reason": "camera_recorded_AF_area_only", "value": point["area_size"]},
                       raw_bbox_xywh_hypothesis=point["raw_bbox_xywh_hypothesis"],
                       oriented_center_xy_hypothesis=point["oriented_center_xy_hypothesis"],
                       oriented_bbox_xywh_hypothesis=point["oriented_bbox_xywh_hypothesis"])
    modes["single_area_candidate"] = True
    return result
