"""Read-only, optional ExifTool AF evidence. Never selects or scores a subject.

Panasonic metadata and explicitly profiled Sony/Canon models receive diagnostic
interpretation. Even a well-formed
S5M2 result remains ``not_validated``: there is no real-camera calibration of
the MakerNote, RAW crop, decoded image, and displayed-image coordinate frames.
ExifTool's ``-n`` output is retained verbatim as decoded JSON values, not claimed
to be the original TIFF bytes. No face size/sharpness fallback is permitted.

Reference definitions (not evidence of validation on our photographs):
https://exiftool.org/TagNames/Panasonic.html
https://github.com/exiftool/exiftool/blob/2200871d9cef988051d2a99d67df3bda6cbb30a8/lib/Image/ExifTool/Panasonic.pm
https://exiftool.org/exiftool_pod.html
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any


SCHEMA_VERSION = "af-metadata-v1"
ADAPTER_VERSION = "9"
# Do not accept additional tags/options from callers. AFAreaMode is Panasonic's
# actual tag; AFMode is also requested and preserved without conflating the two.
READ_TAGS = (
    "AFPointPosition", "AFAreaSize", "FocusMode", "AFMode", "AFAreaMode",
    "SweepPanoramaDirection", "SweepPanoramaFieldOfView",
    "FacesDetected", "NumFacePositions", "Face1Position", "Face2Position",
    "Face3Position", "Face4Position", "Face5Position",
    "AFSubjectDetection", "Make", "Model", "Orientation", "ImageWidth",
    "ImageHeight", "ExifImageWidth", "ExifImageHeight",
    "PanasonicImageWidth", "PanasonicImageHeight",
    "FocusLocation", "FocusFrameSize", "FullImageSize", "AFAreaModeSetting",
    "SonyImageWidth", "SonyImageHeight", "DigitalZoomRatio",
    "AutoPortraitFramed", "DefaultCropOrigin", "DefaultCropSize",
    "SonyCropTopLeft", "SonyCropSize", "DefaultUserCrop",
    "NumAFPoints", "ValidAFPoints", "CanonImageWidth", "CanonImageHeight",
    "AFImageWidth", "AFImageHeight", "AFAreaWidths", "AFAreaHeights",
    "AFAreaXPositions", "AFAreaYPositions", "AFPointsInFocus", "AFPointsSelected",
    "PrimaryAFPoint", "SubjectToDetect", "EyeDetection", "FocusContinuous",
    "FocusBracketing", "AFConfigTool", "OrientationLinkedAF", "InitialAFPointInServo",
    "DigitalZoom", "ZoomSourceWidth", "ZoomTargetWidth", "AspectRatio",
    "CropLeftMargin", "CropRightMargin", "CropTopMargin", "CropBottomMargin",
    "CroppedImageWidth", "CroppedImageHeight", "CroppedImageLeft", "CroppedImageTop",
    "CropTop", "CropLeft", "CropBottom", "CropRight",
    "CropAngle", "HasCrop", "CropConstrainToWarp",
    "AFInfo2Version", "AFDetectionMethod", "AFCoordinatesAvailable",
    "AFAreaXPosition", "AFAreaYPosition", "AFAreaWidth", "AFAreaHeight", "FocusResult",
    "CropArea", "CropHiSpeed", "AutoDistortionControl", "RetouchHistory",
    "FocusPointSchema", "AFPointsUsed", "RetouchNEFProcessing", "ImageBoundary", "LensProfileEnable",
    "Software", "CreatorTool", "HistorySoftwareAgent", "HistoryAction", "HistoryChanged",
    "DistortionCorrectionSetting", "ExifToolVersion", "Error", "Warning",
)
# Family 4 preserves repeated tag instances that -G1 alone can suppress in JSON.
READ_OPTIONS = ("-config", "", "-j", "-n", "-G1:4", "-a",
                "-charset", "filename=UTF8")
_CHUNK_SIZE = 1024 * 1024
_MAX_OUTPUT = 1024 * 1024
_FOCUS_NAMES = {1: "Auto", 2: "Manual", 4: "Auto, Focus button",
                5: "Auto, Continuous", 6: "AF-S", 7: "AF-C", 8: "AF-F"}
_SINGLE_AREA_MODES = {(16, 0), (16, 16), (128, 0)}
_MAIN_GROUPS = {"IFD0", "PanasonicRaw"}


def _stat_identity(value: os.stat_result) -> dict[str, int]:
    # Access time is deliberately excluded: reading a file can update it.
    return {"size": value.st_size, "mtime_ns": value.st_mtime_ns,
            "ctime_ns": value.st_ctime_ns, "device": value.st_dev,
            "inode": value.st_ino}


def _path_matches_open_handle(path_identity: dict[str, int],
                              handle_identity: dict[str, int]) -> bool:
    """Bind a path lookup to the opened file without Windows ctime false alarms."""
    keys = ("size", "mtime_ns", "device", "inode")
    if os.name != "nt":
        keys += ("ctime_ns",)
    return all(path_identity[key] == handle_identity[key] for key in keys)


class _SourceChanged(OSError):
    pass


def _source_identity(path: Path) -> dict[str, Any]:
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError("source is not a regular file")
    expected = _stat_identity(before)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = _stat_identity(os.fstat(stream.fileno()))
        # Windows path stat and handle fstat can report slightly different
        # ctime values for the same new file. Volume/file ID, size and mtime
        # still bind this handle to the path; handle-to-handle and path-to-path
        # comparisons below retain the full ctime check.
        if not _path_matches_open_handle(expected, opened):
            raise _SourceChanged("source changed before hashing")
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
        if _stat_identity(os.fstat(stream.fileno())) != opened:
            raise _SourceChanged("source changed while hashing")
    if _stat_identity(path.stat()) != expected:
        raise _SourceChanged("source changed after hashing")
    return {"path": str(path), "sha256": digest.hexdigest(),
            "stat_identity": expected}


def _safe_environment() -> dict[str, str]:
    # ExifTool is often a Perl script. Do not allow user startup/module injection.
    # Preserve PATH for a vendor shebang and SystemRoot for Windows process start.
    blocked = {"LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME"}
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PERL", "DYLD_", "EXIFTOOL"))
                   and key.upper() not in blocked}
    # Windows environment keys are case-insensitive and Python normalizes them
    # to uppercase. Use the documented spelling in the child environment while
    # preserving its actual value.
    system_root = next((value for key, value in environment.items()
                        if key.upper() == "SYSTEMROOT"), None)
    if system_root is not None:
        environment = {key: value for key, value in environment.items()
                       if key.upper() != "SYSTEMROOT"}
        environment["SystemRoot"] = system_root
    return environment


def _resolve_tool(configured: str | os.PathLike[str] | None) -> Path:
    candidate = os.fspath(configured) if configured is not None else shutil.which("exiftool")
    if not candidate:
        raise FileNotFoundError("ExifTool is not configured or on PATH")
    path = Path(candidate).expanduser().resolve(strict=True)
    if not path.is_file():
        raise OSError("ExifTool path is not a regular file")
    # Windows may execute these through a shell even with shell=False.
    if path.suffix.lower() in {".bat", ".cmd"}:
        raise OSError("shell wrappers are not supported; use the vendor executable")
    return path


def _run(tool: Path, arguments: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(tool), *arguments], shell=False, check=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="strict", timeout=timeout,
        env=_safe_environment(), cwd=str(tool.parent),
    )


def _unique_tag(raw: dict[str, Any], name: str,
                groups: set[str] | None = None, *,
                identical_identity_ok: bool = False) -> tuple[Any, str | None]:
    candidates = [(key, value) for key, value in raw.items()
                  if key.rsplit(":", 1)[-1] == name
                  and (groups is None or any(group in groups for group in key.split(":")[:-1]))]
    if not candidates:
        return None, "missing"
    # Only camera identity strings may use agreement in this generic lookup.
    # AF instances remain ambiguous even when values match; orientation has a
    # separate, value-validated main-frame consensus rule below.
    if len(candidates) != 1:
        values = [value for _, value in candidates]
        if identical_identity_ok and all(isinstance(value, str) and value == values[0] for value in values):
            return values[0], None
        return None, "ambiguous_instances"
    return candidates[0][1], None


def _numbers(value: Any, count: int) -> list[float] | None:
    parts = value.split() if isinstance(value, str) else value
    if not isinstance(parts, (list, tuple)) or len(parts) != count:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float, str)) for item in parts):
        return None
    try:
        numbers = [float(item) for item in parts]
    except (ValueError, OverflowError):
        return None
    return numbers if all(math.isfinite(item) for item in numbers) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number.is_integer() else None


def _orientation(value: Any) -> int:
    orientation = _integer(value)
    if orientation not in range(1, 9):
        raise ValueError("orientation must be an EXIF integer from 1 through 8")
    if isinstance(value, str):
        # float() can round a fractional string onto a valid orientation. Such
        # a value must not establish agreement between metadata instances.
        try:
            exact = Decimal(value)
        except InvalidOperation:
            raise ValueError("orientation must be an EXIF integer from 1 through 8") from None
        if exact != orientation:
            raise ValueError("orientation must be an EXIF integer from 1 through 8")
    return orientation


def _orientation_diagnostic(raw: dict[str, Any]) -> dict[str, Any]:
    fields = {key: value for key, value in raw.items()
              if key.rsplit(":", 1)[-1] == "Orientation"}
    main_fields = {key: value for key, value in fields.items()
                   if any(group in _MAIN_GROUPS for group in key.split(":")[:-1])}
    orientation = {"status": "unknown", "reason": "missing", "exif_value": None,
                   "raw_fields": fields, "main_frame_fields": main_fields,
                   "instance_resolution": None, "coordinate_frame_validated": False,
                   "source_policy": "IFD0_or_PanasonicRaw_only; no_thumbnail_fallback"}
    if not main_fields:
        return orientation
    repeated = len(main_fields) > 1
    orientation["reason"] = "ambiguous_instances" if repeated else "invalid_orientation"
    if not repeated:
        orientation["exif_value"] = next(iter(main_fields.values()))
    try:
        values = [_orientation(value) for value in main_fields.values()]
    except ValueError:
        return orientation
    if any(value != values[0] for value in values):
        return orientation
    if repeated:
        # Retain every source/value above; only the diagnostic value is merged.
        orientation["exif_value"] = values[0]
    orientation["instance_resolution"] = ("agreeing_valid_main_frame_instances"
                                           if repeated else "single_main_frame_instance")
    orientation["reason"] = "conflicting_orientation_frames"
    try:
        if any(_orientation(value) != values[0] for value in fields.values()):
            return orientation
    except ValueError:
        return orientation
    orientation.update(status="not_validated", reason="exif_math_only")
    return orientation


def orient_normalized_point_hypothesis(point: Any, orientation: int) -> list[float]:
    """EXIF edge-coordinate math only; no camera/crop correctness is implied."""
    values = _numbers(point, 2)
    if values is None or any(not 0 <= item <= 1 for item in values):
        raise ValueError("point must be finite normalized xy within [0, 1]")
    orientation = _orientation(orientation)
    x, y = values
    return list({1: (x, y), 2: (1-x, y), 3: (1-x, 1-y), 4: (x, 1-y),
                 5: (y, x), 6: (1-y, x), 7: (1-y, 1-x), 8: (y, 1-x)}[orientation])


def orient_normalized_box_hypothesis(box: Any, orientation: int) -> list[float]:
    """Transform all four corners of normalized xywh; never clamp/crop a box."""
    values = _numbers(box, 4)
    if values is None:
        raise ValueError("box must be finite normalized xywh")
    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x+width > 1 or y+height > 1:
        raise ValueError("box extends outside normalized frame")
    corners = [orient_normalized_point_hypothesis(point, orientation)
               for point in ((x, y), (x+width, y), (x, y+height), (x+width, y+height))]
    left, top = min(p[0] for p in corners), min(p[1] for p in corners)
    return [left, top, max(p[0] for p in corners)-left, max(p[1] for p in corners)-top]


def _pair_diagnostic(value: Any, issue: str | None, *, size: bool = False) -> dict[str, Any]:
    if issue:
        return {"status": "unknown", "reason": issue, "value": None}
    if isinstance(value, str) and value.strip().lower() in {"none", "n/a", "nan", "inf", "-inf"}:
        return {"status": "unknown", "reason": "sentinel_or_nonfinite", "value": None}
    pair = _numbers(value, 2)
    if pair is None:
        return {"status": "unknown", "reason": "invalid_or_multiple_values", "value": None}
    if any(number in {16777216, 65535, 4294967295} or 4194303.9 <= number < 4194304 for number in pair):
        return {"status": "unknown", "reason": "sentinel", "value": None}
    if any(number < 0 or number > 1 or (size and number == 0) for number in pair):
        return {"status": "unknown", "reason": "out_of_range", "value": None}
    return {"status": "not_validated", "reason": "normalized_pair_only", "value": pair}


def _diagnostics(raw: dict[str, Any]) -> dict[str, Any]:
    # Lazy import avoids coupling the optional brand parser to subprocess I/O.
    from . import af_sony, af_canon, af_nikon
    if af_canon.matches_camera(raw):
        return af_canon.diagnostics(raw)
    if af_sony.matches_camera(raw):
        return af_sony.diagnostics(raw)
    if af_nikon.matches_camera(raw):
        return af_nikon.diagnostics(raw)
    make, make_issue = _unique_tag(raw, "Make", _MAIN_GROUPS, identical_identity_ok=True)
    model, model_issue = _unique_tag(raw, "Model", _MAIN_GROUPS, identical_identity_ok=True)
    camera = {"make": make, "model": model, "make_issue": make_issue,
              "model_issue": model_issue, "real_camera_validated": False,
              "identity_policy": "main_IFD_identity_strings_must_agree",
              "is_panasonic": isinstance(make, str) and make.strip().casefold() in {"panasonic", "panasonic corporation"},
              "is_s5m2_candidate": isinstance(model, str) and model.upper() in {"DC-S5M2", "DC-S5M2X", "S5M2", "S5M2X"}}
    orientation = _orientation_diagnostic(raw)
    orientation_value = _integer(orientation["exif_value"])

    modes: dict[str, Any] = {"status": "unknown", "reasons": [], "single_area_candidate": False}
    for name in ("FocusMode", "AFMode", "AFAreaMode", "AFSubjectDetection"):
        value, issue = _unique_tag(raw, name, {"Panasonic"})
        modes[name] = {"raw_value": value, "issue": issue}
    focus = _integer(modes["FocusMode"]["raw_value"])
    modes["focus_mode_name"] = _FOCUS_NAMES.get(focus)
    area = _numbers(modes["AFAreaMode"]["raw_value"], 2)
    if not camera["is_panasonic"]:
        modes["reasons"].append("unsupported_or_unknown_camera")
    if focus == 2:
        modes["reasons"].append("manual_focus")
    elif focus not in _FOCUS_NAMES:
        modes["reasons"].append("unknown_focus_mode")
    tracking_reference = camera["model"] == "DC-S5M2" and area == [240.0, 0.0]
    if area is None or (tuple(area) not in _SINGLE_AREA_MODES and not tracking_reference):
        modes["reasons"].append("unknown_or_non_single_area_mode")
    # AFMode belongs to other makers in current ExifTool; do not silently equate
    # it to Panasonic AFAreaMode if a future parser supplies both.
    if modes["AFMode"]["issue"] != "missing":
        modes["reasons"].append("af_mode_semantics_unknown")
    detection = modes["AFSubjectDetection"]
    if detection["issue"] != "missing" and (_integer(detection["raw_value"]) not in range(14)):
        modes["reasons"].append("unknown_subject_detection_mode")
    if not modes["reasons"]:
        modes.update(status="not_validated", single_area_candidate=not tracking_reference,
                     display_reference_area=tracking_reference)

    center = _pair_diagnostic(*_unique_tag(raw, "AFPointPosition", {"Panasonic"}))
    area_size = _pair_diagnostic(*_unique_tag(raw, "AFAreaSize", {"Panasonic"}), size=True)
    coordinates: dict[str, Any] = {
        "status": "unknown", "reason": "unverified_camera_raw_crop_frame",
        "center": center, "area_size": area_size,
        "raw_bbox_xywh_hypothesis": None, "oriented_center_xy_hypothesis": None,
        "oriented_bbox_xywh_hypothesis": None, "coordinate_frame_validated": False,
    }
    if camera["is_panasonic"] and (modes["single_area_candidate"] or modes.get("display_reference_area")) and center["value"] is not None:
        coordinates.update(status="not_validated", reason="normalized_geometry_only")
        if orientation["status"] == "not_validated":
            coordinates["oriented_center_xy_hypothesis"] = orient_normalized_point_hypothesis(center["value"], orientation_value)
        if area_size["value"] is not None:
            x, y = center["value"]
            width, height = area_size["value"]
            box = [x-width/2, y-height/2, width, height]
            if box[0] >= 0 and box[1] >= 0 and x+width/2 <= 1 and y+height/2 <= 1:
                coordinates["raw_bbox_xywh_hypothesis"] = box
                if orientation["status"] == "not_validated":
                    coordinates["oriented_bbox_xywh_hypothesis"] = orient_normalized_box_hypothesis(box, orientation_value)
            else:
                coordinates.update(status="unknown", reason="area_extends_outside_frame")
                coordinates["oriented_center_xy_hypothesis"] = None
        elif area_size["reason"] != "missing":
            coordinates.update(status="unknown", reason="invalid_or_ambiguous_area")
            coordinates["oriented_center_xy_hypothesis"] = None
    dimensions = {key: value for key, value in raw.items()
                  if key.rsplit(":", 1)[-1] in {"ImageWidth", "ImageHeight", "ExifImageWidth", "ExifImageHeight", "PanasonicImageWidth", "PanasonicImageHeight"}}
    return {"camera": camera, "orientation": orientation, "mode": modes,
            "coordinates": coordinates,
            "dimensions": {"raw_fields": dimensions, "status": "unknown",
                           "reason": "sensor_crop_preview_and_decoder_relationship_unverified"},
            "subject_association": {"status": "unknown", "person_id": None,
                                    "reason": "no_validated_coordinate_frame_or_person_association"}}


def _empty_result(path: str) -> dict[str, Any]:
    return {"schema": SCHEMA_VERSION, "status": "error", "reason": None,
            "source": {"path": path, "sha256": None, "stat_identity": None,
                       "unchanged_during_read": False},
            "parser": {"name": "ExifTool", "path": None, "version": None,
                       "stat_identity": None, "adapter_version": ADAPTER_VERSION,
                       "requested_tags": list(READ_TAGS), "cache_used": False},
            "raw_tags": {}, "raw_format": "ExifTool -n decoded JSON values with group/instance names",
            "diagnostics": _diagnostics({}), "warnings": [],
            "usable_for_subject_selection": False, "usable_for_keep_reject": False}


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key; metadata instances would be lost")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant: " + value)


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON number")
    return result


def _check_utf8_json(value: Any) -> None:
    # JSON \u escapes may contain a lone surrogate even when stdout itself is
    # valid UTF-8. Reject it rather than returning a report the CLI cannot print.
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, dict):
        for key, child in value.items():
            _check_utf8_json(key)
            _check_utf8_json(child)
    elif isinstance(value, list):
        for child in value:
            _check_utf8_json(child)


def read_af_metadata(path: str | os.PathLike[str], *,
                     exiftool_path: str | os.PathLike[str] | None = None,
                     timeout: float = 10.0) -> dict[str, Any]:
    """Read one local regular file without writing it or changing any decisions.

    ``timeout`` bounds each of two subprocesses (version query and tag read).
    Source hashing is streamed before and after the read, with content AND stat
    comparison. There is deliberately no cache, including no cached failures.
    An explicit path or PATH lookup locates a separately installed ExifTool;
    this module never downloads, installs, or runs shell wrappers for the tool.
    Status is one of error, unsupported, missing, or not_validated. Successful
    extraction is never equivalent to validated AF coordinates or an AF hit.
    """
    result = _empty_result(os.fspath(path))
    try:
        valid_timeout = (not isinstance(timeout, bool) and isinstance(timeout, (int, float))
                         and math.isfinite(timeout) and timeout > 0)
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        result["reason"] = "invalid_timeout"
        return result
    try:
        source = Path(path).expanduser().resolve(strict=True)
        result["source"].update(_source_identity(source))
    except _SourceChanged:
        result["reason"] = "source_changed_during_read"
        return result
    except (OSError, ValueError, RuntimeError) as exc:
        result.update(reason="source_unavailable")
        result["warnings"].append(str(exc)[:1000])
        return result
    try:
        tool = _resolve_tool(exiftool_path)
        result["parser"].update(path=str(tool), stat_identity=_stat_identity(tool.stat()))
    except (OSError, ValueError, RuntimeError) as exc:
        result["reason"] = "exiftool_unavailable"
        result["warnings"].append(str(exc)[:1000])
        return result
    stage = "version"
    try:
        version_process = _run(tool, ("-config", "", "-ver"), timeout)
        version = version_process.stdout.strip()
        if version_process.returncode != 0 or not re.fullmatch(r"\d+\.\d+", version):
            result["reason"] = "exiftool_version_error"
            result["warnings"].append(version_process.stderr[:1000])
            return result
        result["parser"]["version"] = version
        stage = "metadata"
        arguments = (*READ_OPTIONS, *("-" + name for name in READ_TAGS), str(source))
        result["parser"]["read_argv"] = [str(tool), *arguments]
        process = _run(tool, arguments, timeout)
        if process.stderr:
            result["warnings"].append(process.stderr[:1000])
        # Hash again even if metadata parsing failed: do not report stale evidence.
        after = _source_identity(source)
        expected = {key: result["source"][key] for key in after}
        if after != expected:
            raise _SourceChanged("source changed across ExifTool invocation")
        result["source"]["unchanged_during_read"] = True
        if _stat_identity(tool.stat()) != result["parser"]["stat_identity"]:
            result["reason"] = "exiftool_changed_during_read"
            return result
        if len(process.stdout.encode("utf-8")) > _MAX_OUTPUT:
            result["reason"] = "exiftool_output_too_large"
            return result
        payload = json.loads(process.stdout, object_pairs_hook=_strict_object,
                             parse_constant=_invalid_constant, parse_float=_finite_float)
        _check_utf8_json(payload)
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise ValueError("expected one ExifTool JSON record")
        raw = payload[0]
        result["raw_tags"] = raw
        tool_warnings = [value for key, value in raw.items()
                         if key.rsplit(":", 1)[-1] == "Warning"]
        result["warnings"].extend(str(value)[:1000] for value in tool_warnings)
        if process.returncode != 0 or any(key.rsplit(":", 1)[-1] == "Error" for key in raw):
            result["reason"] = "exiftool_read_error"
            return result
        # ExifTool may return zero for damaged metadata. Even minor warnings are
        # conservatively non-usable; do not disguise them as missing/unsupported.
        if tool_warnings:
            result["reason"] = "exiftool_read_warning"
            return result
        reported_source = raw.get("SourceFile")
        if not isinstance(reported_source, str) or Path(reported_source).resolve() != source:
            result["reason"] = "exiftool_source_mismatch"
            return result
        reported_version, issue = _unique_tag(raw, "ExifToolVersion", {"ExifTool"})
        if issue or str(reported_version).rstrip("0").rstrip(".") != version.rstrip("0").rstrip("."):
            result["reason"] = "exiftool_version_mismatch"
            return result
        result["diagnostics"] = _diagnostics(raw)
        camera = result["diagnostics"]["camera"]
        sony_supported = camera.get("display_profile") == "sony_ilce_6600_focus_location_v1"
        canon_supported = camera.get("display_profile") == "canon_eos_r3_afinfo2_v1"
        nikon_supported = camera.get("display_profile") == "nikon_z5_2_afinfo2_0402_v1"
        if nikon_supported:
            present = any(key.rsplit(":", 1)[-1] in {"AFInfo2Version", "AFCoordinatesAvailable", "AFAreaXPosition", "AFAreaYPosition"}
                          and "Nikon" in key.split(":")[:-1] for key in raw)
            result.update(status="not_validated" if present else "missing",
                          reason="camera_AF_record_not_sharpness_proof" if present else "af_record_missing")
        elif canon_supported:
            present = any(key.rsplit(":", 1)[-1] in {"NumAFPoints", "AFAreaXPositions", "AFPointsInFocus", "AFPointsSelected"}
                          and any(group in {"Canon", "Track4"} for group in key.split(":")[:-1]) for key in raw)
            result.update(status="not_validated" if present else "missing",
                          reason="camera_reported_AF_not_sharpness_proof" if present else "af_record_missing")
        elif sony_supported:
            present = any(key.rsplit(":", 1)[-1] == "FocusLocation"
                          and "Sony" in key.split(":")[:-1] for key in raw)
            result.update(status="not_validated" if present else "missing",
                          reason="camera_playback_location_not_focus_proof" if present else "af_location_missing")
        elif not camera["is_panasonic"]:
            result.update(status="unsupported", reason="unsupported_or_unknown_camera")
        elif not any(key.rsplit(":", 1)[-1] in {"AFPointPosition", "AFAreaSize"}
                     and "Panasonic" in key.split(":")[:-1] for key in raw):
            result.update(status="missing", reason="af_position_and_area_missing")
        else:
            result.update(status="not_validated", reason="real_camera_coordinate_mapping_not_validated")
    except _SourceChanged:
        result["reason"] = "source_changed_during_read"
        result["source"]["unchanged_during_read"] = False
    except subprocess.TimeoutExpired:
        result["reason"] = "exiftool_" + stage + "_timeout"
    except (OSError, subprocess.SubprocessError) as exc:
        result["reason"] = "exiftool_" + stage + "_error"
        result["warnings"].append(str(exc)[:1000])
    except (ValueError, UnicodeError, RecursionError) as exc:
        result["reason"] = "exiftool_invalid_output"
        result["warnings"].append(str(exc)[:1000])
    return result
