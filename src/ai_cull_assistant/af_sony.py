"""Model-scoped Sony playback-location diagnostics, never focus-quality evidence.

The ILCE-6600 profile interprets ExifTool's decoded ``-n`` values, not TIFF
bytes. FocusLocation is [reference width, height, x, y]; FullImageSize has
already undergone ExifTool's height/width ValueConv. FocusFrameSize has THREE
fields [width, height, validity]. No synthetic box or selected-person fallback
is permitted. Even coherent coordinates remain unvalidated.

Definitions inspected in official ExifTool Sony.pm, pinned revision
2200871d9cef988051d2a99d67df3bda6cbb30a8: Main/0x2027, Main/0x2037,
Main/0xb02b, Main/0x201b and Tag9402/0x16. The last two use the same focus-mode
mapping on this profile; other Sony tables use DIFFERENT numeric mappings.
https://github.com/exiftool/exiftool/blob/2200871d9cef988051d2a99d67df3bda6cbb30a8/lib/Image/ExifTool/Sony.pm
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from typing import Any


PROFILE = "sony_ilce_6600_focus_location_v1"
READ_TAGS = (
    "FocusLocation", "FocusFrameSize", "FullImageSize", "AFAreaModeSetting",
    "SonyImageWidth", "SonyImageHeight", "DigitalZoomRatio", "AutoPortraitFramed",
    "DefaultCropOrigin", "DefaultCropSize", "SonyCropTopLeft", "SonyCropSize",
    "DefaultUserCrop",
)
_FOCUS_NAMES = {0: "Manual", 2: "AF-S", 3: "AF-C", 4: "AF-A", 6: "DMF"}
_AUTO_FOCUS_MODES = {2, 3, 4}
_DIMENSION_TAGS = {
    "ImageWidth", "ImageHeight", "ExifImageWidth", "ExifImageHeight",
    "SonyImageWidth", "SonyImageHeight", "FullImageSize", "DigitalZoomRatio",
    "AutoPortraitFramed", "DefaultCropOrigin", "DefaultCropSize",
    "SonyCropTopLeft", "SonyCropSize", "DefaultUserCrop",
}


def _fields(raw: dict[str, Any], name: str, groups: set[str]) -> dict[str, Any]:
    return {key: value for key, value in raw.items()
            if key.rsplit(":", 1)[-1] == name
            and any(group in groups for group in key.split(":")[:-1])}


def _field(raw: dict[str, Any], name: str, groups: set[str]) -> dict[str, Any]:
    fields = _fields(raw, name, groups)
    return {"raw_value": next(iter(fields.values())) if len(fields) == 1 else None,
            "raw_fields": fields,
            "issue": "missing" if not fields else "ambiguous_instances" if len(fields) != 1 else None}


def _int(value: Any) -> int | None:
    """Exact bounded integers: no bools, truncation or float-rounded strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        if isinstance(value, str):
            number = Decimal(value)
            if not number.is_finite() or number != number.to_integral_value():
                return None
            if not 0 <= number <= 4294967295:
                return None
            return int(number)
        if not math.isfinite(value) or not 0 <= value <= 4294967295:
            return None
        integer = int(value)
        return integer if integer == value else None
    except (ValueError, InvalidOperation, OverflowError):
        return None


def _ints(value: Any, count: int, *, maximum: int = 65535) -> list[int] | None:
    parts = value.split() if isinstance(value, str) else value
    if not isinstance(parts, (tuple, list)) or len(parts) != count:
        return None
    result = [_int(part) for part in parts]
    if any(item is None or item > maximum for item in result):
        return None
    return result


def _identity(raw: dict[str, Any]) -> tuple[Any, str | None, Any, str | None]:
    # Lazy import permits af_metadata to import this profile for dispatch.
    from .af_metadata import _unique_tag
    make, make_issue = _unique_tag(raw, "Make", {"IFD0"}, identical_identity_ok=True)
    model, model_issue = _unique_tag(raw, "Model", {"IFD0"}, identical_identity_ok=True)
    return make, make_issue, model, model_issue


