"""Bound, latest-only AF preview diagnostics for the optional group-editor view.

This is deliberately separate from ``af_overlay``'s calibrated display contract.
The original metadata and geometry remain ``not_validated``; only the projected
UI result is called ``diagnostic``. Nothing here selects a person or changes a
quality/keep decision. The caller supplies an explicit JPEG or a separately bound RAW extraction route;
never an asset cache, inferred sibling or already-rotated thumbnail.

All filesystem/ExifTool/decode work runs in one daemon. One pending request and
one completion are retained. Popped images belong to the caller; all other
images are closed on replacement, invalidation, error or shutdown. There is no
persistent cache. Shutdown never waits for an outstanding read.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Any

from PIL import Image

from . import af_diagnostic, af_metadata


DISPLAY_FRAME = "exif_transposed_full_jpeg_normalized_edge_coordinates"
_JPEG_SUFFIXES = {".jpg", ".jpeg", ".jpe"}
_STAT_KEYS = ("size", "mtime_ns", "ctime_ns", "device", "inode")


@dataclass(frozen=True)
class JpegDiagnosticGeometry:
    """Canvas-space diagnostic only; box/clip rectangle are xyxy."""

    status: str
    reason: str
    center: tuple[float, float] | None = None
    box: tuple[float, float, float, float] | None = None
    clip_rect: tuple[float, float, float, float] | None = None
    markers: tuple["JpegDiagnosticGeometry", ...] = ()
    role: str | None = None
    index: int | None = None


@dataclass(frozen=True)
class _BoundMarker:
    center: tuple[float, float]
    box: tuple[float, float, float, float] | None
    role: str
    index: int


def _sealed_markers(geometry: dict) -> tuple[_BoundMarker, ...]:
    return tuple(_BoundMarker(_numbers(r['center_xy_hypothesis'], 2),
                             _numbers(r.get('bbox_xywh_hypothesis'), 4), r['role'], r['index'])
                 for r in geometry.get('records', ()))


@dataclass(frozen=True)
class _PixelBinding:
    image: Image.Image
    size: tuple[int, int]
    digest: str
    evidence: str
    center: tuple[float, float] | None
    box: tuple[float, float, float, float] | None
    reason: str = "same_jpeg_unvalidated_af_area_hypothesis"
    markers: tuple[_BoundMarker, ...] = ()


class _PreparedBundle(dict):
    """Only the worker can issue the in-memory pixel/evidence association.

    A same-size substitute image or a fabricated plain dict is not proof.
    Public mappings are useful for UI status/tests; mutating any evidence makes
    projection fail closed. The Pillow object is mutable, so its live pixels
    are also checked before projection, not merely its identity or dimensions.
    """

    _pixel_binding: _PixelBinding | None = None


def _numbers(value: Any, length: int) -> tuple[float, ...] | None:
    if not isinstance(value, (tuple, list)) or len(value) != length:
        return None
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
        return None
    try:
        result = tuple(float(x) for x in value)
        return result if all(math.isfinite(x) for x in result) else None
    except (ValueError, OverflowError):
        return None


def _size(value: Any) -> tuple[int, int] | None:
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(isinstance(x, bool) or not isinstance(x, int) or not 0 < x <= 2**31-1 for x in value)):
        return None
    return tuple(value)


def _stat_key(value: Any) -> tuple[int, ...] | None:
    if (not isinstance(value, Mapping)
            or any(isinstance(value.get(k), bool) or not isinstance(value.get(k), int) for k in _STAT_KEYS)
            or value["size"] < 0):
        return None
    return tuple(value[k] for k in _STAT_KEYS)


def _source_key(value: Any) -> tuple[Any, ...] | None:
    if not isinstance(value, Mapping):
        return None
    path, digest = value.get("path"), value.get("sha256")
    identity = _stat_key(value.get("stat_identity"))
    if (not isinstance(path, str) or not os.path.isabs(path)
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or identity is None):
        return None
    return path, digest, identity


def _parser_key(value: Any) -> tuple[Any, ...] | None:
    if not isinstance(value, Mapping):
        return None
    strings = tuple(value.get(k) for k in ("name", "path", "version", "adapter_version"))
    identity = _stat_key(value.get("stat_identity"))
    if (any(not isinstance(x, str) or not x for x in strings) or identity is None
            or strings[0] != "ExifTool" or not os.path.isabs(strings[1])):
        return None
    return *strings, identity


def _fingerprint(bundle: Mapping[str, Any]) -> str:
    value = {key: bundle.get(key) for key in ("metadata", "provenance", "geometry", "request_path")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def _hidden(reason: str) -> JpegDiagnosticGeometry:
    return JpegDiagnosticGeometry("hidden", reason)


def _status_text(metadata: Mapping[str, Any], reason: str) -> str:
    if reason == "record_collection_exceeds_display_limit":
        return "AF：记录超过4096条显示上限；保留元数据，不截断绘制"
    if (reason == "mode_not_supported" and af_diagnostic._display_profile(dict(metadata)) == "canon_eos_r3_afinfo2_v1"
            and metadata.get("diagnostics", {}).get("mode", {}).get("status") == "not_validated"):
        return "AF：未取得唯一可显示记录点，保留原始信息"
    if reason == "same_jpeg_unvalidated_af_area_hypothesis" and af_diagnostic._display_profile(dict(metadata)) == "nikon_z5_2_afinfo2_0402_v1":
        return "AF：相机记录区域（最近AF结果不等于清晰度）"
    if reason == "same_jpeg_unvalidated_af_area_hypothesis" and af_diagnostic._display_profile(dict(metadata)) == "canon_eos_r3_afinfo2_v1":
        records = metadata.get("diagnostics", {}).get("coordinates", {}).get("display_records", [])
        if len(records) > 1:
            return f"AF：{len(records)}条记录；长虚线=选中，点线=机报合焦，点划线=两者（非清晰度证明）"
        role = metadata.get("diagnostics", {}).get("coordinates", {}).get("display_role")
        labels = {"selected_and_camera_reported_in_focus": "选中且相机报告合焦", "camera_reported_in_focus": "相机报告合焦", "selected": "选中区域"}
        return "AF：" + labels.get(role, "相机记录点") + "（非清晰度证明）"
    if (reason == "coordinates_not_supported" and metadata.get("diagnostics", {}).get("coordinates", {}).get("reason") == "camera_center_fallback_ambiguous"):
        return "AF：中心回放位置可能是缺省值，暂不绘制"
    if reason == "same_jpeg_unvalidated_af_area_hypothesis" and af_diagnostic._display_profile(dict(metadata)) == "sony_ilce_6600_focus_location_v1":
        return "AF：同源 JPEG 回放位置（非眼睛或合焦证明）"
    if reason == "same_jpeg_unvalidated_af_area_hypothesis":
        return "AF：同源 JPEG 区域（未校准，非眼睛或合焦证明）"
    if reason in {"processing_geometry_unconfirmed", "processing_evidence_missing_or_mismatched"}:
        return "AF：处理来源的几何映射未确认，暂不绘制"
    if reason == "jpeg_source_required":
        return "AF：仅支持明确选择的同源 JPEG"
    if "source" in reason and any(x in reason for x in ("changed", "mismatch", "unbound")):
        return "AF：原件已变化或来源不匹配，请重新读取"
    if reason == "exiftool_unavailable" or metadata.get("reason") == "exiftool_unavailable":
        return "AF：未找到 ExifTool（可选）"
    if "parser" in reason or "exiftool_changed" in reason:
        return "AF：解析器已变化或无法核对，暂不绘制"
    if "timeout" in reason:
        return "AF：读取超时"
    if reason == "exiftool_read_warning" or metadata.get("reason") == "exiftool_read_warning":
        return "AF：元数据有警告，暂不绘制"
    if reason == "source_unavailable":
        return "AF：原件无法读取"
    if (reason in {"jpeg_metadata_dimensions_mismatch", "camera_dimensions_missing_ambiguous_or_mismatched"}
            and af_diagnostic._display_profile(dict(metadata)) == "nikon_z5_2_afinfo2_0402_v1"):
        return "AF：此文件的裁切或处理映射未确认，暂不绘制"
    if reason in {"unsupported_jpeg_dimensions", "jpeg_metadata_dimensions_mismatch"}:
        return "AF：此 JPEG 尺寸尚不支持区域诊断"
    if reason == "unsupported_camera":
        return "AF：暂不支持此相机的区域诊断"
    if reason == "mode_not_supported":
        return "AF：此对焦模式暂不支持区域诊断"
    if metadata.get("status") == "missing":
        return "AF：未发现对焦位置信息"
    return "AF：诊断信息不可用，暂不绘制"


def _error_bundle(path: str, reason: str) -> _PreparedBundle:
    metadata = {"status": "error", "reason": reason, "source": {"path": path},
                "usable_for_subject_selection": False, "usable_for_keep_reject": False}
    return _PreparedBundle(metadata=metadata, clean_image=None, request_path=path,
                           provenance={"source": {"path": path}},
                           geometry={"status": "unknown", "reason": reason},
                           status_text=_status_text(metadata, reason), status="error", reason=reason)


def _close_image(image: Any) -> None:
    if isinstance(image, Image.Image):
        image.close()


def _close_bundle(bundle: Mapping[str, Any] | None) -> None:
    if bundle is not None:
        _close_image(bundle.get("clean_image"))
        if isinstance(bundle, _PreparedBundle):
            bundle._pixel_binding = None


def _geometry_reason(metadata: dict[str, Any], provenance: dict[str, Any],
                     geometry: Any) -> str | None:
    """Check prepared hypotheses; never orient them a second time."""
    binding, preview = provenance["binding"], provenance["preview"]
    if metadata.get("status") != "not_validated":
        return str(metadata.get("reason") or "metadata_not_supported")
    if (metadata.get("usable_for_subject_selection") is not False
            or metadata.get("usable_for_keep_reject") is not False):
        return "automatic_decisions_not_disabled"
    if (binding.get("metadata_source_matches") is not True
            or metadata.get("source", {}).get("unchanged_during_read") is not True
            or _source_key(metadata.get("source")) != _source_key(provenance["source"])):
        return "metadata_source_mismatch"
    if binding.get("parser_unchanged") is not True or _parser_key(provenance.get("parser")) is None:
        return "parser_changed_or_unbound"
    diagnostics = metadata.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return "metadata_not_supported"
    camera = diagnostics.get("camera", {})
    profile = af_diagnostic._display_profile(metadata)
    from . import af_canon
    if (af_canon.matches_camera(metadata.get("raw_tags", {}))
            and af_canon.diagnostics(metadata["raw_tags"]) != diagnostics):
        return "record_collection_invalid"
    if profile is None:
        return "unsupported_camera"
    mode = diagnostics.get("mode", {})
    area_mode = mode.get("AFAreaMode", {})
    if mode.get("status") != "not_validated" or (mode.get("single_area_candidate") is not True and mode.get("display_records_candidate") is not True):
        return "mode_not_supported"
    if profile == "panasonic_s5m2_single_area_v1" and (
            area_mode.get("issue") is not None
            or af_metadata._numbers(area_mode.get("raw_value"), 2) != [16.0, 0.0]):
        return "mode_not_supported"
    orientation = diagnostics.get("orientation", {})
    actual_orientation = preview.get("exif_orientation")
    if (isinstance(actual_orientation, bool) or not isinstance(actual_orientation, int)
            or actual_orientation not in range(1, 9)
            or orientation.get("status") != "not_validated"
            or preview.get("orientation_agrees") is not True
            or af_metadata._orientation(orientation.get("exif_value")) != actual_orientation):
        return "orientation_mismatch"
    source_size = _size(preview.get("decoded_source_size"))
    native_frame = (6048, 4032) if profile == "nikon_z5_2_afinfo2_0402_v1" else (6000, 4000)
    expected_display = native_frame[::-1] if actual_orientation >= 5 else native_frame
    if source_size != native_frame or _size(preview.get("display_frame_size")) != expected_display:
        return "unsupported_jpeg_dimensions"
    # Matching dimensions is not authentication of an unedited camera JPEG.
    dimensions = preview.get("dimension_evidence", {})
    if (dimensions.get("status") != "not_validated"
            or dimensions.get("reason") != "stored_and_recorded_dimensions_agree_only"
            or dimensions.get("camera_original_required") is not True
            or dimensions.get("camera_original_authenticated") is not False
            or dimensions != af_diagnostic._dimension_evidence(metadata, list(native_frame))):
        return "jpeg_metadata_dimensions_mismatch"
    processing = preview.get("processing_evidence")
    if processing != af_diagnostic._processing_evidence(metadata):
        return "processing_evidence_missing_or_mismatched"
    if processing["status"] != "not_validated":
        return "processing_geometry_unconfirmed"
    coordinates = diagnostics.get("coordinates", {})
    if coordinates.get("status") != "not_validated":
        return "coordinates_not_supported"
    if len(coordinates.get("display_records", [])) > 4096:
        return "record_collection_exceeds_display_limit"
    if (not isinstance(geometry, dict) or geometry.get("status") != "not_validated"
            or geometry.get("reason") != "same_jpeg_unvalidated_af_area_hypothesis"
            or geometry.get("coordinate_frame") != DISPLAY_FRAME
            or geometry.get("center_drawn") is not False
            or geometry.get("box_drawn") is not False):
        return "prepared_geometry_unavailable"
    if profile == "canon_eos_r3_afinfo2_v1":
        from .af_markers import canon_records
        records = canon_records(metadata, actual_orientation)
        if not records or geometry.get("records") != records:
            return "record_collection_invalid"
        singleton = records[0] if len(records) == 1 else {}
        if (geometry.get("center_xy_hypothesis") != singleton.get("center_xy_hypothesis")
                or geometry.get("bbox_xywh_hypothesis") != singleton.get("bbox_xywh_hypothesis")):
            return "record_collection_singleton_mismatch"
        return None
    center = _numbers(geometry.get("center_xy_hypothesis"), 2)
    if (center is None or any(not 0 <= x <= 1 for x in center)
            or center != _numbers(coordinates.get("oriented_center_xy_hypothesis"), 2)):
        return "invalid_center"
    supplied_box = geometry.get("bbox_xywh_hypothesis")
    if supplied_box is None:
        if (coordinates.get("oriented_bbox_xywh_hypothesis") is not None
                or coordinates.get("area_size", {}).get("reason") != "missing"):
            return "invalid_area"
    else:
        box = _numbers(supplied_box, 4)
        if box is None or box != _numbers(coordinates.get("oriented_bbox_xywh_hypothesis"), 4):
            return "invalid_area"
        x, y, w, h = box
        if (x < 0 or y < 0 or w <= 0 or h <= 0
                or x+w > 1 or y+h > 1
                or not math.isclose(x+w/2, center[0], abs_tol=1e-12)
                or not math.isclose(y+h/2, center[1], abs_tol=1e-12)):
            return "invalid_area"
    return None


def _bind_prepared(path: str, image: Any, report: Any, *, max_edge: int = 768) -> _PreparedBundle:
    """Worker-only trust/freshness check; transfers image ownership on success."""
    try:
        if not isinstance(report, dict) or report.get("schema") != af_diagnostic.SCHEMA_VERSION:
            raise ValueError("invalid_prepared_report")
        metadata, binding, preview = (deepcopy(report.get(key)) for key in ("metadata", "binding", "preview"))
        if not isinstance(metadata, dict) or not isinstance(binding, dict):
            raise ValueError("invalid_prepared_report")
        before = binding.get("before")
        source_key = _source_key(before)
        canonical = Path(path).expanduser().resolve(strict=True)
        if (source_key is None or source_key[0] != str(canonical)
                or canonical.suffix.lower() not in _JPEG_SUFFIXES
                or binding.get("source_unchanged") is not True
                or source_key != _source_key(binding.get("after"))):
            raise ValueError("source_changed_or_unbound")
        if af_metadata._stat_identity(canonical.stat()) != before["stat_identity"]:
            raise ValueError("source_changed_after_prepare")
        if image is None and metadata.get("status") == "error":
            reason = str(metadata.get("reason") or "jpeg_prepare_error")
            return _PreparedBundle(metadata=metadata, clean_image=None, request_path=path,
                                   provenance={"source": deepcopy(before), "parser": deepcopy(metadata.get("parser")),
                                               "binding": binding, "preview": preview},
                                   geometry=deepcopy(report.get("geometry")), status="error", reason=reason,
                                   status_text=_status_text(metadata, reason))
        decode_before = preview.get("decode_stat_before") if isinstance(preview, Mapping) else None
        decode_after = preview.get("decode_stat_after") if isinstance(preview, Mapping) else None
        if (not isinstance(image, Image.Image) or not isinstance(preview, dict)
                or image.mode != "RGB" or _size(preview.get("raster_size")) != image.size
                or max(image.size) > max_edge
                or preview.get("pixel_mode") != "RGB" or preview.get("crop") != "full_frame"
                or preview.get("decoder", {}).get("format") not in {"JPEG", "MPO"}
                or type(preview.get("decoder", {}).get("primary_frame_index")) is not int
                or preview["decoder"]["primary_frame_index"] != 0
                or preview.get("decoder", {}).get("encoded_snapshot") is not True
                or preview.get("annotation_applied") is not False
                or preview.get("exif_transposed") is not True
                or type(preview.get("exif_transpose_count")) is not int
                or preview["exif_transpose_count"] != 1
                or preview.get("decoded_bytes_sha256") != before["sha256"]
                or _size(preview.get("display_frame_size")) is None
                or _size(preview.get("decoded_source_size")) is None
                or _stat_key(decode_before) is None or _stat_key(decode_after) is None
                or not af_metadata._path_matches_open_handle(before["stat_identity"], decode_before)
                or decode_after != decode_before):
            raise ValueError("preview_source_binding_mismatch")
        frame_w, frame_h = _size(preview["display_frame_size"])
        raster_w, raster_h = image.size
        if (raster_w > frame_w or raster_h > frame_h
                or min(abs(raster_w-raster_h*frame_w/frame_h),
                       abs(raster_h-raster_w*frame_h/frame_w)) > 1.0):
            raise ValueError("preview_source_binding_mismatch")
        pixels_sha = hashlib.sha256(image.tobytes()).hexdigest()
        if pixels_sha != preview.get("clean_pixels_sha256"):
            raise ValueError("preview_pixels_mismatch")
        parser = deepcopy(metadata.get("parser"))
        parser_key = _parser_key(parser)
        # A parser is optional. Missing/failed parsing may still return a clean
        # source-bound JPEG; it cannot acquire geometry or a pixel proof.
        if parser_key is not None:
            if af_metadata._stat_identity(Path(parser["path"]).stat()) != parser["stat_identity"]:
                raise ValueError("exiftool_changed_after_prepare")
        provenance = {"source": deepcopy(before), "parser": parser,
                      "binding": binding, "preview": preview}
        geometry = deepcopy(report.get("geometry"))
        reason = _geometry_reason(metadata, provenance, geometry)
        bundle = _PreparedBundle(metadata=metadata, clean_image=image, provenance=provenance, request_path=path,
                                 geometry=geometry, status="diagnostic" if reason is None else "unavailable",
                                 reason=reason or "same_jpeg_unvalidated_af_area_hypothesis")
        bundle["status_text"] = _status_text(metadata, bundle["reason"])
        if reason is None:
            if (report.get("status") != "not_validated"
                    or report.get("automatic_decisions_enabled") is not False
                    or report.get("usable_for_subject_selection") is not False
                    or report.get("usable_for_keep_reject") is not False):
                raise ValueError("invalid_prepared_report")
            center = _numbers(geometry["center_xy_hypothesis"], 2)
            box = _numbers(geometry.get("bbox_xywh_hypothesis"), 4)
            bundle._pixel_binding = _PixelBinding(image, image.size, pixels_sha,
                                                   _fingerprint(bundle), center, box, markers=_sealed_markers(geometry))
        return bundle
    except Exception:
        _close_image(image)
        raise


def project_jpeg_diagnostic(bundle: Mapping[str, Any] | None,
                            image_rect: tuple[float, float, float, float]) -> JpegDiagnosticGeometry:
    """Project only a worker-bound diagnostic onto its actual contained raster.

    No filesystem access, EXIF transform, source lookup or dimensional guessing
    is performed here. Raster hashing is bounded by the loader's preview edge
    (768 by default), and rejects even an in-place edit of a same-size image.
    ``image_rect`` is the actual full-image rectangle, never the enclosing cell.
    The returned clip rectangle lets the UI clip cross arms at image boundaries.
    """
    if not isinstance(bundle, _PreparedBundle) or bundle._pixel_binding is None:
        return _hidden("bound_diagnostic_unavailable")
    proof = bundle._pixel_binding
    image = bundle.get("clean_image")
    try:
        if (image is not proof.image or image.mode != "RGB" or image.size != proof.size
                or _fingerprint(bundle) != proof.evidence
                or hashlib.sha256(image.tobytes()).hexdigest() != proof.digest):
            return _hidden("preview_binding_mismatch")
    except (AttributeError, OSError, TypeError, ValueError, OverflowError):
        return _hidden("preview_binding_mismatch")
    rect = _numbers(image_rect, 4)
    if rect is None:
        return _hidden("invalid_image_rectangle")
    left, top, right, bottom = rect
    width, height = right-left, bottom-top
    if not all(math.isfinite(x) and x > 0 for x in (width, height)):
        return _hidden("invalid_image_rectangle")
    frame_w, frame_h = proof.size
    # Permit integer contain rounding, but not stretch/crop: at least one axis
    # must agree with aspect-preserving scaling to within one display pixel.
    if min(abs(width-height*frame_w/frame_h), abs(height-width*frame_h/frame_w)) > 1.0:
        return _hidden("display_aspect_mismatch")
    def project(center, box, role=None, index=None):
        center = (left+center[0]*width, top+center[1]*height)
        if box is not None:
            x, y, w, h = box
            box = (left+x*width, top+y*height, left+(x+w)*width, top+(y+h)*height)
        if (not all(math.isfinite(x) for x in (*center, *(box or ())))
                or (box is not None and (box[2] <= box[0] or box[3] <= box[1]))):
            return None
        return JpegDiagnosticGeometry("diagnostic", proof.reason, center, box, rect, role=role, index=index)
    if proof.markers:
        markers = tuple(project(m.center, m.box, m.role, m.index) for m in proof.markers)
        if any(m is None for m in markers):
            return _hidden("nonfinite_projection")
        singleton = markers[0] if len(markers) == 1 else None
        return JpegDiagnosticGeometry("diagnostic", proof.reason,
            singleton.center if singleton else None, singleton.box if singleton else None,
            rect, markers)
    if proof.center is None:
        return _hidden("bound_diagnostic_unavailable")
    return project(proof.center, proof.box) or _hidden("nonfinite_projection")


class AfJpegPreviewLoader:
    """One running preparation plus one replaceable pending source request."""

    def __init__(self, *, exiftool_path: str | os.PathLike[str] | None = None,
                 timeout: float = 10.0, max_edge: int = 768,
                 preparer: Callable[..., tuple[Image.Image | None, dict[str, Any]]] | None = None) -> None:
        if isinstance(max_edge, bool) or not isinstance(max_edge, int) or not 64 <= max_edge <= 4096:
            raise ValueError("max_edge must be an integer from 64 to 4096")
        self._preparer = preparer
        self._exiftool_path, self._timeout, self._max_edge = exiftool_path, timeout, max_edge
        self._condition = threading.Condition()
        self._generation = 0
        self._pending: tuple[int, str] | None = None
        self._completed: tuple[int, _PreparedBundle] | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._work, name="af-jpeg-preview", daemon=True)
        self._thread.start()

    def request(self, path: str | os.PathLike[str]) -> int:
        """Latest request wins; no stat/resolve/decode/subprocess runs here."""
        path_string = os.fspath(path)
        if not isinstance(path_string, str):
            raise TypeError("AF JPEG source path must be text")
        with self._condition:
            if self._closed:
                raise RuntimeError("AF JPEG loader is closed")
            self._generation += 1
            token = self._generation
            self._pending = token, path_string
            old, self._completed = self._completed, None
            self._condition.notify()
        if old is not None:
            _close_bundle(old[1])
        return token

    def pop_completed(self) -> list[tuple[int, dict[str, Any]]]:
        """Transfer ownership of at most one current bundle/image to the UI."""
        with self._condition:
            completed, self._completed = self._completed, None
            valid = not self._closed and completed is not None and completed[0] == self._generation
        if valid:
            return [completed]
        if completed is not None:
            _close_bundle(completed[1])
        return []

    def invalidate(self) -> None:
        with self._condition:
            self._generation += 1
            self._pending = None
            old, self._completed = self._completed, None
        if old is not None:
            _close_bundle(old[1])

    def close(self) -> None:
        """Nonblocking and idempotent; a running preparation closes its result."""
        with self._condition:
            self._closed = True
            self._generation += 1
            self._pending = None
            old, self._completed = self._completed, None
            self._condition.notify()
        if old is not None:
            _close_bundle(old[1])

    def _work(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                generation, path = self._pending
                self._pending = None
            image = None
            try:
                suffix = Path(path).suffix.lower()
                if suffix in {'.cr3', '.nef', '.arw', '.rw2'}:
                    from .af_raw_source import load_raw_embedded_preview
                    from .af_raw_preview import bind_raw_preview
                    image, report = load_raw_embedded_preview(path, exiftool_path=self._exiftool_path,
                                                              timeout=self._timeout, max_edge=self._max_edge)
                    owned_image, image = image, None
                    bundle = bind_raw_preview(path, owned_image, report, max_edge=self._max_edge)
                elif suffix not in _JPEG_SUFFIXES:
                    bundle = _error_bundle(path, "unsupported_preview_source")
                else:
                    preparer = self._preparer or af_diagnostic.prepare_af_diagnostic
                    image, report = preparer(path, exiftool_path=self._exiftool_path,
                                             timeout=self._timeout, max_edge=self._max_edge)
                    owned_image, image = image, None
                    bundle = _bind_prepared(path, owned_image, report, max_edge=self._max_edge)
            except Exception as exc:
                _close_image(image)
                reason = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                    "invalid_prepared_report", "source_changed_or_unbound", "source_changed_after_prepare",
                    "preview_source_binding_mismatch", "preview_pixels_mismatch", "exiftool_changed_after_prepare",
                    "invalid_raw_prepared_report", "raw_source_changed_or_unbound", "raw_snapshot_metadata_unbound",
                    "raw_parser_changed_or_unbound", "raw_automatic_decisions_not_disabled", "raw_extraction_profile_unbound",
                    "raw_diagnostics_inconsistent",
                    "raw_preview_orientation_or_frame_unbound", "raw_preview_pixels_or_frame_unbound",
                    "raw_preview_pixels_changed"
                } else ("raw_preview_prepare_error" if Path(path).suffix.lower() in {".cr3", ".nef", ".arw", ".rw2"} else "jpeg_prepare_error")
                bundle = _error_bundle(path, reason)
            with self._condition:
                accepted = not self._closed and generation == self._generation
                old = self._completed if accepted else None
                if accepted:
                    self._completed = generation, bundle
            if old is not None:
                _close_bundle(old[1])
            if not accepted:
                _close_bundle(bundle)
