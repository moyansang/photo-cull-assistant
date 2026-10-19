"""Explicit offline, same-source JPEG AF-area diagnostics, never culling input.

Run ``python -m ai_cull_assistant.af_diagnostic original.jpg --report NEW.json
--preview NEW.png``. The image is always labelled UNVALIDATED. Both outputs
must be new files. No app asset, decoder/cache, RAW preview, sibling matching,
subject selection, quality score, or validated overlay contract is used.

An AF area's recorded center is not proof of an eye position or successful
focus. Full-frame JPEG provenance removes the RAW/preview-crop guess, but does
not certify autofocus success. Only explicit model profiles are eligible:
DC-S5M2 AF area, ILCE-6600 playback location and EOS R3 AFInfo2 record.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
from typing import Any
import warnings

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import af_metadata


SCHEMA_VERSION = "af-jpeg-diagnostic-v1"
WARNING = "UNVALIDATED AF DIAGNOSTIC"
_JPEG_SUFFIXES = {".jpg", ".jpeg", ".jpe"}
_UNLINK_REQUIRES_CLOSED_FILES = os.name == "nt"


def _new_target(path: str | os.PathLike[str]) -> Path:
    # Resolve the directory, NOT the leaf: a dangling leaf symlink is an
    # existing file too. Exclusive creation below also closes the check race.
    supplied = Path(path).expanduser()
    target = supplied.parent.resolve(strict=True) / supplied.name
    try:
        target.lstat()
    except FileNotFoundError:
        return target
    raise FileExistsError(f"Output must be a NEW file: {target}")


def _write_outputs(outputs: list[tuple[Path, bytes]]) -> None:
    """Exclusive-create the pair; rollback only files this invocation owns.

    This is not a cross-filesystem atomic transaction. On a caught error,
    existing files (including concurrent replacements) are never removed.
    On POSIX descriptors remain open until cleanup, preventing inode reuse;
    Windows requires closing our handles before unlinking our files.
    """
    created: list[tuple[Path, int, tuple[int, int]]] = []
    closed: set[int] = set()
    try:
        for path, _ in outputs:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            fd = os.open(path, flags, 0o600)
            identity = os.fstat(fd)
            created.append((path, fd, (identity.st_dev, identity.st_ino)))
        for (_, fd, _), (_, payload) in zip(created, outputs):
            remaining = memoryview(payload)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("Output write made no progress")
                remaining = remaining[written:]
            os.fsync(fd)
        for (path, _, identity), (_, payload) in zip(created, outputs):
            current = path.lstat()
            if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != identity:
                raise OSError(f"Output path changed during export: {path}")
            actual = af_metadata._source_identity(path)
            current = path.lstat()
            if (not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != identity
                    or (actual["stat_identity"]["device"], actual["stat_identity"]["inode"]) != identity
                    or actual["sha256"] != hashlib.sha256(payload).hexdigest()):
                raise OSError(f"Output bytes or identity changed during export: {path}")
    except BaseException as error:
        if _UNLINK_REQUIRES_CLOSED_FILES:
            for _, fd, _ in created:
                os.close(fd)
                closed.add(fd)
        for path, _, identity in created:
            try:
                current = path.lstat()
                if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == identity:
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                # Attempt every rollback even if one directory became
                # unwritable; retain the original error and surface residue.
                error.add_note(f"Could not clean new output {path}: {cleanup_error}")
        raise
    finally:
        for _, fd, _ in created:
            if fd not in closed:
                os.close(fd)


def _snapshot(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return af_metadata._source_identity(path), None
    except (OSError, ValueError, RuntimeError) as exc:
        return None, str(exc)[:1000]


def _parser_unchanged(parser: Any) -> bool:
    if not isinstance(parser, dict):
        return False
    if any(not isinstance(parser.get(key), str) or not parser[key]
           for key in ("name", "path", "version", "adapter_version")):
        return False
    if parser["name"] != "ExifTool" or not Path(parser["path"]).is_absolute():
        return False
    try:
        return af_metadata._stat_identity(Path(parser["path"]).stat()) == parser.get("stat_identity")
    except OSError:
        return False


def _decode_jpeg(source: Path, max_edge: int) -> tuple[Image.Image, dict[str, Any], bool]:
    """Decode an exact, hashed snapshot of this file's complete primary JPEG.

    Some camera JPEGs use an MPO container. Only its initial, full-size frame
    zero is decoded; never seek an embedded thumbnail or second image. Hashing
    the actual BytesIO input also closes Windows stat/mtime restoration gaps.
    """
    image = original = transposed = None
    try:
        with source.open("rb") as stream:
            before = af_metadata._stat_identity(os.fstat(stream.fileno()))
            # Bound the immutable encoded snapshot separately from Pillow's
            # decompressed-pixel limit. No reduced decode or thumbnail fallback.
            limit = 256 * 1024 * 1024
            if before["size"] > limit:
                raise ValueError("JPEG source exceeds the 256 MiB diagnostic snapshot limit")
            encoded = stream.read(limit + 1)
            if len(encoded) > limit:
                raise ValueError("JPEG source exceeds the 256 MiB diagnostic snapshot limit")
            after = af_metadata._stat_identity(os.fstat(stream.fileno()))
        encoded_digest = hashlib.sha256(encoded).hexdigest()
        with warnings.catch_warnings(), io.BytesIO(encoded) as snapshot:
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            original = Image.open(snapshot)
            image_format = original.format
            if image_format not in {"JPEG", "MPO"} or original.tell() != 0:
                raise ValueError("Diagnostic images require original JPEG content and primary frame zero")
            source_size = original.size
            orientation = original.getexif().get(274)
            frame_count = getattr(original, "n_frames", 1)
            original.load()
            transposed = ImageOps.exif_transpose(original)
            image = transposed.convert("RGB")
            frame_size = image.size
            image.info.clear()
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        return image, {"decoded_source_size": list(source_size),
                       "display_frame_size": list(frame_size), "raster_size": list(image.size),
                       "exif_orientation": orientation, "exif_transposed": True,
                       "exif_transpose_count": 1, "crop": "full_frame", "annotation_applied": False,
                       "pixel_mode": "RGB", "clean_pixels_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
                       "decoded_bytes_sha256": encoded_digest,
                       "decoder": {"name": "Pillow", "version": Image.__version__, "format": image_format,
                                   "primary_frame_index": 0, "container_frame_count": frame_count,
                                   "encoded_snapshot": True},
                       "decode_stat_before": before, "decode_stat_after": after}, before == after
    except BaseException:
        if image is not None:
            image.close()
        raise
    finally:
        if transposed is not None:
            transposed.close()
        if original is not None:
            original.close()


def _display_profile(metadata: dict[str, Any]) -> str | None:
    """Explicit per-model display profiles; never a blanket vendor promise."""
    camera = metadata.get("diagnostics", {}).get("camera", {})
    if camera.get("make_issue") is not None or camera.get("model_issue") is not None:
        return None
    if camera.get("is_panasonic") is True and camera.get("model") == "DC-S5M2":
        return "panasonic_s5m2_single_area_v1"
    if (camera.get("vendor") == "sony" and camera.get("model") == "ILCE-6600"
            and camera.get("display_profile") == "sony_ilce_6600_focus_location_v1"):
        return "sony_ilce_6600_focus_location_v1"
    if (camera.get("vendor") == "canon" and camera.get("model") == "Canon EOS R3"
            and camera.get("display_profile") == "canon_eos_r3_afinfo2_v1"):
        return "canon_eos_r3_afinfo2_v1"
    if (camera.get("vendor") == "nikon" and camera.get("model") == "NIKON Z5_2"
            and camera.get("display_profile") == "nikon_z5_2_afinfo2_0402_v1"):
        return "nikon_z5_2_afinfo2_0402_v1"
    return None


def _processing_evidence(metadata: dict[str, Any]) -> dict[str, Any]:
    """Known processing history makes the original geometry assumption unsafe.

    Even /metadata history is not proof of a pixel edit or an unchanged raster.
    Absence of these tags does not authenticate an original camera image.
    """
    names = {"Software", "CreatorTool", "HistorySoftwareAgent", "HistoryAction", "HistoryChanged"}
    raw = metadata.get("raw_tags", {})
    fields = {key: value for key, value in raw.items() if key.split(":")[-1] in names}
    external = ("adobe", "photoshop", "lightroom", "capture one", "dxo", "darktable",
                "rawtherapee", "affinity", "gimp", "luminar", "on1", "pixelmator")
    reasons = []
    for key, value in fields.items():
        name = key.split(":")[-1]
        if value in (None, "", []):
            continue
        if name.startswith("History"):
            reasons.append(key)
        elif not isinstance(value, str) or any(word in value.casefold() for word in external):
            reasons.append(key)
    crop_expected = {"CropTop": 0, "CropLeft": 0, "CropBottom": 1, "CropRight": 1, "CropAngle": 0}
    crop_fields = {key: value for key, value in raw.items()
                   if "XMP-crs" in key.split(":")[:-1]
                   and key.split(":")[-1] in {*crop_expected, "HasCrop", "CropConstrainToWarp"}}
    fields.update(crop_fields)
    if crop_fields:
        # Full, unique explicit identity crop only; no absent defaults inferred.
        for name, expected in crop_expected.items():
            entries = [value for key, value in crop_fields.items() if key.split(":")[-1] == name]
            try:
                valid = (len(entries) == 1 and not isinstance(entries[0], bool)
                         and isinstance(entries[0], (int, float, str))
                         and Decimal(str(entries[0])) == Decimal(expected))
            except InvalidOperation:
                valid = False
            if not valid:
                reasons.append("XMP-crs:" + name)
        for name in ("HasCrop", "CropConstrainToWarp"):
            entries = [value for key, value in crop_fields.items() if key.split(":")[-1] == name]
            if entries and (len(entries) != 1 or entries[0] not in (False, 0, "0", "False", "false")):
                reasons.append("XMP-crs:" + name)
    return {"status": "unknown" if reasons else "not_validated",
            "reason": "processing_geometry_unconfirmed" if reasons else "no_known_processing_evidence_only",
            "fields": fields, "trigger_fields": sorted(reasons),
            "pixel_edit_proven": False, "camera_original_authenticated": False,
            "limitation": "History can describe metadata-only saves; missing history cannot authenticate original pixels"}


def _geometry(metadata: dict[str, Any], binding: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any]:
    result = {"status": "unknown", "reason": None, "center_drawn": False, "box_drawn": False,
              "center_xy_hypothesis": None, "bbox_xywh_hypothesis": None,
              "coordinate_frame": "exif_transposed_full_jpeg_normalized_edge_coordinates",
              "meaning": "Recorded AF area center and optional search area; not an eye or proof of focus"}
    def reject(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result
    if not binding["source_unchanged"] or not binding["metadata_source_matches"]:
        return reject("source_changed_or_unbound")
    if metadata.get("status") != "not_validated":
        return reject("metadata_not_supported")
    if not binding["parser_unchanged"]:
        return reject("parser_changed_or_unbound")
    diagnostics = metadata.get("diagnostics", {})
    camera = diagnostics.get("camera", {})
    from . import af_canon
    if (af_canon.matches_camera(metadata.get("raw_tags", {}))
            and af_canon.diagnostics(metadata["raw_tags"]) != diagnostics):
        return reject("record_collection_invalid")
    if _display_profile(metadata) is None:
        return reject("unsupported_camera")
    if _display_profile(metadata) == "sony_ilce_6600_focus_location_v1":
        result["meaning"] = "Camera playback focus location; center may be fallback, not focus-success or subject proof"
    if _display_profile(metadata) == "canon_eos_r3_afinfo2_v1":
        result["meaning"] = "Camera-recorded AF point with preserved selected/in-focus role; not optical sharpness proof"
    if _display_profile(metadata) == "nikon_z5_2_afinfo2_0402_v1":
        result["meaning"] = "Camera-recorded AF region; last-operation result is not optical sharpness proof"
    if preview.get("processing_evidence") != _processing_evidence(metadata):
        return reject("processing_evidence_missing_or_mismatched")
    if preview["processing_evidence"]["status"] != "not_validated":
        return reject("processing_geometry_unconfirmed")
    if preview.get("dimension_evidence", {}).get("status") != "not_validated":
        return reject("camera_dimensions_missing_ambiguous_or_mismatched")
    mode = diagnostics.get("mode", {})
    if mode.get("status") != "not_validated" or (mode.get("single_area_candidate") is not True and mode.get("display_records_candidate") is not True and mode.get("display_reference_area") is not True):
        return reject("mode_not_supported")
    orientation = diagnostics.get("orientation", {})
    if orientation.get("status") != "not_validated":
        return reject("orientation_not_supported")
    actual = preview["exif_orientation"]
    if isinstance(actual, bool) or not isinstance(actual, int) or actual not in range(1, 9):
        return reject("jpeg_orientation_missing_or_invalid")
    if not preview["orientation_agrees"]:
        return reject("orientation_mismatch")
    coordinates = diagnostics.get("coordinates", {})
    if coordinates.get("status") != "not_validated":
        return reject("coordinates_not_supported")
    if _display_profile(metadata) == "canon_eos_r3_afinfo2_v1":
        if len(coordinates.get("display_records", [])) > 4096:
            return reject("record_collection_exceeds_display_limit")
        from .af_markers import canon_records
        records = canon_records(metadata, actual)
        if not records:
            return reject("record_collection_invalid")
        singleton = records[0] if len(records) == 1 else {}
        result.update(status="not_validated", reason="same_jpeg_unvalidated_af_area_hypothesis",
                      records=records, center_xy_hypothesis=singleton.get("center_xy_hypothesis"),
                      bbox_xywh_hypothesis=singleton.get("bbox_xywh_hypothesis"),
                      center_drawn=True, box_drawn=True)
        return result
    # Use the unrotated values exactly, then apply the adapter's fixed 8-way
    # EXIF math once. Never rotate the already-oriented hypothesis again.
    try:
        center = af_metadata.orient_normalized_point_hypothesis(coordinates["center"]["value"], actual)
        if center != coordinates.get("oriented_center_xy_hypothesis"):
            return reject("coordinate_hypothesis_mismatch")
        raw_box = coordinates.get("raw_bbox_xywh_hypothesis")
        box = af_metadata.orient_normalized_box_hypothesis(raw_box, actual) if raw_box is not None else None
        if box != coordinates.get("oriented_bbox_xywh_hypothesis"):
            return reject("coordinate_hypothesis_mismatch")
    except (KeyError, TypeError, ValueError):
        return reject("coordinates_not_supported")
    result.update(status="not_validated", reason="same_jpeg_unvalidated_af_area_hypothesis",
                  center_xy_hypothesis=center, bbox_xywh_hypothesis=box,
                  center_drawn=True, box_drawn=box is not None)
    return result


def _dashed_line(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float], width: int,
                 pattern: tuple[int, ...] = (9, 7)) -> None:
    distance = math.dist(start, end)
    if distance <= 0:
        return
    offset, segment = 0, 0
    while offset < distance:
        length = pattern[segment % len(pattern)]
        if segment % 2 == 0:
            a, b = offset/distance, min(offset+length, distance)/distance
            points = [(start[0]+(end[0]-start[0])*t, start[1]+(end[1]-start[1])*t) for t in (a, b)]
            draw.line(points, fill="black", width=width+2)
            draw.line(points, fill="#ffdd00", width=width)
        offset += length
        segment += 1


def _render(image: Image.Image, geometry: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    # Draw on a separate full-frame raster. Pillow clips at its border; the
    # reported normalized geometry itself is never clamped or moved inward.
    raster = image.copy()
    canvas = None
    try:
        draw = ImageDraw.Draw(raster)
        width, height = raster.size
        records = geometry.get("records") or [geometry]
        center = geometry.get("center_xy_hypothesis")
        for record in records:
            center = record.get("center_xy_hypothesis")
            box = record.get("bbox_xywh_hypothesis")
            if box is not None:
                x, y, w, h = box
                corners = [(x*width, y*height), ((x+w)*width, y*height),
                           ((x+w)*width, (y+h)*height), (x*width, (y+h)*height)]
                for a, b in zip(corners, corners[1:] + corners[:1]):
                    _dashed_line(draw, a, b, 2, {"selected": (12, 6), "camera_reported_in_focus": (4, 4), "selected_and_camera_reported_in_focus": (12, 4, 4, 4)}.get(record.get("role"), (9, 7)))
            if center is not None:
                x, y = center[0]*width, center[1]*height
                for points in (((x-12, y), (x+12, y)), ((x, y-12), (x, y+12))):
                    draw.line(points, fill="black", width=5)
                    draw.line(points, fill="#ffdd00", width=2)
        banner_height = 108
        canvas = Image.new("RGB", (max(640, width), height+banner_height), "#202020")
        left = (canvas.width-width)//2
        canvas.paste(raster, (left, banner_height))
        label = ImageDraw.Draw(canvas)
        label.rectangle((0, 0, canvas.width, banner_height-1), fill="#ffce52")
        label.text((14, 8), WARNING, fill="black", font=ImageFont.load_default(size=27))
        title = "PLAYBACK LOCATION: not an eye or proof of focus" if geometry.get("meaning", "").startswith("Camera playback") else "AF AREA DIAGNOSTIC: not an eye or proof of focus"
        if geometry.get("meaning", "").startswith("Camera-recorded"):
            title = "RECORDED AF POINT: not optical sharpness proof"
        label.text((14, 43), title, fill="black", font=ImageFont.load_default(size=18))
        detail = "Cross = recorded center; dashed box = recorded area" if center is not None else "No AF geometry: " + str(geometry["reason"])
        if geometry.get("records"):
            detail = f"{len(geometry['records'])} records: dash=selected; dots=camera in-focus; dash-dot=both"
        label.text((14, 76), detail, fill="black", font=ImageFont.load_default(size=16))
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        payload = buffer.getvalue()
        return payload, {"sha256": hashlib.sha256(payload).hexdigest(), "format": "PNG",
                         "output_size": list(canvas.size), "image_rect_xyxy": [left, banner_height, left+width, banner_height+height],
                         "warning": WARNING, "banner_height": banner_height,
                         "output_pixels_sha256": hashlib.sha256(canvas.tobytes()).hexdigest()}
    finally:
        raster.close()
        if canvas is not None:
            canvas.close()


def _check_limits(timeout: float, max_edge: int) -> None:
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not 0 < timeout <= 120):
        raise ValueError("timeout must be finite, >0 and <=120")
    if isinstance(max_edge, bool) or not isinstance(max_edge, int) or not 64 <= max_edge <= 4096:
        raise ValueError("max_edge must be an integer from 64 to 4096")


def _dimension_evidence(metadata: dict[str, Any], size: list[int]) -> dict[str, Any]:
    """Reject obvious crops/resizes; agreement cannot authenticate an original."""
    raw = metadata.get("raw_tags", {})
    if _display_profile(metadata) == "nikon_z5_2_afinfo2_0402_v1":
        from .af_nikon import jpeg_dimension_evidence
        return jpeg_dimension_evidence(raw, size)
    if _display_profile(metadata) == "canon_eos_r3_afinfo2_v1":
        from .af_canon import jpeg_dimension_evidence
        return jpeg_dimension_evidence(raw, size)
    result = {"status": "unknown", "reason": "required_dimensions_missing_ambiguous_or_mismatched",
              "fields": {}, "camera_original_required": True, "camera_original_authenticated": False,
              "limitation": "Matching dimensions cannot rule out same-size edits or crop-and-resize"}
    agrees = True
    sony = _display_profile(metadata) == "sony_ilce_6600_focus_location_v1"
    fields = (("SonyImageWidth", "Sony", size[0]), ("SonyImageHeight", "Sony", size[1])) if sony else (
        ("PanasonicImageWidth", "Panasonic", size[0]), ("PanasonicImageHeight", "Panasonic", size[1]))
    fields += (("ExifImageWidth", "ExifIFD", size[0]), ("ExifImageHeight", "ExifIFD", size[1]))
    if sony:
        value, issue = af_metadata._unique_tag(raw, "FullImageSize", {"Sony"})
        from .af_sony import _ints
        full = _ints(value, 2, maximum=4294967295)
        valid = issue is None and full == size
        result["fields"]["FullImageSize"] = {"value": value, "issue": issue, "agrees_with_stored_size": valid}
        agrees = agrees and valid
        frame = metadata.get("diagnostics", {}).get("coordinates", {}).get("reference_frame_size")
        result["fields"]["FocusLocationFrame"] = {"value": frame, "agrees_with_stored_size": frame == size}
        agrees = agrees and frame == size
    for name, group, expected in fields:
        value, issue = af_metadata._unique_tag(raw, name, {group})
        valid = issue is None and isinstance(value, int) and not isinstance(value, bool) and value == expected
        result["fields"][name] = {"value": value, "issue": issue, "agrees_with_stored_size": valid}
        agrees = agrees and valid
    if agrees:
        result.update(status="not_validated", reason="stored_and_recorded_dimensions_agree_only")
    return result


def prepare_af_diagnostic(source: str | os.PathLike[str], *,
                          exiftool_path: str | os.PathLike[str] | None = None,
                          timeout: float = 10.0, max_edge: int = 1600
                          ) -> tuple[Image.Image | None, dict[str, Any]]:
    """Read one original; return owned clean RGB pixels and diagnostic evidence.

    No files are written. The caller MUST close a returned image. RAW/non-JPEG
    inputs return information only, with no sibling search or pixel decode.
    Source mismatch returns no pixels and no drawable geometry. A returned
    image is full-frame and EXIF-transposed exactly once; coordinates remain
    unvalidated and must never enter a validated overlay/decision pathway.
    """
    _check_limits(timeout, max_edge)
    original = Path(source).expanduser().resolve(strict=True)
    jpeg = original.suffix.lower() in _JPEG_SUFFIXES
    before, before_error = _snapshot(original)
    if before is None:
        raise OSError("Cannot bind source: " + str(before_error))
    metadata = af_metadata.read_af_metadata(original, exiftool_path=exiftool_path, timeout=timeout)
    image, preview, decode_unchanged = None, None, True
    try:
        if jpeg:
            image, preview, decode_unchanged = _decode_jpeg(original, max_edge)
        after, after_error = _snapshot(original)
        metadata_source = metadata.get("source", {})
        metadata_matches = (metadata_source.get("unchanged_during_read") is True
                            and all(metadata_source.get(key) == value for key, value in before.items()))
        unchanged = before == after and decode_unchanged
        if preview is not None:
            unchanged = (unchanged
                         and af_metadata._path_matches_open_handle(
                             before["stat_identity"], preview["decode_stat_before"])
                         and preview["decoded_bytes_sha256"] == before["sha256"])
            orientation = metadata.get("diagnostics", {}).get("orientation", {})
            try:
                adapter_orientation = af_metadata._orientation(orientation.get("exif_value"))
            except ValueError:
                adapter_orientation = None
            preview.update(metadata_orientation=adapter_orientation,
                           orientation_agrees=orientation.get("status") == "not_validated"
                           and adapter_orientation is not None and adapter_orientation == preview["exif_orientation"],
                           dimension_evidence=_dimension_evidence(metadata, preview["decoded_source_size"]),
                           processing_evidence=_processing_evidence(metadata))
        binding = {"before": before, "after": after, "after_error": after_error,
                   "source_unchanged": bool(unchanged), "metadata_source_matches": metadata_matches,
                   "parser_unchanged": _parser_unchanged(metadata.get("parser")),
                   "metadata_path_read_atomic": False,
                   "limitation": "ExifTool reads the original path separately; fully restored concurrent A-B-A metadata changes cannot be excluded",
                   "source_policy": "same_original_file_only; no_asset_cache_RAW_pixels_or_sibling_lookup"}
        report = {"schema": SCHEMA_VERSION, "status": "not_validated" if jpeg else "unsupported",
                  "purpose": "AF area diagnostic inspection; not focus-hit or subject/quality evidence",
                  "camera_mapping": {"status": "not_validated", "reason": "real_camera_coordinate_semantics_not_calibrated"},
                  "source_assumption": "Requires a camera-original JPEG; no cryptographic authenticity claim; same-size edits cannot be excluded",
                  "metadata": metadata, "binding": binding, "preview": preview,
                  "geometry": None, "automatic_decisions_enabled": False,
                  "usable_for_subject_selection": False, "usable_for_keep_reject": False}
        if preview is not None:
            report["geometry"] = _geometry(metadata, binding, preview)
            report["geometry"].update(center_drawn=False, box_drawn=False)
            if not binding["source_unchanged"] or not binding["metadata_source_matches"]:
                report.update(status="error", reason="source_changed_or_unbound")
                image.close()
                image = None
        else:
            report["reason"] = "non_jpeg_information_only; diagnostic image refused; no sibling lookup"
        return image, report
    except BaseException:
        if image is not None:
            image.close()
        raise


def export_af_diagnostic(source: str | os.PathLike[str], *,
                         report_path: str | os.PathLike[str],
                         preview_path: str | os.PathLike[str] | None = None,
                         exiftool_path: str | os.PathLike[str] | None = None,
                         timeout: float = 10.0, max_edge: int = 1600) -> dict[str, Any]:
    """Export a NEW JSON/PNG pair; for RAW/non-JPEG, permit JSON information only.

    A successful export is not calibration. Unknown metadata, mode, orientation
    yields a clearly labelled unboxed JPEG preview. An unbound source yields no
    output; a later source change during rendering removes geometry. Failures
    never overwrite existing files. Parent directories must already exist.
    """
    _check_limits(timeout, max_edge)
    original = Path(source).expanduser().resolve(strict=True)
    report_target = _new_target(report_path)
    preview_target = _new_target(preview_path) if preview_path is not None else None
    if report_target == original or preview_target == original or report_target == preview_target:
        raise ValueError("Outputs must be distinct NEW files, never the source")
    jpeg = original.suffix.lower() in _JPEG_SUFFIXES
    if preview_target is not None and not jpeg:
        raise ValueError("Diagnostic images require an original JPEG; RAW is information-only, no sibling lookup")
    if jpeg and preview_target is None:
        raise ValueError("JPEG diagnostics require an explicit NEW PNG preview_path")
    if preview_target is not None and preview_target.suffix.lower() != ".png":
        raise ValueError("preview_path must have a .png extension")
    image, report = prepare_af_diagnostic(original, exiftool_path=exiftool_path, timeout=timeout, max_edge=max_edge)
    try:
        metadata, binding, preview = report["metadata"], report["binding"], report["preview"]
        image_bytes = None
        if preview is not None:
            if image is None:
                raise ValueError("Source changed or could not be bound to metadata; no diagnostic files written")
            report["geometry"] = _geometry(metadata, binding, preview)
            image_bytes, rendered = _render(image, report["geometry"])
            # Cover rendering too. A later change removes all geometry from the
            # already-loaded, exact JPEG snapshot instead of relabelling it.
            final_source, final_error = _snapshot(original)
            binding.update(after=final_source, after_error=final_error,
                           source_unchanged=bool(binding["source_unchanged"] and final_source == binding["before"]),
                           parser_unchanged=binding["parser_unchanged"] and _parser_unchanged(metadata.get("parser")))
            final_geometry = _geometry(metadata, binding, preview)
            if final_geometry != report["geometry"]:
                report["geometry"] = final_geometry
                image_bytes, rendered = _render(image, final_geometry)
            preview.update(rendered, path=str(preview_target), annotation_applied=True)
        encoded = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        outputs = [(report_target, encoded)]
        if image_bytes is not None:
            outputs.append((preview_target, image_bytes))
        _write_outputs(outputs)
        return report
    finally:
        if image is not None:
            image.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Original JPEG; RAW permits a JSON report only")
    parser.add_argument("--report", required=True, type=Path, help="NEW JSON report, never overwrite")
    parser.add_argument("--preview", type=Path, help="NEW diagnostic PNG; required for JPEG, refused for RAW")
    parser.add_argument("--exiftool", type=Path, help="Trusted external ExifTool executable; otherwise PATH")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--max-edge", type=int, default=1600, help="Full-frame preview maximum edge, 64..4096")
    args = parser.parse_args(argv)
    try:
        report = export_af_diagnostic(args.source, report_path=args.report, preview_path=args.preview,
                                      exiftool_path=args.exiftool, timeout=args.timeout, max_edge=args.max_edge)
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        parser.exit(2, f"Cannot export AF diagnostic: {exc}\n")
    print(f"{WARNING}: created {args.report}" + (f" and {args.preview}" if args.preview else " (information only)"))
    return 2 if report["metadata"].get("status") == "error" or not report["binding"]["source_unchanged"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
