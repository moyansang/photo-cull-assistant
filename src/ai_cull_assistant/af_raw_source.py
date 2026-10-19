"""Read-only, source-bound extraction of four profiled camera JPEG previews.

This is a viewing capability, not an AF geometry validator or a RAW decoder.
Only the selected RAW is read. No sibling image, user config, guessed tag,
thumbnail fallback, or metadata-writing command is used. Temporary RAW paths
in the returned metadata remain truthful historical snapshot labels.

The existing metadata adapter bounds its returned JSON after subprocess
capture. The additional probe/extraction processes here enforce streaming
stdout/stderr limits. All subprocesses have finite, per-process deadlines.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import tempfile
import threading
from typing import Any
import warnings

from PIL import Image

from . import af_metadata


SCHEMA_VERSION = "af-raw-source-v1"
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_JPEG_BYTES = 64 * 1024 * 1024
MAX_JPEG_PIXELS = 64_000_000
MAX_EDGE = 768
_MAX_JSON_BYTES = 1024 * 1024
_MAX_STDERR_BYTES = 64 * 1024
_CHUNK = 64 * 1024


@dataclass(frozen=True)
class _Profile:
    name: str
    suffix: str
    makes: tuple[str, ...]
    model: str
    group: str
    tag: str

    @property
    def qualified_tag(self) -> str:
        return self.group + ":" + self.tag


_PROFILES = (
    _Profile("canon_eos_r3_track1_jpgfromraw_v1", ".cr3", ("canon",),
             "Canon EOS R3", "Track1", "JpgFromRaw"),
    _Profile("panasonic_dc_s5m2_ifd0_jpgfromraw2_v1", ".rw2",
             ("panasonic", "panasonic corporation"), "DC-S5M2", "IFD0", "JpgFromRaw2"),
    _Profile("nikon_z5_2_subifd_jpgfromraw_v1", ".nef", ("nikon corporation",),
             "NIKON Z5_2", "SubIFD", "JpgFromRaw"),
    _Profile("sony_ilce_6600_ifd0_previewimage_v1", ".arw", ("sony",),
             "ILCE-6600", "IFD0", "PreviewImage"),
)


class _Failure(Exception):
    def __init__(self, reason: str, status: str = "error"):
        super().__init__(reason)
        self.reason, self.status = reason, status


def _identity(path: Path, limit: int = MAX_SOURCE_BYTES) -> dict[str, Any]:
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise _Failure("source_not_regular_file")
    if before.st_size > limit:
        raise _Failure("source_too_large")
    expected = af_metadata._stat_identity(before)
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        opened = af_metadata._stat_identity(os.fstat(stream.fileno()))
        if not af_metadata._path_matches_open_handle(expected, opened):
            raise _Failure("source_changed_during_read")
        while chunk := stream.read(min(_CHUNK, limit - count + 1)):
            count += len(chunk)
            if count > limit:
                raise _Failure("source_too_large")
            digest.update(chunk)
        if af_metadata._stat_identity(os.fstat(stream.fileno())) != opened:
            raise _Failure("source_changed_during_read")
    if af_metadata._stat_identity(path.stat()) != expected:
        raise _Failure("source_changed_during_read")
    return {"path": str(path), "sha256": digest.hexdigest(), "stat_identity": expected}


def _make_snapshot(source: Path, target: Path, expected: dict[str, Any]) -> dict[str, Any]:
    digest, count = hashlib.sha256(), 0
    with source.open("rb") as origin, target.open("xb") as snapshot:
        opened = af_metadata._stat_identity(os.fstat(origin.fileno()))
        if not af_metadata._path_matches_open_handle(expected["stat_identity"], opened):
            raise _Failure("source_changed_during_snapshot")
        while chunk := origin.read(min(_CHUNK, MAX_SOURCE_BYTES - count + 1)):
            count += len(chunk)
            if count > MAX_SOURCE_BYTES:
                raise _Failure("source_too_large")
            snapshot.write(chunk)
            digest.update(chunk)
        if af_metadata._stat_identity(os.fstat(origin.fileno())) != opened:
            raise _Failure("source_changed_during_snapshot")
    target.chmod(stat.S_IRUSR)
    if (af_metadata._stat_identity(source.stat()) != expected["stat_identity"]
            or digest.hexdigest() != expected["sha256"]):
        raise _Failure("source_changed_during_snapshot")
    identity = _identity(target)
    if identity["sha256"] != expected["sha256"]:
        raise _Failure("snapshot_source_mismatch")
    return identity


def _fixed_environment() -> dict[str, str]:
    # A minimal environment also disables Perl, dynamic-loader and ExifTool
    # startup injection. PATH is needed for the official Perl-script shebang.
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    for output_key in ("SystemRoot", "WINDIR"):
        value = next((value for key, value in os.environ.items()
                      if key.upper() == output_key.upper()), None)
        if value is not None:
            environment[output_key] = value
    return environment


def _run_bounded(tool: Path, arguments: tuple[str, ...], timeout: float,
                 stdout_limit: int) -> subprocess.CompletedProcess:
    """Bound both pipes while draining them, including on process failure."""
    argv = [str(tool), *arguments]
    process = subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=_fixed_environment(), cwd=str(tool.parent))
    output: list[bytes] = []
    error: list[bytes] = []
    overflow = threading.Event()
    read_failed = threading.Event()

    def drain(pipe: Any, chunks: list[bytes], limit: int) -> None:
        count = 0
        try:
            while chunk := pipe.read(_CHUNK):
                remaining = max(0, limit - count)
                chunks.append(chunk[:remaining])
                count += len(chunk)
                if count > limit:
                    overflow.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
        except (OSError, ValueError):
            read_failed.set()
        finally:
            pipe.close()

    threads = [threading.Thread(target=drain, args=(process.stdout, output, stdout_limit), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, error, _MAX_STDERR_BYTES), daemon=True)]
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=timeout)
    except BaseException:
        process.kill()
        process.wait()
        for thread in threads:
            thread.join(timeout=1.0)
        raise
    for thread in threads:
        thread.join(timeout=1.0)
    if any(thread.is_alive() for thread in threads):
        raise _Failure("exiftool_pipe_timeout")
    if overflow.is_set():
        raise _Failure("exiftool_output_too_large")
    if read_failed.is_set():
        raise _Failure("exiftool_pipe_error")
    return subprocess.CompletedProcess(argv, process.returncode, b"".join(output), b"".join(error))


def _profile(raw: dict[str, Any], suffix: str) -> _Profile:
    make, make_issue = af_metadata._unique_tag(raw, "Make", {"IFD0", "PanasonicRaw"},
                                             identical_identity_ok=True)
    model, model_issue = af_metadata._unique_tag(raw, "Model", {"IFD0", "PanasonicRaw"},
                                               identical_identity_ok=True)
    if make_issue or model_issue or not isinstance(make, str) or not isinstance(model, str):
        raise _Failure("camera_identity_missing_or_ambiguous", "unsupported")
    for profile in _PROFILES:
        if suffix == profile.suffix and make.strip().casefold() in profile.makes and model == profile.model:
            return profile
    raise _Failure("unsupported_camera_or_raw_extension", "unsupported")


def _raw_orientation(raw: dict[str, Any]) -> dict[str, Any]:
    # A thumbnail's independent direction is not the main RAW's direction.
    main_fields = {key: value for key, value in raw.items()
                   if key.rsplit(":", 1)[-1] == "Orientation"
                   and any(group in {"IFD0", "PanasonicRaw"} for group in key.split(":")[:-1])}
    return af_metadata._orientation_diagnostic(main_fields)


def _probe(tool: Path, snapshot: Path, profile: _Profile, timeout: float,
           report: dict[str, Any]) -> None:
    arguments = (*af_metadata.READ_OPTIONS, "-" + profile.qualified_tag,
                 "-ExifToolVersion", "-Error", "-Warning", str(snapshot))
    report["probe_argv"] = [str(tool), *arguments]
    process = _run_bounded(tool, arguments, timeout, _MAX_JSON_BYTES)
    report["probe_returncode"] = process.returncode
    report["probe_stderr"] = process.stderr.decode("utf-8", "replace")[:1000]
    if process.returncode or process.stderr:
        raise _Failure("embedded_preview_probe_error")
    payload = json.loads(process.stdout.decode("utf-8", "strict"),
                         object_pairs_hook=af_metadata._strict_object,
                         parse_constant=af_metadata._invalid_constant,
                         parse_float=af_metadata._finite_float)
    af_metadata._check_utf8_json(payload)
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise _Failure("embedded_preview_probe_invalid")
    raw = payload[0]
    if raw.get("SourceFile") != str(snapshot):
        raise _Failure("embedded_preview_probe_source_mismatch")
    version, issue = af_metadata._unique_tag(raw, "ExifToolVersion", {"ExifTool"})
    expected_version = report["parser"]["version"]
    if issue or str(version).rstrip("0").rstrip(".") != expected_version.rstrip("0").rstrip("."):
        raise _Failure("embedded_preview_probe_version_mismatch")
    if any(key.rsplit(":", 1)[-1] in {"Error", "Warning"} for key in raw):
        raise _Failure("embedded_preview_probe_warning_or_error")
    candidates = {key: value for key, value in raw.items()
                  if key.rsplit(":", 1)[-1] == profile.tag
                  and profile.group in key.split(":")[:-1]}
    report["tag_instances"] = candidates
    if not candidates:
        raise _Failure("embedded_preview_missing", "unsupported")
    if len(candidates) != 1:
        raise _Failure("embedded_preview_ambiguous_instances")
    value = next(iter(candidates.values()))
    match = re.fullmatch(r"\(Binary data ([0-9]+) bytes, use -b option to extract\)", value) if isinstance(value, str) else None
    if not match:
        raise _Failure("embedded_preview_not_binary")
    byte_count = int(match.group(1))
    if not 0 < byte_count <= MAX_JPEG_BYTES:
        raise _Failure("embedded_preview_too_large" if byte_count else "embedded_preview_empty")
    report["declared_jpeg_bytes"] = byte_count


def _tiff_orientation(exif: bytes) -> list[int]:
    """Inspect IFD0 directly: Pillow otherwise collapses repeated tag entries."""
    if len(exif) < 8 or exif[:2] not in (b"II", b"MM"):
        raise _Failure("embedded_exif_invalid")
    endian = "<" if exif[:2] == b"II" else ">"
    if struct.unpack_from(endian + "H", exif, 2)[0] != 42:
        raise _Failure("embedded_exif_invalid")
    offset = struct.unpack_from(endian + "I", exif, 4)[0]
    if offset < 8 or offset + 2 > len(exif):
        raise _Failure("embedded_exif_invalid")
    count = struct.unpack_from(endian + "H", exif, offset)[0]
    if offset + 2 + 12 * count + 4 > len(exif):
        raise _Failure("embedded_exif_invalid")
    values = []
    for index in range(count):
        entry = offset + 2 + 12 * index
        tag, kind, length = struct.unpack_from(endian + "HHI", exif, entry)
        if tag == 274:
            if kind != 3 or length != 1:
                raise _Failure("embedded_orientation_invalid")
            value = struct.unpack_from(endian + "H", exif, entry + 8)[0]
            if value not in range(1, 9):
                raise _Failure("embedded_orientation_invalid")
            values.append(value)
    return values


def _jpeg_orientation(encoded: bytes, *, allow_panasonic_trailer: bool = False,
                      details: dict[str, Any] | None = None) -> int | None:
    """Require one complete primary JPEG, preserving duplicate EXIF evidence.

    The measured DC-S5M2 JpgFromRaw2 field includes 272 bytes after EOI.
    ExifTool's PanasonicRaw 0x127 DataTag/ValidateImage returns that field intact;
    it does not define the trailer's semantics. This exact viewing profile may
    retain at most 512 opaque trailer bytes, never another JPEG SOI. Both the
    complete extracted field and its primary/trailer pieces remain hashed.
    """
    if not encoded.startswith(b"\xff\xd8"):
        raise _Failure("embedded_preview_not_jpeg")
    position, in_scan, exif_count = 2, False, 0
    orientation_values: list[int] = []
    while position < len(encoded):
        if in_scan:
            position = encoded.find(b"\xff", position)
            if position < 0:
                raise _Failure("embedded_jpeg_truncated")
        if encoded[position] != 0xff:
            raise _Failure("embedded_jpeg_invalid")
        while position < len(encoded) and encoded[position] == 0xff:
            position += 1
        if position >= len(encoded):
            raise _Failure("embedded_jpeg_truncated")
        marker = encoded[position]
        position += 1
        if in_scan and (marker == 0 or 0xd0 <= marker <= 0xd7):
            continue
        in_scan = False
        if marker == 0xd9:
            trailer = encoded[position:]
            if trailer and (not allow_panasonic_trailer or len(trailer) > 512
                            or b"\xff\xd8" in trailer):
                raise _Failure("embedded_jpeg_trailing_or_multiple_images")
            if len(orientation_values) > 1 or exif_count > 1:
                raise _Failure("embedded_orientation_ambiguous_instances")
            if details is not None:
                details.update(primary_jpeg_bytes=position,
                               primary_jpeg_sha256=hashlib.sha256(encoded[:position]).hexdigest(),
                               opaque_trailer_bytes=len(trailer),
                               opaque_trailer_sha256=hashlib.sha256(trailer).hexdigest(),
                               trailer_policy=("profiled_panasonic_max_512_no_second_jpeg_soi"
                                               if trailer else "no_trailer"),
                               trailer_semantics="unknown" if trailer else "absent")
            return orientation_values[0] if orientation_values else None
        if marker in (0, 0xd8) or 0xd0 <= marker <= 0xd7:
            raise _Failure("embedded_jpeg_invalid")
        if marker == 1:  # TEM is the only other marker without a payload.
            continue
        if position + 2 > len(encoded):
            raise _Failure("embedded_jpeg_truncated")
        length = int.from_bytes(encoded[position:position+2], "big")
        if length < 2 or position + length > len(encoded):
            raise _Failure("embedded_jpeg_truncated")
        payload = encoded[position+2:position+length]
        if marker == 0xe1 and payload.startswith(b"Exif\x00\x00"):
            exif_count += 1
            orientation_values.extend(_tiff_orientation(payload[6:]))
        position += length
        in_scan = marker == 0xda
    raise _Failure("embedded_jpeg_truncated")


def _decode(encoded: bytes, raw_orientation: int, max_edge: int,
            report: dict[str, Any]) -> Image.Image:
    embedded = _jpeg_orientation(
        encoded,
        allow_panasonic_trailer=(report["extraction"]["profile"] == "panasonic_dc_s5m2_ifd0_jpgfromraw2_v1"),
        details=report["extraction"],
    )
    report["orientation"].update(embedded_orientation=embedded)
    if embedded is not None and embedded != raw_orientation:
        raise _Failure("unsafe_embedded_raw_orientation_conflict")
    orientation_source = "raw_main_agrees_with_embedded_ifd0" if embedded is not None else "raw_main_only"
    report["orientation"].update(effective_orientation=raw_orientation, source=orientation_source,
                                 reason="validated_exif_direction_only_not_af_geometry")
    methods = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
               4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
               6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
               8: Image.Transpose.ROTATE_90}
    with warnings.catch_warnings(), io.BytesIO(encoded) as stream:
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(stream) as original:
            if original.format != "JPEG" or getattr(original, "n_frames", 1) != 1:
                raise _Failure("embedded_preview_not_single_jpeg")
            source_size = original.size
            if min(source_size) <= 0 or source_size[0] * source_size[1] > MAX_JPEG_PIXELS:
                raise _Failure("embedded_preview_pixel_limit")
            original.load()
            with original.convert("RGB") as rgb:
                transformed = rgb.transpose(methods[raw_orientation]) if raw_orientation in methods else rgb.copy()
                try:
                    frame_size = transformed.size
                    transformed.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                    clean = Image.new("RGB", transformed.size)
                    clean.paste(transformed)
                finally:
                    transformed.close()
    digest = hashlib.sha256(encoded).hexdigest()
    report["preview"] = {
        "decoded_source_size": list(source_size), "display_frame_size": list(frame_size),
        "raster_size": list(clean.size), "clean_pixels_sha256": hashlib.sha256(clean.tobytes()).hexdigest(),
        "decoded_bytes_sha256": digest, "effective_orientation": raw_orientation,
        "exif_orientation": raw_orientation, "embedded_orientation": embedded,
        "orientation_source": orientation_source, "exif_transposed": True, "exif_transpose_count": 1,
        "crop": "full_frame", "annotation_applied": False, "pixel_mode": "RGB",
        "resolution_provenance": "camera_rendered_embedded_jpeg_not_full_raw_decode",
        "decoder": {"name": "Pillow", "version": Image.__version__, "format": "JPEG",
                    "primary_frame_index": 0, "container_frame_count": 1, "encoded_snapshot": True},
    }
    return clean


def _empty_report(path: str) -> dict[str, Any]:
    return {"schema": SCHEMA_VERSION, "status": "error", "reason": None,
            "source": {"path": path, "sha256": None, "stat_identity": None, "unchanged_during_read": False},
            "snapshot": {"path": None, "sha256": None, "stat_identity": None,
                         "removed": False, "kind": "private_read_only_copy"},
            "binding": {"source_unchanged": False, "snapshot_matches_source": False,
                        "snapshot_unchanged": False, "metadata_matches_snapshot": False,
                        "parser_unchanged": False}, "metadata": None,
            "extraction": {"profile": None, "group": None, "tag": None, "qualified_tag": None,
                           "parser": {"name": "ExifTool", "path": None, "version": None,
                                      "stat_identity": None, "sha256": None, "unchanged_during_read": False},
                           "encoded_jpeg_sha256": None, "encoded_jpeg_bytes": None},
            "preview": None,
            "orientation": {"raw_main_diagnostic": None, "raw_orientation": None,
                            "embedded_orientation": None, "effective_orientation": None,
                            "source": None, "reason": None},
            "viewable_preview": False, "af_geometry_supported": False,
            "usable_for_subject_selection": False, "usable_for_keep_reject": False, "warnings": []}


def _load_raw_embedded_frame(path: str | os.PathLike[str], *,
                              exiftool_path: str | os.PathLike[str] | None = None,
                              timeout: float = 10.0, max_edge: int = MAX_EDGE, edge_limit: int = MAX_EDGE
                              ) -> tuple[Image.Image | None, dict[str, Any]]:
    """Return a clean, bounded camera preview and immutable-source evidence.

    A successful preview says nothing about AF-to-preview geometry. The caller
    owns/should close the returned RGB image. No persistent file is produced.
    ``timeout`` is a per-process timeout (maximum 60 seconds); source hashing
    and JPEG decoding instead have strict byte/pixel bounds.
    """
    report = _empty_report(os.fspath(path))
    try:
        valid_timeout = (not isinstance(timeout, bool) and isinstance(timeout, (int, float))
                         and math.isfinite(timeout) and 0 < timeout <= 60)
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        report["reason"] = "invalid_timeout"
        return None, report
    if type(max_edge) is not int or not 1 <= max_edge <= edge_limit:
        report["reason"] = "invalid_max_edge"
        return None, report
    image = None
    source = tool = snapshot = temporary = None
    source_before = parser_before = snapshot_before = None
    stage = "source"
    try:
        source = Path(path).expanduser().resolve(strict=True)
        source_before = _identity(source)
        report["source"].update(source_before)
        report["binding"]["before"] = source_before
        if source.suffix.lower() not in {profile.suffix for profile in _PROFILES}:
            raise _Failure("unsupported_raw_extension", "unsupported")
        stage = "tool"
        tool = af_metadata._resolve_tool(exiftool_path)
        parser_before = _identity(tool)
        report["extraction"]["parser"].update(parser_before)
        stage = "snapshot"
        temporary = tempfile.TemporaryDirectory(prefix="aicull-raw-")
        snapshot = Path(temporary.name) / ("source" + source.suffix.lower())
        report["snapshot"]["path"] = str(snapshot)
        snapshot_before = _make_snapshot(source, snapshot, source_before)
        report["snapshot"].update(snapshot_before)
        report["binding"]["snapshot_matches_source"] = True
        stage = "metadata"
        metadata = af_metadata.read_af_metadata(snapshot, exiftool_path=tool, timeout=timeout)
        report["metadata"] = metadata
        metadata_source = metadata.get("source", {})
        bound = (all(metadata_source.get(key) == value for key, value in snapshot_before.items())
                 and metadata_source.get("unchanged_during_read") is True
                 and metadata.get("raw_tags", {}).get("SourceFile") == str(snapshot))
        report["binding"]["metadata_matches_snapshot"] = bound
        if metadata.get("status") == "error":
            raise _Failure("metadata_" + str(metadata.get("reason") or "read_error"))
        if not bound:
            raise _Failure("metadata_snapshot_binding_mismatch")
        metadata_parser = metadata.get("parser", {})
        if (metadata_parser.get("path") != str(tool)
                or metadata_parser.get("stat_identity") != parser_before["stat_identity"]
                or not isinstance(metadata_parser.get("version"), str)
                or not re.fullmatch(r"\d+\.\d+", metadata_parser["version"])):
            raise _Failure("metadata_parser_binding_mismatch")
        report["extraction"]["parser"]["version"] = metadata_parser["version"]
        raw = metadata["raw_tags"]
        profile = _profile(raw, source.suffix.lower())
        report["extraction"].update(profile=profile.name, group=profile.group,
                                    tag=profile.tag, qualified_tag=profile.qualified_tag)
        direction = _raw_orientation(raw)
        report["orientation"]["raw_main_diagnostic"] = direction
        if direction["status"] != "not_validated":
            report["orientation"]["reason"] = direction["reason"]
            raise _Failure("unsafe_raw_main_orientation_" + str(direction["reason"]))
        raw_orientation = af_metadata._orientation(direction["exif_value"])
        report["orientation"]["raw_orientation"] = raw_orientation
        stage = "probe"
        _probe(tool, snapshot, profile, timeout, report["extraction"])
        stage = "extraction"
        arguments = ("-config", "", "-b", "-a", "-charset", "filename=UTF8",
                     "-" + profile.qualified_tag, str(snapshot))
        report["extraction"]["read_argv"] = [str(tool), *arguments]
        process = _run_bounded(tool, arguments, timeout, MAX_JPEG_BYTES)
        report["extraction"]["returncode"] = process.returncode
        report["extraction"]["stderr"] = process.stderr.decode("utf-8", "replace")[:1000]
        if process.returncode or process.stderr:
            raise _Failure("embedded_preview_extraction_error")
        encoded = process.stdout
        report["extraction"].update(encoded_jpeg_sha256=hashlib.sha256(encoded).hexdigest(),
                                    encoded_jpeg_bytes=len(encoded))
        if len(encoded) > MAX_JPEG_BYTES:
            raise _Failure("embedded_preview_too_large")
        if len(encoded) != report["extraction"]["declared_jpeg_bytes"]:
            raise _Failure("embedded_preview_length_mismatch")
        stage = "decode"
        image = _decode(encoded, raw_orientation, max_edge, report)
        report.update(status="viewable", reason="camera_embedded_jpeg_only_af_geometry_unverified",
                      viewable_preview=True)
    except _Failure as exc:
        report.update(status=exc.status, reason=exc.reason)
    except subprocess.TimeoutExpired:
        report.update(reason="exiftool_" + stage + "_timeout")
    except (OSError, ValueError, RuntimeError, SyntaxError, subprocess.SubprocessError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        reason = {"source": "source_unavailable", "tool": "exiftool_unavailable",
                  "decode": "embedded_jpeg_decode_error"}.get(stage, "raw_preview_" + stage + "_error")
        report.update(reason=reason)
        report["warnings"].append(str(exc)[:1000])
    finally:
        if snapshot_before is not None:
            try:
                report["binding"]["snapshot_unchanged"] = _identity(snapshot) == snapshot_before
            except (OSError, ValueError, _Failure):
                pass
            if not report["binding"]["snapshot_unchanged"]:
                report.update(status="error", reason="snapshot_changed_during_read")
        if temporary is not None:
            try:
                temporary.cleanup()
                report["snapshot"]["removed"] = not Path(temporary.name).exists()
            except OSError as exc:
                report.update(status="error", reason="snapshot_cleanup_failed")
                report["warnings"].append(str(exc)[:1000])
        if source_before is not None:
            try:
                after = _identity(source)
                report["binding"]["after"] = after
                unchanged = after == source_before
            except (OSError, ValueError, _Failure):
                unchanged = False
            report["source"]["unchanged_during_read"] = unchanged
            report["binding"]["source_unchanged"] = unchanged
            if not unchanged:
                report.update(status="error", reason="source_changed_during_read")
        if parser_before is not None:
            try:
                unchanged = _identity(tool) == parser_before
            except (OSError, ValueError, _Failure):
                unchanged = False
            report["binding"]["parser_unchanged"] = unchanged
            report["extraction"]["parser"]["unchanged_during_read"] = unchanged
            if not unchanged:
                report.update(status="error", reason="exiftool_changed_during_read")
    if report["status"] != "viewable":
        report["viewable_preview"] = False
        if image is not None:
            image.close()
        image = None
    return image, report


def load_raw_embedded_preview(path, *, exiftool_path=None, timeout=10.0, max_edge=MAX_EDGE):
    """Existing bounded display API; never changes quality or selection."""
    return _load_raw_embedded_frame(path, exiftool_path=exiftool_path,
                                    timeout=timeout, max_edge=max_edge)


def load_raw_native_frame(path, *, exiftool_path=None, timeout=10.0):
    """Extract a camera-rendered native JPEG, not demosaiced RAW sensor pixels.

    Extraction alone grants no AF geometry or quality authority. Callers must
    validate the existing source/tag/crop/warp binding and require no resizing.
    The 64 MP decode bound remains active; an oversized side is rejected.
    """
    image, report = _load_raw_embedded_frame(path, exiftool_path=exiftool_path,
        timeout=timeout, max_edge=8192, edge_limit=8192)
    if image is not None and list(image.size) != report['preview']['display_frame_size']:
        image.close()
        report.update(status='unsupported', reason='native_embedded_frame_would_resize',
                      viewable_preview=False)
        return None, report
    return image, report
