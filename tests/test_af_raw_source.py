"""Synthetic extraction contracts; none of these tests calibrates real AF."""
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from ai_cull_assistant import af_metadata as metadata
from ai_cull_assistant import af_raw_source as raw


def jpeg(size=(160, 96), orientation=None):
    image = Image.new("RGB", size, (30, 40, 50))
    image.paste((240, 10, 20), (0, 0, size[0] // 3, size[1] // 2))
    image.paste((10, 220, 30), (size[0] // 2, size[1] // 2, size[0], size[1]))
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    stream = io.BytesIO()
    image.save(stream, "JPEG", quality=95, subsampling=0, exif=exif)
    image.close()
    return stream.getvalue()


@pytest.fixture
def sample(tmp_path):
    source = tmp_path / "-execute 中文照片.cr3"
    source.write_bytes(b"synthetic RAW input; no real photograph")
    tool = tmp_path / "exiftool"
    tool.write_bytes(b"placeholder, never executed by extraction tests")
    return source, tool


def mocks(monkeypatch, sample, *, encoded=None, direction=1, tags=None,
          probe_tags=None, effect=None, metadata_effect=None):
    source, tool = sample
    encoded = jpeg() if encoded is None else encoded
    calls, snapshot_paths = [], []

    def metadata_run(executable, arguments, timeout):
        if arguments[-1] == "-ver":
            return subprocess.CompletedProcess(arguments, 0, "13.59\n", "")
        snapshot = Path(arguments[-1])
        snapshot_paths.append(snapshot)
        assert snapshot != source.resolve()
        assert snapshot.read_bytes() == source.read_bytes()
        payload = {"SourceFile": str(snapshot), "ExifTool:ExifToolVersion": 13.59,
                   "IFD0:Make": "Canon", "IFD0:Model": "Canon EOS R3",
                   "IFD0:Orientation": direction,
                   "Canon:NumAFPoints": 1}
        if tags:
            payload.update(tags)
        if metadata_effect:
            metadata_effect(snapshot, payload)
        return subprocess.CompletedProcess(arguments, 0, json.dumps([payload]), "")

    def bounded(executable, arguments, timeout, limit):
        calls.append((executable, arguments, timeout, limit))
        snapshot = Path(arguments[-1])
        assert snapshot.is_file() and snapshot != source.resolve()
        assert snapshot.stat().st_mode & 0o222 == 0
        stage = "extract" if "-b" in arguments else "probe"
        if effect:
            altered = effect(stage, snapshot, arguments)
            if altered is not None:
                return altered
        if stage == "extract":
            return subprocess.CompletedProcess(arguments, 0, encoded, b"")
        tag = next(item[1:] for item in arguments if item.startswith(("-Track1:", "-IFD0:", "-SubIFD:")))
        payload = {"SourceFile": str(snapshot), "ExifTool:ExifToolVersion": 13.59,
                   tag: f"(Binary data {len(encoded)} bytes, use -b option to extract)"}
        if probe_tags:
            payload.update(probe_tags)
        return subprocess.CompletedProcess(arguments, 0, json.dumps([payload]).encode(), b"")

    monkeypatch.setattr(metadata, "_run", metadata_run)
    monkeypatch.setattr(raw, "_run_bounded", bounded)
    return calls, snapshot_paths


def load(sample, **kwargs):
    return raw.load_raw_embedded_preview(sample[0], exiftool_path=sample[1], **kwargs)


@pytest.mark.skipif(os.name != "nt", reason="Windows stat/fstat ctime semantics")
def test_windows_identity_accepts_path_handle_ctime_difference(monkeypatch, sample):
    source = sample[0]
    original_fstat = os.fstat

    def windows_handle_ctime(fd):
        value = original_fstat(fd)
        fields = {name: getattr(value, name) for name in
                  ("st_size", "st_mtime_ns", "st_ctime_ns", "st_dev", "st_ino")}
        fields["st_ctime_ns"] += 1_000_000
        return SimpleNamespace(**fields)

    monkeypatch.setattr(raw.os, "fstat", windows_handle_ctime)
    identity = raw._identity(source)
    assert identity["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    target = source.with_name("snapshot.cr3")
    snapshot = raw._make_snapshot(source, target, identity)
    assert snapshot["sha256"] == identity["sha256"]


def test_identity_rejects_handle_change_while_hashing(monkeypatch, sample):
    original_fstat = os.fstat
    calls = 0

    def changing_fstat(fd):
        nonlocal calls
        calls += 1
        value = original_fstat(fd)
        fields = {name: getattr(value, name) for name in
                  ("st_size", "st_mtime_ns", "st_ctime_ns", "st_dev", "st_ino")}
        if calls == 2:
            fields["st_ctime_ns"] += 1
        return SimpleNamespace(**fields)

    monkeypatch.setattr(raw.os, "fstat", changing_fstat)
    with pytest.raises(raw._Failure, match="source_changed_during_read"):
        raw._identity(sample[0])


def test_source_bound_readonly_clean_preview_and_fixed_arguments(monkeypatch, sample):
    source, tool = sample
    before = source.read_bytes(), metadata._stat_identity(source.stat())
    encoded = jpeg()
    calls, snapshots = mocks(monkeypatch, sample, encoded=encoded)
    image, report = load(sample, timeout=2.5)
    assert report["status"] == "viewable"
    assert report["viewable_preview"] is True and report["af_geometry_supported"] is False
    assert report["usable_for_subject_selection"] is False
    assert report["usable_for_keep_reject"] is False
    assert image.mode == "RGB" and not image.info and not image.getexif()
    assert report["source"]["path"] == str(source.resolve())
    assert report["source"]["stat_identity"] == before[1]
    assert report["source"]["sha256"] == hashlib.sha256(before[0]).hexdigest()
    assert report["source"]["unchanged_during_read"] is True
    assert report["snapshot"]["sha256"] == report["source"]["sha256"]
    assert report["snapshot"]["path"] != report["source"]["path"]
    assert report["snapshot"]["removed"] is True
    assert report["metadata"]["source"]["path"] == report["snapshot"]["path"]
    assert report["metadata"]["raw_tags"]["SourceFile"] == report["snapshot"]["path"]
    assert all(not path.exists() and not path.parent.exists() for path in snapshots)
    for key in ("source_unchanged", "snapshot_matches_source", "snapshot_unchanged",
                "metadata_matches_snapshot", "parser_unchanged"):
        assert report["binding"][key] is True
    extraction = report["extraction"]
    assert extraction["profile"] == "canon_eos_r3_track1_jpgfromraw_v1"
    assert extraction["qualified_tag"] == "Track1:JpgFromRaw"
    assert extraction["encoded_jpeg_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert extraction["encoded_jpeg_bytes"] == len(encoded)
    assert extraction["parser"]["path"] == str(tool.resolve())
    assert extraction["parser"]["version"] == "13.59"
    assert extraction["parser"]["unchanged_during_read"] is True
    assert len(calls) == 2
    assert calls[1][1] == ("-config", "", "-b", "-a", "-charset", "filename=UTF8",
                            "-Track1:JpgFromRaw", report["snapshot"]["path"])
    assert calls[0][2] == calls[1][2] == 2.5
    assert calls[0][3] == raw._MAX_JSON_BYTES and calls[1][3] == raw.MAX_JPEG_BYTES
    assert (source.read_bytes(), metadata._stat_identity(source.stat())) == before
    assert sorted(p.name for p in source.parent.iterdir()) == sorted([source.name, tool.name])
    assert report["preview"]["decoded_source_size"] == [160, 96]
    assert report["preview"]["display_frame_size"] == [160, 96]
    assert report["preview"]["clean_pixels_sha256"] == hashlib.sha256(image.tobytes()).hexdigest()
    assert report["preview"]["decoded_bytes_sha256"] == hashlib.sha256(encoded).hexdigest()
    image.close()


@pytest.mark.parametrize("direction", range(1, 9))
@pytest.mark.parametrize("embedded", [False, True])
def test_all_eight_directions_once_with_or_without_embedded_orientation(monkeypatch, sample, direction, embedded):
    encoded = jpeg(orientation=direction if embedded else None)
    mocks(monkeypatch, sample, encoded=encoded, direction=direction)
    image, report = load(sample, max_edge=100)
    assert report["status"] == "viewable"
    methods = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
               4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
               6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
               8: Image.Transpose.ROTATE_90}
    with Image.open(io.BytesIO(encoded)) as original:
        expected = original.convert("RGB")
        if direction in methods:
            expected = expected.transpose(methods[direction])
        expected_frame = list(expected.size)
        expected.thumbnail((100, 100), Image.Resampling.LANCZOS)
        assert image.tobytes() == expected.tobytes()
        expected.close()
    assert report["preview"]["display_frame_size"] == expected_frame
    assert report["preview"]["effective_orientation"] == direction
    assert report["preview"]["exif_transpose_count"] == 1
    assert report["preview"]["embedded_orientation"] == (direction if embedded else None)
    assert report["orientation"]["raw_orientation"] == direction
    assert report["orientation"]["source"] == ("raw_main_agrees_with_embedded_ifd0" if embedded else "raw_main_only")
    image.close()


def test_disagreeing_embedded_direction_is_unsafe(monkeypatch, sample):
    mocks(monkeypatch, sample, encoded=jpeg(orientation=8), direction=6)
    image, report = load(sample)
    assert image is None and report["reason"] == "unsafe_embedded_raw_orientation_conflict"
    assert report["orientation"]["raw_orientation"] == 6
    assert report["orientation"]["embedded_orientation"] == 8
    assert report["snapshot"]["removed"] is True


@pytest.mark.parametrize("direction", [0, 9, -1, 1.5, True, "NaN", "1.00000000000000001", [1]])
def test_invalid_raw_orientation_fails_closed(monkeypatch, sample, direction):
    calls, _ = mocks(monkeypatch, sample, direction=direction)
    image, report = load(sample)
    assert image is None and report["reason"].startswith("unsafe_raw_main_orientation_")
    assert calls == [] and report["snapshot"]["removed"] is True


@pytest.mark.parametrize("duplicate,okay", [(1, True), ("1", True), (2, False), ("x", False), (True, False)])
def test_duplicate_raw_main_direction_requires_valid_consensus(monkeypatch, sample, duplicate, okay):
    mocks(monkeypatch, sample, tags={"IFD0:Copy1:Orientation": duplicate})
    image, report = load(sample)
    assert report["viewable_preview"] is okay
    if okay:
        assert report["orientation"]["raw_main_diagnostic"]["instance_resolution"] == "agreeing_valid_main_frame_instances"
        image.close()
    else:
        assert image is None


def test_thumbnail_direction_does_not_override_raw_main(monkeypatch, sample):
    mocks(monkeypatch, sample, direction=8, tags={"IFD1:Orientation": 1})
    image, report = load(sample)
    assert report["status"] == "viewable"
    assert report["preview"]["effective_orientation"] == 8
    assert report["metadata"]["raw_tags"]["IFD1:Orientation"] == 1
    image.close()


def exif_segment(values):
    entries = b"".join(struct.pack("<HHIHH", 274, 3, 1, value, 0) for value in values)
    exif = b"Exif\x00\x00II" + struct.pack("<HIH", 42, 8, len(values)) + entries + b"\x00" * 4
    return b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif


@pytest.mark.parametrize("values", [[1, 1], [1, 8]])
def test_duplicate_embedded_orientation_not_collapsed_by_pillow(monkeypatch, sample, values):
    encoded = jpeg()
    encoded = encoded[:2] + exif_segment(values) + encoded[2:]
    mocks(monkeypatch, sample, encoded=encoded)
    image, report = load(sample)
    assert image is None and report["reason"] == "embedded_orientation_ambiguous_instances"


def test_multiple_exif_documents_are_ambiguous(monkeypatch, sample):
    encoded = jpeg()
    encoded = encoded[:2] + exif_segment([1]) + exif_segment([1]) + encoded[2:]
    mocks(monkeypatch, sample, encoded=encoded)
    assert load(sample)[1]["reason"] == "embedded_orientation_ambiguous_instances"


@pytest.mark.parametrize("suffix,make,model,qualified", [
    (".cr3", "Canon", "Canon EOS R3", "Track1:JpgFromRaw"),
    (".rw2", "Panasonic", "DC-S5M2", "IFD0:JpgFromRaw2"),
    (".nef", "NIKON CORPORATION", "NIKON Z5_2", "SubIFD:JpgFromRaw"),
    (".arw", "SONY", "ILCE-6600", "IFD0:PreviewImage"),
])
def test_exact_camera_profiles_allow_viewing_without_af_claim(monkeypatch, sample, suffix, make, model, qualified):
    source = sample[0].with_suffix(suffix)
    sample[0].rename(source)
    sample = source, sample[1]
    mocks(monkeypatch, sample, tags={"IFD0:Make": make, "IFD0:Model": model})
    image, report = load(sample)
    assert report["status"] == "viewable"
    assert report["extraction"]["qualified_tag"] == qualified
    assert report["af_geometry_supported"] is False
    image.close()


@pytest.mark.parametrize("tags", [{"IFD0:Model": "Canon EOS R5"}, {"IFD0:Make": "Sony"},
                                  {"IFD0:Copy1:Model": "Canon EOS R5"}, {"IFD0:Model": "EOS R3"}])
def test_unsupported_or_ambiguous_camera_never_guesses_a_tag(monkeypatch, sample, tags):
    calls, _ = mocks(monkeypatch, sample, tags=tags)
    image, report = load(sample)
    assert image is None and report["status"] == "unsupported" and calls == []
    assert report["source"]["unchanged_during_read"] is True


@pytest.mark.parametrize("case", ["multiple", "truncated", "text", "png", "trailing", "bad_exif"])
def test_malformed_or_multiple_jpegs_rejected(monkeypatch, sample, case):
    encoded = jpeg()
    variants = {"multiple": encoded + encoded, "truncated": encoded[:-5], "text": b"not a JPEG",
                "png": b"\x89PNG\r\n\x1a\n", "trailing": encoded + b"extra",
                "bad_exif": encoded[:2] + b"\xff\xe1\x00\x09Exif\x00\x00x" + encoded[2:]}
    mocks(monkeypatch, sample, encoded=variants[case])
    image, report = load(sample)
    assert image is None and report["status"] == "error"
    assert report["snapshot"]["removed"] is True


@pytest.mark.parametrize("trailer,okay", [(b"\x00" * 16 + bytes(range(256)), True),
                                          (b"x" * 512, True), (b"x" * 513, False),
                                          (b"x" * 10 + b"\xff\xd8" + b"x" * 10, False)])
def test_only_panasonic_profile_allows_bounded_opaque_nonjpeg_trailer(monkeypatch, sample, trailer, okay):
    source = sample[0].with_suffix(".rw2")
    sample[0].rename(source)
    sample = source, sample[1]
    primary = jpeg()
    encoded = primary + trailer
    mocks(monkeypatch, sample, encoded=encoded, tags={"IFD0:Make": "Panasonic", "IFD0:Model": "DC-S5M2"})
    image, report = load(sample)
    assert report["viewable_preview"] is okay
    assert report["af_geometry_supported"] is False
    assert report["extraction"]["encoded_jpeg_sha256"] == hashlib.sha256(encoded).hexdigest()
    if okay:
        extraction = report["extraction"]
        assert extraction["primary_jpeg_bytes"] == len(primary)
        assert extraction["primary_jpeg_sha256"] == hashlib.sha256(primary).hexdigest()
        assert extraction["opaque_trailer_bytes"] == len(trailer)
        assert extraction["opaque_trailer_sha256"] == hashlib.sha256(trailer).hexdigest()
        assert extraction["trailer_semantics"] == "unknown"
        assert report["preview"]["decoded_bytes_sha256"] == hashlib.sha256(encoded).hexdigest()
        with Image.open(io.BytesIO(primary)) as expected:
            assert image.tobytes() == expected.convert("RGB").tobytes()
        image.close()
    else:
        assert image is None and report["reason"] == "embedded_jpeg_trailing_or_multiple_images"


@pytest.mark.parametrize("stage", ["probe", "extract"])
@pytest.mark.parametrize("failure", ["timeout", "nonzero", "stderr", "oserror", "overflow"])
def test_extraction_errors_are_controlled_and_snapshot_removed(monkeypatch, sample, stage, failure):
    def effect(current, snapshot, arguments):
        if current != stage:
            return None
        if failure == "timeout":
            raise subprocess.TimeoutExpired(arguments, 0.1)
        if failure == "oserror":
            raise OSError("cannot execute vendor parser")
        if failure == "overflow":
            raise raw._Failure("exiftool_output_too_large")
        return subprocess.CompletedProcess(arguments, int(failure == "nonzero"), b"", b"warning" if failure == "stderr" else b"")
    mocks(monkeypatch, sample, effect=effect)
    image, report = load(sample)
    assert image is None and report["status"] == "error"
    assert report["snapshot"]["removed"] is True
    assert report["source"]["unchanged_during_read"] is True


def test_missing_tool_does_not_extract_or_write_original(monkeypatch, sample):
    calls, _ = mocks(monkeypatch, sample)
    image, report = raw.load_raw_embedded_preview(sample[0], exiftool_path=sample[1].with_name("absent"))
    assert image is None and report["reason"] == "exiftool_unavailable" and not calls
    assert report["source"]["unchanged_during_read"] is True
    assert report["snapshot"]["path"] is None


@pytest.mark.parametrize("changed", ["source", "snapshot", "tool"])
def test_change_during_extraction_rejects_stale_evidence(monkeypatch, sample, changed):
    def effect(stage, snapshot, arguments):
        if stage == "extract":
            target = {"source": sample[0], "snapshot": snapshot, "tool": sample[1]}[changed]
            target.chmod(0o600)
            target.write_bytes(b"replacement data")
    mocks(monkeypatch, sample, effect=effect)
    image, report = load(sample)
    assert image is None and report["viewable_preview"] is False
    assert report["reason"] == {"source": "source_changed_during_read", "snapshot": "snapshot_changed_during_read",
                                 "tool": "exiftool_changed_during_read"}[changed]
    assert report["snapshot"]["removed"] is True


def test_same_size_source_change_with_restored_mtime_still_rejected(monkeypatch, sample):
    before = sample[0].stat()
    def effect(stage, snapshot, arguments):
        if stage == "extract":
            sample[0].write_bytes(b"x" * before.st_size)
            os.utime(sample[0], ns=(before.st_atime_ns, before.st_mtime_ns))
    mocks(monkeypatch, sample, effect=effect)
    assert load(sample)[1]["reason"] == "source_changed_during_read"


def test_probe_duplicate_named_tag_is_unsafe(monkeypatch, sample):
    calls, _ = mocks(monkeypatch, sample, probe_tags={"Track1:Copy1:JpgFromRaw": "(Binary data 12 bytes, use -b option to extract)"})
    image, report = load(sample)
    assert image is None and report["reason"] == "embedded_preview_ambiguous_instances"
    assert len(calls) == 1


@pytest.mark.parametrize("probe_tags,reason", [
    ({"Track1:JpgFromRaw": "(Binary data 67108865 bytes, use -b option to extract)"}, "embedded_preview_too_large"),
    ({"Track1:JpgFromRaw": "(Binary data 0 bytes, use -b option to extract)"}, "embedded_preview_empty"),
    ({"Track1:JpgFromRaw": 123}, "embedded_preview_not_binary"),
    ({"ExifTool:Warning": "bad offsets"}, "embedded_preview_probe_warning_or_error"),
    ({"SourceFile": "/different/raw.cr3"}, "embedded_preview_probe_source_mismatch"),
    ({"ExifTool:ExifToolVersion": 12.34}, "embedded_preview_probe_version_mismatch"),
])
def test_probe_safety_failures(monkeypatch, sample, probe_tags, reason):
    mocks(monkeypatch, sample, probe_tags=probe_tags)
    image, report = load(sample)
    assert image is None and report["reason"] == reason


def test_encoded_length_matches_probed_embedded_length(monkeypatch, sample):
    mocks(monkeypatch, sample, probe_tags={"Track1:JpgFromRaw": "(Binary data 12 bytes, use -b option to extract)"})
    assert load(sample)[1]["reason"] == "embedded_preview_length_mismatch"


def test_pixel_limit_checked_before_decode(monkeypatch, sample):
    mocks(monkeypatch, sample)
    monkeypatch.setattr(raw, "MAX_JPEG_PIXELS", 100)
    assert load(sample)[1]["reason"] == "embedded_preview_pixel_limit"


def test_raw_snapshot_size_limit_precedes_hashing_or_subprocess(monkeypatch, sample):
    with sample[0].open("wb") as stream:
        stream.truncate(raw.MAX_SOURCE_BYTES + 1)
    monkeypatch.setattr(metadata, "read_af_metadata", lambda *a, **kw: pytest.fail("must not parse"))
    image, report = load(sample)
    assert image is None and report["reason"] == "source_too_large"


@pytest.mark.parametrize("timeout", [0, -1, 61, float("nan"), float("inf"), True, "10", 10**1000])
def test_invalid_timeout_is_controlled(sample, timeout):
    assert load(sample, timeout=timeout)[1]["reason"] == "invalid_timeout"


@pytest.mark.parametrize("max_edge", [0, -1, 769, True, 768.0, "768"])
def test_invalid_raster_limit_is_controlled(sample, max_edge):
    assert load(sample, max_edge=max_edge)[1]["reason"] == "invalid_max_edge"


def test_metadata_source_identity_is_never_relabelled(monkeypatch, sample):
    mocks(monkeypatch, sample, metadata_effect=lambda path, payload: payload.update(SourceFile=str(sample[0])))
    image, report = load(sample)
    assert image is None and report["reason"] == "metadata_exiftool_source_mismatch"
    assert report["metadata"]["raw_tags"]["SourceFile"] == str(sample[0])
    assert report["metadata"]["source"]["path"] == report["snapshot"]["path"]


def test_missing_raw_main_orientation_never_guesses_normal(monkeypatch, sample):
    calls, _ = mocks(monkeypatch, sample, metadata_effect=lambda path, payload: payload.pop("IFD0:Orientation"))
    image, report = load(sample)
    assert image is None and report["reason"] == "unsafe_raw_main_orientation_missing" and calls == []


def test_snapshot_metadata_stat_mismatch_fails_binding(monkeypatch, sample):
    mocks(monkeypatch, sample)
    original = metadata.read_af_metadata
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        result["source"]["stat_identity"]["inode"] += 1
        return result
    monkeypatch.setattr(metadata, "read_af_metadata", changed)
    image, report = load(sample)
    assert image is None and report["reason"] == "metadata_snapshot_binding_mismatch"
    assert report["binding"]["metadata_matches_snapshot"] is False


def test_metadata_parser_mismatch_fails_binding(monkeypatch, sample):
    mocks(monkeypatch, sample)
    original = metadata.read_af_metadata
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        result["parser"]["stat_identity"]["size"] += 1
        return result
    monkeypatch.setattr(metadata, "read_af_metadata", changed)
    assert load(sample)[1]["reason"] == "metadata_parser_binding_mismatch"


@pytest.mark.parametrize("payload", [b"not json", b"[]", b"[{},{}]", b'[{"x":1,"x":2}]',
                                     b'[{"x":NaN}]', b'[{"x":"\\ud800"}]'])
def test_probe_malformed_json_fails_closed(monkeypatch, sample, payload):
    def effect(stage, snapshot, arguments):
        if stage == "probe":
            return subprocess.CompletedProcess(arguments, 0, payload, b"")
    mocks(monkeypatch, sample, effect=effect)
    image, report = load(sample)
    assert image is None and report["status"] == "error"
    assert report["snapshot"]["removed"] is True


def test_missing_named_tag_never_falls_back_to_another_tag(monkeypatch, sample):
    def effect(stage, snapshot, arguments):
        if stage == "probe":
            payload = [{"SourceFile": str(snapshot), "ExifTool:ExifToolVersion": 13.59,
                        "QuickTime:PreviewImage": "(Binary data 12 bytes, use -b option to extract)"}]
            return subprocess.CompletedProcess(arguments, 0, json.dumps(payload).encode(), b"")
    calls, _ = mocks(monkeypatch, sample, effect=effect)
    image, report = load(sample)
    assert image is None and report["reason"] == "embedded_preview_missing" and len(calls) == 1


def test_sibling_jpeg_is_never_opened_or_needed(monkeypatch, sample):
    sibling = sample[0].with_suffix(".jpg")
    sibling.write_bytes(b"unrelated and deliberately invalid JPEG")
    original_open = Path.open
    def opened(path, *args, **kwargs):
        assert path != sibling, "RAW extraction must not consult a sibling file"
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", opened)
    mocks(monkeypatch, sample)
    image, report = load(sample)
    assert report["viewable_preview"] is True
    image.close()


def test_unicode_option_like_relative_source_path_resolves_safely(monkeypatch, sample):
    monkeypatch.chdir(sample[0].parent)
    relative = Path(sample[0].name)
    mocks(monkeypatch, sample)
    image, report = raw.load_raw_embedded_preview(relative, exiftool_path=sample[1])
    assert report["viewable_preview"] is True
    assert report["source"]["path"] == str(sample[0])
    assert not report["extraction"]["read_argv"][-1].startswith("-")
    image.close()


@pytest.mark.parametrize("source_type", ["missing", "directory", "jpeg"])
def test_invalid_source_does_not_start_metadata(monkeypatch, sample, source_type):
    source = {"missing": sample[0].with_name("absent.cr3"), "directory": sample[0].parent,
              "jpeg": sample[0].with_suffix(".jpg")}[source_type]
    if source_type == "jpeg":
        source.write_bytes(jpeg())
    monkeypatch.setattr(metadata, "read_af_metadata", lambda *a, **kw: pytest.fail("must not parse"))
    image, report = raw.load_raw_embedded_preview(source, exiftool_path=sample[1])
    assert image is None and report["reason"] == {
        "missing": "source_unavailable", "directory": "source_not_regular_file", "jpeg": "unsupported_raw_extension"}[source_type]


def test_fixed_environment_has_no_user_injection(monkeypatch):
    for key in ("PERL5OPT", "PERL5LIB", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "EXIFTOOL_HOME", "PYTHONPATH"):
        monkeypatch.setenv(key, "do-not-inherit")
    monkeypatch.setenv("PATH", "/untrusted/bin")
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    env = raw._fixed_environment()
    assert "do-not-inherit" not in env.values()
    assert env["PATH"] == os.defpath
    assert env["SystemRoot"] == "C:\\Windows"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_streaming_output_limit_kills_process(stream):
    limit = 1024 if stream == "stdout" else raw._MAX_STDERR_BYTES
    code = f"import sys; sys.{stream}.buffer.write(b'x' * {limit + 65536}); sys.{stream}.flush()"
    with pytest.raises(raw._Failure, match="exiftool_output_too_large"):
        raw._run_bounded(Path(sys.executable), ("-c", code), 3.0, 1024)


def test_bounded_runner_timeout():
    with pytest.raises(subprocess.TimeoutExpired):
        raw._run_bounded(Path(sys.executable), ("-c", "import time; time.sleep(3)"), 0.05, 1024)


def test_bounded_runner_success():
    process = raw._run_bounded(Path(sys.executable), ("-c", "print('safe binary output')"), 3.0, 1024)
    assert (process.returncode == 0
            and process.stdout == ("safe binary output" + os.linesep).encode()
            and not process.stderr)
