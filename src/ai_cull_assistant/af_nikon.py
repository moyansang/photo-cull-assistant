"""Read-only, exact Nikon Z5II AFInfo2 0402 display diagnostics.

Consumes ExifTool ``-n -G1:4 -a`` fields, never maker-note bytes or guessed
layouts. AFImageWidth/Height describe the output coordinate frame, not the
larger NEF sensor raster. X/Y are the area center measured from top left.
FocusResult describes the last AF operation in AF-S/AF-C, not optical quality.

Definitions: official ExifTool Nikon.pm, AFInfo2V0400, CropArea, CropHiSpeed,
RetouchHistory and DistortInfo, revision
2200871d9cef988051d2a99d67df3bda6cbb30a8:
https://github.com/exiftool/exiftool/blob/2200871d9cef988051d2a99d67df3bda6cbb30a8/lib/Image/ExifTool/Nikon.pm
Photo focus modes: https://onlinemanual.nikonimglib.com/z5II/en/focus_37.html
No source photos or third-party parser code are included.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Callable


PROFILE = "nikon_z5_2_afinfo2_0402_v1"
READ_TAGS = (
    "AFInfo2Version", "AFDetectionMethod", "AFCoordinatesAvailable", "FocusMode",
    "AFAreaMode", "AFImageWidth", "AFImageHeight", "AFAreaXPosition", "AFAreaYPosition",
    "AFAreaWidth", "AFAreaHeight", "FocusResult", "AFPointsUsed", "FocusPointSchema",
    "CropArea", "CropHiSpeed", "ImageBoundary", "AutoDistortionControl",
    "RetouchHistory", "RetouchNEFProcessing", "DigitalZoomRatio", "DefaultCropOrigin",
    "DefaultCropSize", "DefaultUserCrop", "CropTop", "CropLeft", "CropBottom",
    "CropRight", "CropAngle", "HasCrop", "CropConstrainToWarp", "LensProfileEnable",
)
_AREA_NAMES = {
    192: "Pinpoint", 193: "Single", 195: "Wide (S)", 196: "Wide (L)", 197: "Auto",
    204: "Dynamic Area (S)", 205: "Dynamic Area (M)", 206: "Dynamic Area (L)",
    207: "3D-tracking", 208: "Wide (C1/C2)",
}
_FOCUS_NAMES = {"AF-A": "AF-A", "AF-S": "AF-S", "AF-C": "AF-C",
                "AF-F": "AF-F", "MF": "Manual", "M": "Manual", "MANUAL": "Manual"}
_AUTO_MODES = {"AF-A", "AF-S", "AF-C"}
_FRAME_TAGS = ("AFImageWidth", "AFImageHeight")
_CENTER_TAGS = ("AFAreaXPosition", "AFAreaYPosition")
_SIZE_TAGS = ("AFAreaWidth", "AFAreaHeight")
_GEOMETRY_TAGS = ("AFInfo2Version", "AFDetectionMethod", "AFCoordinatesAvailable",
                  *_FRAME_TAGS, *_CENTER_TAGS, *_SIZE_TAGS)
_DIMENSION_TAGS = set(READ_TAGS[13:]) | {
    *_FRAME_TAGS, "ImageWidth", "ImageHeight", "ExifImageWidth", "ExifImageHeight"}
_NATIVE_FRAME = [6048, 4032]
_NATIVE_STORAGE = [6064, 4040]
_NATIVE_CROP = [8, 4, 6048, 4032]


def _int(value: Any, minimum: int = 0, maximum: int = 65535) -> int | None:
    """Check exact finite integer values before conversion or any arithmetic."""
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


def _array(value: Any, count: int) -> list[int] | None:
    parts = value.split() if isinstance(value, str) else value
    if not isinstance(parts, (list, tuple)) or len(parts) != count:
        return None
    values = [_int(part) for part in parts]
    return None if any(item is None for item in values) else values


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _field(raw: dict[str, Any], name: str, parse: Callable = _int,
           groups: set[str] | None = None) -> dict[str, Any]:
    groups = {"Nikon"} if groups is None else groups
    fields = {key: value for key, value in raw.items() if key.rsplit(":", 1)[-1] == name
              and any(group in groups for group in key.split(":")[:-1])}
    result = {"raw_fields": fields, "raw_value": None, "value": None,
              "issue": "missing", "instance_resolution": None}
    if len(fields) != 1:
        if fields:
            result["issue"] = "ambiguous_instances"
        return result
    result["raw_value"] = next(iter(fields.values()))
    value = parse(result["raw_value"])
    result.update(value=value, issue="invalid_value" if value is None else None,
                  instance_resolution="single_instance" if value is not None else None)
    return result


def _identity(raw: dict[str, Any]) -> tuple[Any, str | None, Any, str | None]:
    from .af_metadata import _unique_tag
    make, make_issue = _unique_tag(raw, "Make", {"IFD0"}, identical_identity_ok=True)
    model, model_issue = _unique_tag(raw, "Model", {"IFD0"}, identical_identity_ok=True)
    return make, make_issue, model, model_issue


def matches_camera(raw: dict[str, Any]) -> bool:
    make, make_issue, model, model_issue = _identity(raw)
    return (make_issue is None and model_issue is None and make == "NIKON CORPORATION"
            and model == "NIKON Z5_2")


def _mode(raw: dict[str, Any], supported: bool) -> dict[str, Any]:
    focus = _field(raw, "FocusMode", lambda value: value.rstrip(" ") if isinstance(value, str) else None)
    area, detection = _field(raw, "AFAreaMode"), _field(raw, "AFDetectionMethod")
    reasons = []
    if not supported:
        reasons.append("unsupported_or_unknown_camera")
    if focus["issue"]:
        reasons.append("focus_mode_" + focus["issue"])
    elif focus["value"] in {"MF", "M", "MANUAL"}:
        reasons.append("manual_focus")
    elif focus["value"] not in _AUTO_MODES:
        reasons.append("unknown_or_unsupported_focus_mode")
    if area["issue"]:
        reasons.append("af_area_mode_" + area["issue"])
    elif area["value"] not in _AREA_NAMES:
        reasons.append("unknown_or_unsupported_af_area_mode")
    if detection["issue"]:
        reasons.append("af_detection_method_" + detection["issue"])
    elif detection["value"] != 2:
        reasons.append("unsupported_af_detection_method")
    return {"status": "unknown" if reasons else "not_validated", "reasons": reasons,
            "FocusMode": focus, "AFAreaMode": area, "AFDetectionMethod": detection,
            "focus_mode_name": _FOCUS_NAMES.get(focus["value"]),
            "af_area_mode_name": _AREA_NAMES.get(area["value"]),
            "af_detection_method_name": {0: "Phase Detect", 1: "Contrast Detect", 2: "Hybrid"}.get(detection["value"]),
            "automatic_focus_mode_allowed": not reasons, "single_area_candidate": False,
            "candidate_meaning": "one_camera_reported_AF_area; not_a_person_or_eye_claim"}


def _focus_result(raw: dict[str, Any], mode: dict[str, Any], supported_schema: bool) -> dict[str, Any]:
    field = _field(raw, "FocusResult", lambda value: _int(value, 0, 255))
    result = {**field, "status": "unknown", "reason": field["issue"] or "unknown_focus_result",
              "last_af_operation_reported_focus": None, "focus_confirmed": None,
              "usable_for_keep_reject": False,
              "meaning": "last_AF_operation_in_AF_S_or_AF_C_only; not_optical_sharpness"}
    if not supported_schema:
        result["reason"] = "unsupported_camera_or_schema"
    elif mode["FocusMode"]["issue"] or mode["FocusMode"]["value"] not in {"AF-C", "AF-S"}:
        result["reason"] = "focus_result_semantics_not_established_for_mode"
    elif field["issue"] is None and field["value"] in {0, 1}:
        result.update(status="not_validated", reason="camera_reported_last_AF_operation_only",
                      last_af_operation_reported_focus=field["value"] == 1)
    return result


def diagnostics(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse source geometry without authenticating a JPEG or RAW preview."""
    from .af_metadata import (_orientation_diagnostic, orient_normalized_box_hypothesis,
                              orient_normalized_point_hypothesis)
    make, make_issue, model, model_issue = _identity(raw)
    supported = matches_camera(raw)
    camera = {"make": make, "model": model, "make_issue": make_issue, "model_issue": model_issue,
              "vendor": "nikon", "display_profile": PROFILE if supported else None,
              "identity_policy": "exact_main_IFD_NIKON_CORPORATION_and_NIKON_Z5_2_strings_must_agree",
              "real_camera_validated": False, "is_panasonic": False, "is_sony": False,
              "is_canon": False, "is_nikon": make == "NIKON CORPORATION"}
    orientation = _orientation_diagnostic(raw)
    orientation["source_policy"] = "IFD0_only; all_recorded_orientations_must_agree"
    if any("IFD0" not in key.split(":")[:-1] for key in orientation["main_frame_fields"]):
        orientation.update(status="unknown", reason="unexpected_main_orientation_source")
    modes = _mode(raw, supported)
    fields = {name: _field(raw, name, _text if name == "AFInfo2Version" else _int)
              for name in _GEOMETRY_TAGS}
    version = fields["AFInfo2Version"]
    schema_ok = supported and version["issue"] is None and version["value"] == "0402"
    frame = [fields[name]["value"] for name in _FRAME_TAGS]
    if any(value is None or value <= 0 for value in frame):
        frame = None
    focus_result = _focus_result(raw, modes, schema_ok)
    unknown = {"status": "unknown", "reason": "no_safe_AF_geometry", "value": None}
    coordinates = {
        "status": "unknown", "reason": "unsupported_or_unknown_camera",
        "meaning": "camera_reported_AF_area", "display_role": None,
        "reference_frame_size": frame, "fields": fields,
        "raw_fields": {key: value for field in fields.values() for key, value in field["raw_fields"].items()},
        "center": dict(unknown), "area_size": dict(unknown), "focus_result": focus_result,
        "AFPointsUsed": _field(raw, "AFPointsUsed", lambda value: value),
        "AFPointsUsed_meaning": "unmapped_camera_used_point_record; not_generic_in_focus_points",
        "raw_bbox_xywh_hypothesis": None, "oriented_center_xy_hypothesis": None,
        "oriented_bbox_xywh_hypothesis": None, "coordinate_frame_validated": False,
        "focus_confirmed": None, "usable_for_subject_selection": False, "usable_for_keep_reject": False,
        "limitation": "Camera AF area and last AF operation do not establish an eye, person or optical sharpness",
    }
    dimensions = {
        "status": "not_validated" if frame and schema_ok else "unknown",
        "reason": "AF_output_frame_only_rendered_provenance_separate" if frame and schema_ok else "missing_invalid_or_unsupported_af_frame",
        "reference_frame_size": frame, "coordinate_frame_validated": False,
        "camera_original_authenticated": False,
        "raw_fields": {key: value for key, value in raw.items() if key.rsplit(":", 1)[-1] in _DIMENSION_TAGS},
        "limitation": "AF tag dimensions alone do not establish rendered image correspondence",
    }
    result = {"camera": camera, "orientation": orientation, "mode": modes,
              "schema": {**version, "status": "not_validated" if schema_ok else "unknown",
                         "reason": "exact_0402_schema_only" if schema_ok else version["issue"] or "unsupported_AFInfo2Version"},
              "coordinates": coordinates, "dimensions": dimensions, "focus_result": focus_result,
              "subject_association": {"status": "unknown", "person_id": None,
                  "reason": "camera_AF_area_does_not_establish_subject_or_focus_quality"}}

    def reject(reason: str) -> dict[str, Any]:
        coordinates["reason"] = reason
        return result

    if not supported:
        return result
    if not schema_ok:
        return reject("af_info2_version_" + (version["issue"] or "unsupported"))
    if modes["status"] != "not_validated":
        return reject("focus_mode_not_supported")
    available = fields["AFCoordinatesAvailable"]
    if available["issue"] or available["value"] != 1:
        return reject("af_coordinates_" + (available["issue"] or "unavailable_or_unsupported"))
    if frame is None:
        return reject("missing_invalid_or_ambiguous_af_frame")
    if any(fields[name]["issue"] for name in _CENTER_TAGS):
        return reject("af_center_missing_invalid_or_ambiguous")
    x, y = [fields[name]["value"] for name in _CENTER_TAGS]
    if modes["AFAreaMode"]["value"] in {197, 207} and x == y == 0:
        return reject("auto_or_tracking_has_no_determined_target")
    if not 0 <= x < frame[0] or not 0 <= y < frame[1]:
        return reject("af_center_outside_reference_frame")
    size_fields = [fields[name] for name in _SIZE_TAGS]
    size_missing = all(field["issue"] == "missing" for field in size_fields)
    box, normalized_size = None, None
    if not size_missing:
        if any(field["issue"] or field["value"] <= 0 for field in size_fields):
            return reject("af_area_size_partial_invalid_or_ambiguous")
        width, height = [field["value"] for field in size_fields]
        # Integer half-edge tests avoid floating-point rounding into bounds.
        if 2*x-width < 0 or 2*y-height < 0 or 2*x+width > 2*frame[0] or 2*y+height > 2*frame[1]:
            return reject("af_area_extends_outside_reference_frame")
        normalized_size = [width/frame[0], height/frame[1]]
        box = [(2*x-width)/(2*frame[0]), (2*y-height)/(2*frame[1]), *normalized_size]
    center = [x/frame[0], y/frame[1]]
    coordinates.update(status="not_validated", reason="camera_AF_geometry_only",
                       display_role="camera_reported_AF_area" if box else "camera_reported_AF_point",
                       center={"status": "not_validated", "reason": "top_left_origin_center_geometry_only", "value": center},
                       area_size={"status": "unknown" if size_missing else "not_validated",
                                  "reason": "missing" if size_missing else "camera_recorded_AF_area_only", "value": normalized_size},
                       raw_bbox_xywh_hypothesis=box)
    modes["single_area_candidate"] = True
    if orientation["status"] == "not_validated":
        value = orientation["exif_value"]
        coordinates["oriented_center_xy_hypothesis"] = orient_normalized_point_hypothesis(center, value)
        if box is not None:
            coordinates["oriented_bbox_xywh_hypothesis"] = orient_normalized_box_hypothesis(box, value)
    return result


