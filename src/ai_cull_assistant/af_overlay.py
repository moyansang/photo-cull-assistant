"""Fail-closed AF display geometry and a Tk-independent metadata loader.

The current AF adapter never produces validated coordinates. Its diagnostic
``*_hypothesis`` values are deliberately never read here. A future calibrated
adapter may supply the explicit ``validated_display_mapping`` contract below;
that contract must bind the original bytes, parser, and the *actual* displayed
preview, including its crop and orientation. Matching a filename/stem or image
size is not evidence of that relationship.

The loader has one daemon worker, one replaceable pending request and at most
one completion. It performs no filesystem work in ``request``/``pop_completed``
and never calls Tk. There is no cache. ``close`` cannot interrupt an in-flight
hash/ExifTool read, but does not wait for it and prevents late results escaping.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import threading
from typing import Any

from . import af_metadata


DISPLAY_FRAME = "exif_transposed_preview_normalized"
_STAT_KEYS = ("size", "mtime_ns", "ctime_ns", "device", "inode")


@dataclass(frozen=True)
class PreviewFrame:
    """Proof supplied by a trusted preview producer, never inferred by the UI.

    ``source`` contains path, sha256 and stat_identity of the original whose
    bytes produced these pixels. ``preview_id`` identifies this exact preview
    artifact/crop/orientation, not merely its asset or filename. ``frame_size``
    is its full, already EXIF-transposed pixel frame, before display scaling.
    Current group thumbnails do not provide this proof and must pass ``None``.
    """

    source: Mapping[str, Any]
    preview_id: str
    frame_size: tuple[int, int]
    exif_transposed: bool


@dataclass(frozen=True)
class OverlayGeometry:
    """Canvas-space geometry; box is xyxy. Only ``validated`` may be drawn."""

    status: str
    reason: str
    center: tuple[float, float] | None = None
    box: tuple[float, float, float, float] | None = None
    clip_rect: tuple[float, float, float, float] | None = None


def _finite_numbers(value: Any, length: int) -> tuple[float, ...] | None:
    if not isinstance(value, (tuple, list)) or len(value) != length:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    try:
        numbers = tuple(float(item) for item in value)
    except (OverflowError, ValueError):
        return None
    return numbers if all(math.isfinite(item) for item in numbers) else None


def _frame_size(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value):
        return None
    try:
        if not all(math.isfinite(float(item)) for item in value):
            return None
    except OverflowError:
        return None
    return tuple(value)


def _stat_key(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, Mapping):
        return None
    if any(isinstance(value.get(key), bool) or not isinstance(value.get(key), int)
           for key in _STAT_KEYS):
        return None
    if value["size"] < 0:
        return None
    return tuple(value[key] for key in _STAT_KEYS)


def _source_key(value: Any) -> tuple[Any, ...] | None:
    """Compare already canonical paths without filesystem IO on the UI thread."""
    if not isinstance(value, Mapping):
        return None
    path, digest = value.get("path"), value.get("sha256")
    identity = _stat_key(value.get("stat_identity"))
    if (not isinstance(path, str) or not path or not os.path.isabs(path)
            or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or identity is None):
        return None
    return path, digest, identity


def _parser_key(value: Any) -> tuple[Any, ...] | None:
    if not isinstance(value, Mapping):
        return None
    strings = tuple(value.get(key) for key in ("name", "path", "version", "adapter_version"))
    identity = _stat_key(value.get("stat_identity"))
    if any(not isinstance(item, str) or not item for item in strings) or identity is None:
        return None
    if strings[0] != "ExifTool" or not os.path.isabs(strings[1]):
        return None
    return *strings, identity


def build_overlay_geometry(record: Mapping[str, Any] | None,
                           preview: PreviewFrame | None,
                           image_rect: tuple[float, float, float, float]) -> OverlayGeometry:
    """Project a separately validated display-normalized map into letterboxing.

    ``image_rect`` is the *actual* image rectangle (left, top, right, bottom),
    not the enclosing thumbnail cell. No EXIF transform is performed here.

    Forward contract (synthetic tests are not real-camera calibration):
    - record.status and validated_display_mapping.status are ``validated``;
    - mapping.validation_id names the independent calibration/evidence;
    - mapping.coordinate_frame is DISPLAY_FRAME and exif_transposed is True;
    - mapping.source and mapping.parser exactly bind record and preview;
    - mapping.preview_id/frame_size exactly identify preview pixels;
    - mapping.center_xy is normalized interior xy; optional area_size_wh is
      finite positive normalized size wholly inside that frame.

    Absent size yields a cross only. Invalid supplied size rejects the map,
    rather than clipping a rectangle or inventing replacement dimensions.
    """
    reject = lambda status, reason: OverlayGeometry(status, reason)
    if not isinstance(record, Mapping):
        return reject("unavailable", "metadata_unavailable")
    if record.get("status") != "validated":
        return reject("not_validated", "metadata_not_validated")
    mapping = record.get("validated_display_mapping")
    if not isinstance(mapping, Mapping) or mapping.get("status") != "validated":
        return reject("not_validated", "display_mapping_not_validated")
    if not isinstance(mapping.get("validation_id"), str) or not mapping["validation_id"].strip():
        return reject("not_validated", "validation_evidence_missing")
    if not isinstance(preview, PreviewFrame):
        return reject("not_validated", "preview_binding_missing")
    if (preview.exif_transposed is not True or mapping.get("exif_transposed") is not True
            or mapping.get("coordinate_frame") != DISPLAY_FRAME):
        return reject("not_validated", "display_frame_not_validated")
    source = record.get("source")
    source_key = _source_key(source)
    if (source_key is None or source.get("unchanged_during_read") is not True
            or source_key != _source_key(preview.source)
            or source_key != _source_key(mapping.get("source"))):
        return reject("invalid", "source_identity_mismatch")
    parser_key = _parser_key(record.get("parser"))
    if parser_key is None or parser_key != _parser_key(mapping.get("parser")):
        return reject("invalid", "parser_identity_mismatch")
    size = _frame_size(preview.frame_size)
    if (size is None or size != _frame_size(mapping.get("frame_size"))
            or not isinstance(preview.preview_id, str) or not preview.preview_id
            or mapping.get("preview_id") != preview.preview_id):
        return reject("invalid", "preview_identity_mismatch")
    rect = _finite_numbers(image_rect, 4)
    if rect is None or rect[2] <= rect[0] or rect[3] <= rect[1]:
        return reject("invalid", "invalid_image_rectangle")
    left, top, right, bottom = rect
    width, height = right-left, bottom-top
    # A true thumbnail preserves aspect ratio apart from integer pixel rounding.
    # Reject a different crop/frame; permit at most one display pixel rounding.
    if (not math.isfinite(width) or not math.isfinite(height)
            or abs(width / size[0] - height / size[1]) > max(1 / size[0], 1 / size[1])):
        return reject("invalid", "display_aspect_mismatch")
    center = _finite_numbers(mapping.get("center_xy"), 2)
    if center is None or any(not 0 < item < 1 for item in center):
        return reject("invalid", "invalid_center")
    box = None
    area = mapping.get("area_size_wh")
    if area is not None:
        area = _finite_numbers(area, 2)
        if area is None or any(not 0 < item <= 1 for item in area):
            return reject("invalid", "invalid_area_size")
        x, y = center
        area_width, area_height = area
        x0, y0, x1, y1 = (x-area_width/2, y-area_height/2,
                          x+area_width/2, y+area_height/2)
        if x0 < 0 or y0 < 0 or x1 > 1 or y1 > 1:
            return reject("invalid", "area_outside_frame")
        box = (left+x0*width, top+y0*height, left+x1*width, top+y1*height)
    canvas_center = (left+center[0]*width, top+center[1]*height)
    if (not all(math.isfinite(item) for item in (*canvas_center, *(box or ())))
            or (box is not None and (box[2] <= box[0] or box[3] <= box[1]))):
        return reject("invalid", "nonfinite_projection")
    return OverlayGeometry("validated", "validated_display_mapping", canvas_center, box, rect)


def af_status_text(record: Mapping[str, Any] | None) -> str:
    """Short UI-only diagnostic; never claim autofocus hit or select a person."""
    if not isinstance(record, Mapping):
        return "AF：信息不可用"
    status, reason = record.get("status"), str(record.get("reason", ""))
    if status == "not_validated":
        return "AF：坐标未验证，暂不绘制"
    if status == "validated":
        return "AF：映射已验证，仍需匹配预览"
    if status == "unsupported":
        return "AF：暂不支持此相机元数据"
    if status == "missing":
        return "AF：未发现对焦位置信息"
    if reason == "exiftool_unavailable":
        return "AF：未找到 ExifTool（可选）"
    if reason == "exiftool_read_warning":
        return "AF：元数据有警告，暂不绘制"
    if "source_changed" in reason or reason == "source_identity_mismatch":
        return "AF：原件已变化，请重新读取"
    if reason == "source_unavailable":
        return "AF：原件无法读取"
    if "timeout" in reason:
        return "AF：读取超时"
    return "AF：信息不可用"


def _error_record(path: str, reason: str) -> dict[str, Any]:
    # Do not retain coordinates from a stale/failed reader response.
    return {"status": "error", "reason": reason, "source": {"path": path},
            "usable_for_subject_selection": False, "usable_for_keep_reject": False}


def _verify_read_binding(record: Any, path: str) -> dict[str, Any]:
    """Worker-only freshness check, in addition to the adapter's double hash."""
    if not isinstance(record, dict):
        return _error_record(path, "invalid_metadata_record")
    if record.get("status") not in {"validated", "not_validated", "unsupported", "missing"}:
        return record
    source = record.get("source")
    if _source_key(source) is None or source.get("unchanged_during_read") is not True:
        return _error_record(path, "source_identity_mismatch")
    try:
        canonical = Path(path).expanduser().resolve(strict=True)
        if str(canonical) != source["path"]:
            return _error_record(path, "source_identity_mismatch")
        if af_metadata._stat_identity(canonical.stat()) != source["stat_identity"]:
            return _error_record(path, "source_changed_after_read")
        parser = record.get("parser")
        if _parser_key(parser) is None:
            return _error_record(path, "invalid_parser_identity")
        if af_metadata._stat_identity(Path(parser["path"]).stat()) != parser["stat_identity"]:
            return _error_record(path, "exiftool_changed_after_read")
    except (OSError, ValueError, RuntimeError):
        return _error_record(path, "source_or_parser_unavailable_after_read")
    return record