def matches_camera(raw: dict[str, Any]) -> bool:
    """Only the exact supported model with consistent main-IFD identity."""
    make, make_issue, model, model_issue = _identity(raw)
    return (make_issue is None and model_issue is None and isinstance(make, str)
            and make.strip().casefold() == "sony" and model == "ILCE-6600")


def _mode(raw: dict[str, Any], supported: bool) -> dict[str, Any]:
    fields = _fields(raw, "FocusMode", {"Sony"})
    values = [_int(value) for value in fields.values()]
    # The two model-specific tables above agree in semantics. This exception
    # is intentionally NOT shared with AF locations, sizes or crop fields.
    agrees = bool(values) and all(value in _FOCUS_NAMES and value == values[0] for value in values)
    focus = values[0] if agrees else None
    issue = (None if agrees else "missing" if not fields else
             "ambiguous_instances" if len(fields) > 1 else "unknown_focus_mode")
    reasons = []
    if not supported:
        reasons.append("unsupported_or_unknown_camera")
    if issue:
        reasons.append("focus_mode_" + issue if issue != "unknown_focus_mode" else issue)
    elif focus == 0:
        reasons.append("manual_focus")
    elif focus == 6:
        reasons.append("direct_manual_focus")
    result = {
        "status": "not_validated" if not reasons else "unknown", "reasons": reasons,
        "single_area_candidate": supported and focus in _AUTO_FOCUS_MODES and not reasons,
        "candidate_meaning": "one_camera_playback_location; not_an_AF_area_selection_claim",
        "focus_mode_name": _FOCUS_NAMES.get(focus),
        "FocusMode": {"raw_value": focus, "raw_fields": fields, "issue": issue,
                      "instance_resolution": ("agreeing_model_scoped_focus_mode_instances" if len(fields) > 1
                                              else "single_model_scoped_focus_mode_instance") if agrees else None},
    }
    # Preserve selected-area settings without equating them with playback
    # location, focus success or an eye-detection result.
    for name in ("AFAreaMode", "AFAreaModeSetting"):
        field = _field(raw, name, {"Sony"})
        result[name] = field
        if field["issue"] == "missing":
            continue
        value = _int(field["raw_value"])
        if field["issue"] or value is None or value > 255:
            reasons.append("invalid_or_ambiguous_" + name)
        elif name == "AFAreaMode" and value == 255:
            # Tag9402/0x17 explicitly reports Manual. Do not ignore this
            # contradictory evidence merely because FocusMode says AF-C.
            reasons.append("manual_af_area_mode")
    if reasons:
        result.update(status="unknown", single_area_candidate=False)
    return result