def _history(value: Any) -> list[int] | None:
    parts = value.split() if isinstance(value, str) else value if isinstance(value, (list, tuple)) else [value]
    if not 1 <= len(parts) <= 10:
        return None
    return _array(parts, len(parts))


def _boolean(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and value.casefold() in {"false", "true"}:
        return int(value.casefold() == "true")
    return _int(value, 0, 1)


def _frame_evidence(raw: dict[str, Any], size: Any, *, native_raw: bool) -> dict[str, Any]:
    result = {"status": "unknown", "reason": "unsupported_or_unknown_camera", "fields": {},
              "camera_original_required": True, "camera_original_authenticated": False,
              "coordinate_frame_validated": False, "reference_frame_size": None,
              "limitation": "Matching metadata cannot authenticate original pixels or exclude unrecorded edits"}

    def get(name: str, parse: Callable = _int, groups: set[str] | None = None) -> dict[str, Any]:
        field = _field(raw, name, parse, groups)
        key = name if groups is None else "/".join(sorted(groups)) + ":" + name
        result["fields"][key] = field
        return field

    def reject(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result

    if not matches_camera(raw):
        return result
    version = get("AFInfo2Version", _text)
    if version["issue"] or version["value"] != "0402":
        return reject("af_info2_version_" + (version["issue"] or "unsupported"))
    frame_fields = [get(name) for name in _FRAME_TAGS]
    frame = [field["value"] for field in frame_fields]
    if any(field["issue"] or field["value"] <= 0 for field in frame_fields):
        return reject("missing_invalid_or_ambiguous_af_frame")
    result["reference_frame_size"] = frame
    if frame != _NATIVE_FRAME:
        return reject("af_output_frame_not_supported")
    stored = _array(size, 2)
    if stored != frame:
        return reject("stored_size_invalid_or_reference_frame_mismatch")
    history = get("RetouchHistory", _history)
    if history["issue"]:
        return reject("retouch_history_" + history["issue"])
    if any(history["value"]):
        return reject("retouched_image_mapping_not_established")
    distortion = get("AutoDistortionControl")
    if distortion["issue"] or distortion["value"] != 0:
        return reject("distortion_mapping_not_established")
    processing = get("RetouchNEFProcessing", lambda value: _int(value, -128, 127))
    if processing["issue"] != "missing" and (processing["issue"] or processing["value"] != -1):
        return reject("nef_processing_mapping_not_established")
    crop_speed = get("CropHiSpeed", lambda value: _array(value, 7))
    if crop_speed["issue"]:
        return reject("crop_hi_speed_" + crop_speed["issue"])
    speed = crop_speed["value"]
    if speed[0] != 11 or min(speed[1:5]) <= 0 or speed[1:3] != speed[3:5] or speed[5:] != [0, 0]:
        return reject("image_area_not_established_as_FX_uncropped")
    storage = speed[1:3]
    if native_raw:
        if storage != _NATIVE_STORAGE:
            return reject("raw_storage_frame_not_supported")
        sensor_fields = [get(name, groups={"SubIFD1"}) for name in ("ImageWidth", "ImageHeight")]
        if any(field["issue"] for field in sensor_fields):
            return reject("raw_storage_dimensions_missing_invalid_or_ambiguous")
        if [field["value"] for field in sensor_fields] != storage:
            return reject("raw_storage_dimensions_conflict_with_crop_hi_speed")
    crop = get("CropArea", lambda value: _array(value, 4))
    if native_raw or crop["issue"] != "missing":
        if crop["issue"]:
            return reject("crop_area_" + crop["issue"])
        left, top, width, height = crop["value"]
        if [width, height] != frame:
            return reject("crop_area_reference_frame_mismatch")
        if left + width > storage[0] or top + height > storage[1]:
            return reject("crop_area_outside_storage_frame")
        # Bound this initial native route to the evidenced sensor/output crop.
        # The explicit tag is mandatory; never manufacture this padding when
        # absent, or extrapolate correspondence from merely plausible bounds.
        if native_raw and crop["value"] != _NATIVE_CROP:
            return reject("raw_native_crop_not_supported")
        result["storage_crop_xywh"] = crop["value"]
        result["af_coordinate_offset_applied"] = [0, 0]
    elif any(output > sensor for output, sensor in zip(frame, storage)):
        return reject("af_output_frame_outside_storage_frame")
    for names, group, required in ((('ExifImageWidth', 'ExifImageHeight'), {"ExifIFD"}, not native_raw),
                                    (('ImageWidth', 'ImageHeight'), {"File"}, not native_raw)):
        recorded = [get(name, groups=group) for name in names]
        if not required and all(field["issue"] == "missing" for field in recorded):
            continue
        if any(field["issue"] for field in recorded) or [field["value"] for field in recorded] != frame:
            return reject("recorded_output_dimensions_missing_invalid_or_mismatched")
    boundary = get("ImageBoundary", lambda value: _array(value, 4))
    if boundary["issue"] != "missing" and (boundary["issue"] or boundary["value"] != [0, 0, *frame]):
        return reject("image_boundary_unknown_or_not_full_frame")
    for name, expected, parse, groups in (
        ("DigitalZoomRatio", 1, _int, {"ExifIFD"}),
        ("DefaultUserCrop", [0, 0, 1, 1], lambda value: _array(value, 4), {"IFD0", "SubIFD", "SubIFD1"}),
        ("CropTop", 0, _int, {"XMP-crs"}), ("CropLeft", 0, _int, {"XMP-crs"}),
        ("CropBottom", 1, _int, {"XMP-crs"}), ("CropRight", 1, _int, {"XMP-crs"}),
        ("CropAngle", 0, _int, {"XMP-crs"}), ("HasCrop", 0, _boolean, {"XMP-crs"}),
        ("CropConstrainToWarp", 0, _boolean, {"XMP-crs"}), ("LensProfileEnable", 0, _boolean, {"XMP-crs"}),
    ):
        field = get(name, parse, groups)
        if field["issue"] != "missing" and (field["issue"] or field["value"] != expected):
            return reject("additional_crop_zoom_or_warp_unknown_or_active")
    defaults = [get(name, lambda value: _array(value, 2), {"IFD0", "SubIFD", "SubIFD1"})
                for name in ("DefaultCropOrigin", "DefaultCropSize")]
    if any(field["issue"] != "missing" for field in defaults):
        expected_origin = crop["value"][:2] if native_raw and crop["value"] else [0, 0]
        if any(field["issue"] for field in defaults) or [field["value"] for field in defaults] != [expected_origin, frame]:
            return reject("default_crop_missing_invalid_or_conflicting")
    result.update(status="not_validated", reason="stored_and_recorded_dimensions_agree_only",
                  frame_policy="native_NEF_output_crop_no_AF_offset" if native_raw else "same_JPEG_output_frame_no_recorded_retouch_or_warp")
    return result


def jpeg_dimension_evidence(raw: dict[str, Any], size: list[int]) -> dict[str, Any]:
    """Conservative JPEG binding: explicit zero retouch and distortion required.

    A reprocessed JPEG may preserve valid AF metadata, but matching dimensions
    alone cannot establish that the AF record maps to those rendered pixels.
    """
    return _frame_evidence(raw, size, native_raw=False)


def raw_native_frame_evidence(raw: dict[str, Any], size: list[int]) -> dict[str, Any]:
    """Bind native embedded output to explicit NEF crop and sensor bounds.

    ``size`` is the decoded same-file embedded image, never a sibling JPEG.
    CropArea positions the output within storage; it is NOT an AF offset.
    """
    return _frame_evidence(raw, size, native_raw=True)