class AfOverlayLoader:
    """Latest-only, bounded metadata reads; UI polls completions on its thread.

    The reader injection is for tests. Production always uses the read-only
    adapter and its fixed arguments. Repeated requests reread source and parser;
    successful and failed results are never cached. Generation invalidation
    handles navigation, toggle-off and close while expensive reads finish.
    """

    def __init__(self, *, exiftool_path: str | os.PathLike[str] | None = None,
                 timeout: float = 10.0,
                 reader: Callable[..., dict[str, Any]] | None = None) -> None:
        self._reader = reader
        self._exiftool_path = exiftool_path
        self._timeout = timeout
        self._condition = threading.Condition()
        self._generation = 0
        self._pending: tuple[int, str] | None = None
        self._completed: tuple[int, dict[str, Any]] | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._work, name="af-metadata", daemon=True)
        self._thread.start()

    def request(self, path: str | os.PathLike[str]) -> int:
        """Queue only the latest path; no resolve/stat/hash/read runs here."""
        path_string = os.fspath(path)
        if not isinstance(path_string, str):
            raise TypeError("AF source path must be text")
        with self._condition:
            if self._closed:
                raise RuntimeError("AF loader is closed")
            self._generation += 1
            self._pending = (self._generation, path_string)
            self._completed = None
            self._condition.notify()
            return self._generation

    def invalidate(self) -> None:
        with self._condition:
            self._generation += 1
            self._pending = None
            self._completed = None

    def pop_completed(self) -> list[tuple[int, dict[str, Any]]]:
        """Return at most one current generation, without callbacks or IO."""
        with self._condition:
            value, self._completed = self._completed, None
            if self._closed or value is None or value[0] != self._generation:
                return []
            return [value]

    def close(self) -> None:
        """Immediately discard pending/late work. Never join a long RAW read."""
        with self._condition:
            self._closed = True
            self._generation += 1
            self._pending = None
            self._completed = None
            self._condition.notify()

    def _work(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                generation, path = self._pending
                self._pending = None
            try:
                reader = self._reader or af_metadata.read_af_metadata
                record = reader(path, exiftool_path=self._exiftool_path, timeout=self._timeout)
                record = _verify_read_binding(record, path)
            except Exception:
                # An optional metadata facility must not kill the worker or UI.
                record = _error_record(path, "metadata_read_error")
            with self._condition:
                if self._closed:
                    return
                if generation == self._generation:
                    self._completed = (generation, record)