def _dimensions(raw: dict[str, Any], frame: list[int] | None) -> dict[str, Any]:
    result = {
        "raw_fields": {key: value for key, value in raw.items()
                       if key.rsplit(":", 1)[-1] in _DIMENSION_TAGS},
        "status": "unknown", "reason": "missing_or_invalid_location_frame",
        "reference_frame_size": frame, "coordinate_frame_validated": False,
        "camera_original_authenticated": False,
        "limitation": "Metadata agreement cannot rule out same-size edits or crop-and-resize",
        "fields": {},
    }

    def get(name: str, groups: set[str]) -> dict[str, Any]:
        value = _field(raw, name, groups)
        result["fields"][name] = value
        return value

    def reject(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result

    if frame is None:
        return result
    full = get("FullImageSize", {"Sony"})
    if full["issue"]:
        return reject("full_image_size_" + full["issue"])
    full_size = _ints(full["raw_value"], 2, maximum=4294967295)
    if full_size is None or min(full_size) <= 0:
        return reject("invalid_full_image_size")
    if full_size != frame:
        return reject("focus_location_full_image_size_mismatch")
    for width, height, group in (
        ("ExifImageWidth", "ExifImageHeight", "ExifIFD"),
        ("SonyImageWidth", "SonyImageHeight", "Sony"),
        ("ImageWidth", "ImageHeight", "File"),
    ):
        x, y = get(width, {group}), get(height, {group})
        if x["issue"] == y["issue"] == "missing":
            continue
        values = [_int(x["raw_value"]), _int(y["raw_value"])]
        if x["issue"] or y["issue"] or any(value is None or value <= 0 for value in values):
            return reject("recorded_dimensions_missing_invalid_or_ambiguous")
        if values != frame:
            return reject("recorded_dimensions_mismatch")
    for name, group, expected in (("DigitalZoomRatio", "ExifIFD", 1), ("AutoPortraitFramed", "Sony", 0)):
        field = get(name, {group})
        if field["issue"] == "missing":
            continue
        if field["issue"] or _int(field["raw_value"]) != expected:
            return reject("crop_or_zoom_unknown_or_active")
    user_crop = get("DefaultUserCrop", {"IFD0", "SubIFD"})
    if user_crop["issue"] != "missing":
        if user_crop["issue"] or _ints(user_crop["raw_value"], 4) != [0, 0, 1, 1]:
            return reject("user_crop_unknown_or_active")
    origins = []
    for origin_name, size_name in (("DefaultCropOrigin", "DefaultCropSize"), ("SonyCropTopLeft", "SonyCropSize")):
        origin_field = get(origin_name, {"IFD0", "SubIFD", "Sony"})
        size_field = get(size_name, {"IFD0", "SubIFD", "Sony"})
        if origin_field["issue"] == size_field["issue"] == "missing":
            continue
        origin = _ints(origin_field["raw_value"], 2, maximum=4294967295)
        size = _ints(size_field["raw_value"], 2, maximum=4294967295)
        if origin_field["issue"] or size_field["issue"] or origin is None or size is None:
            return reject("crop_metadata_missing_invalid_or_ambiguous")
        if size != frame:
            return reject("crop_size_reference_mismatch")
        origins.append(origin)
        # A sensor-border crop can agree with the tag frame without making
        # the storage raster the AF coordinate reference. If storage bounds
        # exist, check them even for an origin of zero.
        storage_fields = [_field(raw, name, {"SubIFD"}) for name in ("ImageWidth", "ImageHeight")]
        if origin != [0, 0] or any(field["issue"] != "missing" for field in storage_fields):
            storage = [_int(field["raw_value"]) for field in storage_fields]
            if any(field["issue"] for field in storage_fields) or any(value is None for value in storage):
                return reject("crop_storage_dimensions_missing_invalid_or_ambiguous")
            if any(offset + length > bound for offset, length, bound in zip(origin, frame, storage)):
                return reject("crop_extends_outside_storage_frame")
    if origins and any(origin != origins[0] for origin in origins):
        return reject("conflicting_crop_origins")
    result.update(status="not_validated", reason="recorded_reference_dimensions_agree_only")
    return result


def diagnostics(raw: dict[str, Any]) -> dict[str, Any]:
    """Return compatible, fail-closed diagnostics without mutating ``raw``."""
    from .af_metadata import (_orientation_diagnostic, orient_normalized_box_hypothesis,
                              orient_normalized_point_hypothesis)
    make, make_issue, model, model_issue = _identity(raw)
    supported = matches_camera(raw)
    camera = {
        "make": make, "model": model, "make_issue": make_issue, "model_issue": model_issue,
        "vendor": "sony", "display_profile": PROFILE if supported else None,
        "real_camera_validated": False, "identity_policy": "main_IFD_identity_strings_must_agree",
        "is_panasonic": False, "is_sony": isinstance(make, str) and make.strip().casefold() == "sony",
    }
    orientation = _orientation_diagnostic(raw)
    orientation["source_policy"] = "IFD0_only; no_thumbnail_fallback"
    if any("IFD0" not in key.split(":")[:-1] for key in orientation["main_frame_fields"]):
        orientation.update(status="unknown", reason="unexpected_main_orientation_source")
    modes = _mode(raw, supported)
    location = _field(raw, "FocusLocation", {"Sony"})
    values = _ints(location["raw_value"], 4) if location["issue"] is None else None
    frame = values[:2] if values and min(values[:2]) > 0 else None
    center = {**location, "status": "unknown", "reason": location["issue"] or "invalid_focus_location",
              "value": None, "fallback_possible": False}
    if frame is not None and 0 <= values[2] < frame[0] and 0 <= values[3] < frame[1]:
        # An odd dimension cannot represent its exact half as an integer;
        # either neighboring center pixel is conservatively ambiguous.
        fallback = all(abs(2 * point - length) <= 1 for point, length in zip(values[2:], frame))
        center.update(status="unknown" if fallback else "not_validated",
                      reason="camera_center_fallback_ambiguous" if fallback else "playback_location_only",
                      value=[values[2] / frame[0], values[3] / frame[1]], fallback_possible=fallback)
    elif frame is not None:
        center["reason"] = "focus_location_outside_frame"
    dimensions = _dimensions(raw, frame)
    area = _field(raw, "FocusFrameSize", {"Sony"})
    area.update(status="unknown", reason=area["issue"] or "invalid_focus_frame_size", value=None, validity=None)
    area_values = _ints(area["raw_value"], 3) if area["issue"] is None else None
    if area_values is not None:
        area["validity"] = area_values[2]
        if area_values[2] == 0:
            area["reason"] = "focus_frame_marked_unavailable"
        elif frame is not None and all(0 < size <= bound for size, bound in zip(area_values[:2], frame)):
            area.update(status="not_validated", reason="camera_recorded_focus_frame_only",
                        value=[area_values[0] / frame[0], area_values[1] / frame[1]])
    coordinates = {
        "status": "unknown", "reason": "unsupported_or_unknown_camera",
        "meaning": "camera_playback_focus_location", "reference_frame_size": frame,
        "raw_fields": {**location["raw_fields"], **area["raw_fields"]},
        "center": center, "area_size": area,
        "raw_bbox_xywh_hypothesis": None, "oriented_center_xy_hypothesis": None,
        "oriented_bbox_xywh_hypothesis": None, "coordinate_frame_validated": False,
        "focus_confirmed": None, "usable_for_subject_selection": False, "usable_for_keep_reject": False,
        "limitation": "Playback location is not proof of focus, an eye position or an AF-selected subject",
    }
    if supported:
        if modes["status"] != "not_validated":
            coordinates["reason"] = "focus_mode_not_supported"
        elif center["status"] != "not_validated":
            coordinates["reason"] = center["reason"]
        elif dimensions["status"] != "not_validated":
            coordinates["reason"] = dimensions["reason"]
        elif area["value"] is None and area["reason"] != "missing":
            coordinates["reason"] = "invalid_or_ambiguous_focus_frame"
        else:
            box = None
            if area["value"] is not None:
                x, y = center["value"]
                width, height = area["value"]
                box = [x - width / 2, y - height / 2, width, height]
            if box is not None and (box[0] < 0 or box[1] < 0 or box[0] + box[2] > 1 or box[1] + box[3] > 1):
                coordinates["reason"] = "area_extends_outside_frame"
            else:
                coordinates.update(status="not_validated", reason="playback_location_geometry_only",
                                   raw_bbox_xywh_hypothesis=box)
                if orientation["status"] == "not_validated":
                    value = orientation["exif_value"]
                    coordinates["oriented_center_xy_hypothesis"] = orient_normalized_point_hypothesis(center["value"], value)
                    if box is not None:
                        coordinates["oriented_bbox_xywh_hypothesis"] = orient_normalized_box_hypothesis(box, value)
    return {
        "camera": camera, "orientation": orientation, "mode": modes,
        "coordinates": coordinates, "dimensions": dimensions,
        "subject_association": {"status": "unknown", "person_id": None,
                                "reason": "playback_location_does_not_establish_subject_or_focus_quality"},
    }
