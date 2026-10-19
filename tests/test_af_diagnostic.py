"""Same-source diagnostic tests are synthetic, never camera calibration."""
import hashlib
import json
import os
from pathlib import Path

from PIL import Image, ImageOps
import pytest

from ai_cull_assistant import af_diagnostic as diagnostic
from ai_cull_assistant import af_metadata as af


@pytest.fixture
def camera(tmp_path, monkeypatch):
    source = tmp_path / "原片 -execute.jpg"
    parser = tmp_path / "exiftool"
    parser.write_text("mock parser, never executed")
    settings = {"orientation": 1, "raw_changes": {}, "omit": [], "status": "not_validated"}
    calls = []

    def make_source(orientation=1):
        image = Image.new("RGB", (160, 96), (29, 49, 79))
        for x in range(160):
            for y in range(96):
                image.putpixel((x, y), (x, y, (x+y) % 256))
        exif = Image.Exif()
        if orientation is not None:
            exif[274] = orientation
        image.save(source, quality=97, subsampling=0, exif=exif)
        settings["orientation"] = orientation

    def read(path, **kwargs):
        calls.append((path, kwargs))
        raw = {"SourceFile": str(source.resolve()), "ExifTool:ExifToolVersion": 13.59,
               "IFD0:Make": "Panasonic", "IFD0:Model": "DC-S5M2",
               "IFD0:Orientation": settings["orientation"],
               "Panasonic:PanasonicImageWidth": 160, "Panasonic:PanasonicImageHeight": 96,
               "ExifIFD:ExifImageWidth": 160, "ExifIFD:ExifImageHeight": 96,
               "Panasonic:FocusMode": 6, "Panasonic:AFAreaMode": "16 0",
               "Panasonic:AFPointPosition": "0.25 0.6", "Panasonic:AFAreaSize": "0.1 0.2"}
        raw.update(settings["raw_changes"])
        for key in settings["omit"]:
            raw.pop(key, None)
        return {"schema": af.SCHEMA_VERSION, "status": settings["status"],
                "reason": "real_camera_coordinate_mapping_not_validated",
                "source": {**af._source_identity(Path(path)), "unchanged_during_read": True},
                "parser": {"name": "ExifTool", "path": str(parser), "version": "13.59",
                           "stat_identity": af._stat_identity(parser.stat()),
                           "adapter_version": af.ADAPTER_VERSION, "cache_used": False},
                "raw_tags": raw, "diagnostics": af._diagnostics(raw), "warnings": [],
                "usable_for_subject_selection": False, "usable_for_keep_reject": False}

    make_source()
    monkeypatch.setattr(af, "read_af_metadata", read)
    return source, settings, calls, make_source


def export(camera, tmp_path, **kwargs):
    return diagnostic.export_af_diagnostic(
        camera[0], report_path=tmp_path / "report.json", preview_path=tmp_path / "preview.png", **kwargs)


def pixel_digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def symlink_or_skip(link, target):
    try:
        link.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks symbolic-link privilege")
        raise


def rename_or_skip_windows_sharing(source, target):
    try:
        source.rename(target)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 32:
            pytest.skip("Windows sharing lock prevents the POSIX replacement scenario")
        raise


@pytest.mark.parametrize("orientation, center, box", [
    (1, [.25, .6], [.2, .5, .1, .2]),
    (2, [.75, .6], [.7, .5, .1, .2]),
    (3, [.75, .4], [.7, .3, .1, .2]),
    (4, [.25, .4], [.2, .3, .1, .2]),
    (5, [.6, .25], [.5, .2, .2, .1]),
    (6, [.4, .25], [.3, .2, .2, .1]),
    (7, [.4, .75], [.3, .7, .2, .1]),
    (8, [.6, .75], [.5, .7, .2, .1]),
])
def test_eight_orientations_bind_full_jpeg_once(camera, tmp_path, orientation, center, box):
    source, _, calls, make_source = camera
    make_source(orientation)
    before = source.read_bytes(), af._stat_identity(source.stat())
    with Image.open(source) as original:
        expected = ImageOps.exif_transpose(original).convert("RGB")
    result = export(camera, tmp_path)
    assert result["status"] == "not_validated"
    assert result["camera_mapping"]["status"] == "not_validated"
    assert result["automatic_decisions_enabled"] is False
    assert result["usable_for_subject_selection"] is False
    assert result["usable_for_keep_reject"] is False
    assert "validated_display_mapping" not in result
    assert result["geometry"]["status"] == "not_validated"
    assert result["geometry"]["center_xy_hypothesis"] == pytest.approx(center)
    assert result["geometry"]["bbox_xywh_hypothesis"] == pytest.approx(box)
    assert result["geometry"]["center_drawn"] is True
    assert result["geometry"]["box_drawn"] is True
    preview = result["preview"]
    assert preview["decoded_source_size"] == [160, 96]
    assert preview["display_frame_size"] == list(expected.size)
    assert preview["raster_size"] == list(expected.size)
    assert preview["crop"] == "full_frame"
    assert preview["exif_orientation"] == orientation
    assert preview["exif_transposed"] is True
    assert preview["orientation_agrees"] is True
    assert preview["clean_pixels_sha256"] == pixel_digest(expected)
    assert preview["sha256"] == hashlib.sha256((tmp_path / "preview.png").read_bytes()).hexdigest()
    with Image.open(tmp_path / "preview.png") as image:
        assert image.format == "PNG"
        assert image.getexif().get(274) is None
        assert list(image.size) == preview["output_size"]
    binding = result["binding"]
    assert binding["source_unchanged"] is True
    assert binding["metadata_source_matches"] is True
    assert binding["parser_unchanged"] is True
    assert binding["before"] == binding["after"]
    assert binding["before"]["sha256"] == hashlib.sha256(before[0]).hexdigest()
    assert len(calls) == 1 and calls[0][0] == source.resolve()
    assert (source.read_bytes(), af._stat_identity(source.stat())) == before
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == result


@pytest.mark.skipif(os.name != "nt", reason="Windows stat/fstat ctime semantics")
def test_windows_decode_handle_ctime_difference_keeps_source_binding(camera, monkeypatch):
    decode = diagnostic._decode_jpeg

    def windows_handle_ctime(*args, **kwargs):
        image, preview, unchanged = decode(*args, **kwargs)
        for key in ("decode_stat_before", "decode_stat_after"):
            preview[key] = dict(preview[key])
            preview[key]["ctime_ns"] += 1_000_000
        return image, preview, unchanged

    monkeypatch.setattr(diagnostic, "_decode_jpeg", windows_handle_ctime)
    image, report = diagnostic.prepare_af_diagnostic(camera[0])
    try:
        assert image is not None
        assert report["binding"]["source_unchanged"] is True
        assert report["geometry"]["center_xy_hypothesis"] == [.25, .6]
    finally:
        if image is not None:
            image.close()


def test_decode_handle_change_still_rejects_source_binding(camera, monkeypatch):
    decode = diagnostic._decode_jpeg

    def changed_handle(*args, **kwargs):
        image, preview, _ = decode(*args, **kwargs)
        return image, preview, False

    monkeypatch.setattr(diagnostic, "_decode_jpeg", changed_handle)
    image, report = diagnostic.prepare_af_diagnostic(camera[0])
    assert image is None
    assert report["binding"]["source_unchanged"] is False
    assert report["reason"] == "source_changed_or_unbound"


@pytest.mark.parametrize("changes, omit, reason", [
    ({"IFD0:Model": "DC-S5M2X"}, [], "unsupported_camera"),
    ({"IFD0:Model": "DC-GH6"}, [], "unsupported_camera"),
    ({"IFD0:Make": "Canon"}, [], "unsupported_camera"),
    ({"Panasonic:AFAreaMode": "255 255"}, [], "mode_not_supported"),
    ({"Panasonic:FocusMode": 2}, [], "mode_not_supported"),
    ({"IFD0:Orientation": 6}, [], "orientation_mismatch"),
    ({"IFD0:Copy1:Orientation": 6}, [], "orientation_not_supported"),
    ({"IFD1:Orientation": 6}, [], "orientation_not_supported"),
    ({}, ["IFD0:Orientation"], "orientation_not_supported"),
    ({}, ["Panasonic:AFPointPosition"], "coordinates_not_supported"),
    ({"Panasonic:AFPointPosition": "0.9 0.9", "Panasonic:AFAreaSize": ".4 .4"}, [], "coordinates_not_supported"),
    ({"Panasonic:AFAreaSize": "0 0"}, [], "coordinates_not_supported"),
])
def test_uncertain_evidence_exports_image_without_geometry(camera, tmp_path, changes, omit, reason):
    camera[1].update(raw_changes=changes, omit=omit)
    result = export(camera, tmp_path)
    assert result["geometry"]["reason"] == reason
    assert result["geometry"]["center_drawn"] is False
    assert result["geometry"]["box_drawn"] is False
    assert result["geometry"]["center_xy_hypothesis"] is None
    assert (tmp_path / "preview.png").is_file()


def test_missing_area_draws_only_cross(camera, tmp_path):
    camera[1]["omit"] = ["Panasonic:AFAreaSize"]
    result = export(camera, tmp_path)
    assert result["geometry"]["center_drawn"] is True
    assert result["geometry"]["box_drawn"] is False
    assert result["geometry"]["bbox_xywh_hypothesis"] is None


def test_missing_pillow_orientation_cannot_draw(camera, tmp_path):
    camera[3](None)
    camera[1]["raw_changes"] = {"IFD0:Orientation": 1}
    result = export(camera, tmp_path)
    assert result["geometry"]["reason"] == "jpeg_orientation_missing_or_invalid"
    assert result["geometry"]["center_drawn"] is False


def test_metadata_error_cannot_draw(camera, tmp_path):
    camera[1]["status"] = "error"
    result = export(camera, tmp_path)
    assert result["geometry"]["reason"] == "metadata_not_supported"
    assert result["geometry"]["center_drawn"] is False


def test_changed_source_across_parser_returns_no_pixels_or_outputs(camera, tmp_path, monkeypatch):
    reader = af.read_af_metadata
    def change(path, **kwargs):
        record = reader(path, **kwargs)
        camera[3](6)
        return record
    monkeypatch.setattr(af, "read_af_metadata", change)
    image, result = diagnostic.prepare_af_diagnostic(camera[0])
    assert image is None and result["status"] == "error"
    assert result["binding"]["source_unchanged"] is False
    assert result["geometry"]["reason"] == "source_changed_or_unbound"
    assert result["geometry"]["center_drawn"] is False
    with pytest.raises(ValueError, match="Source changed"):
        export(camera, tmp_path)
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "preview.png").exists()


def test_changed_parser_cannot_draw(camera, tmp_path, monkeypatch):
    reader = af.read_af_metadata
    def change(path, **kwargs):
        record = reader(path, **kwargs)
        Path(record["parser"]["path"]).write_text("changed parser")
        return record
    monkeypatch.setattr(af, "read_af_metadata", change)
    result = export(camera, tmp_path)
    assert result["binding"]["parser_unchanged"] is False
    assert result["geometry"]["reason"] == "parser_changed_or_unbound"
    assert result["geometry"]["center_drawn"] is False


def test_full_frame_downscale_keeps_aspect_and_binds_pixels(camera, tmp_path):
    result = export(camera, tmp_path, max_edge=80)
    assert result["preview"]["raster_size"] == [80, 48]
    assert result["preview"]["display_frame_size"] == [160, 96]
    assert result["preview"]["crop"] == "full_frame"
    assert result["preview"]["warning"] == "UNVALIDATED AF DIAGNOSTIC"


@pytest.mark.parametrize("kind", ["source", "file", "symlink", "dangling_symlink", "hardlink", "directory"])
@pytest.mark.parametrize("output", ["report", "preview"])
def test_outputs_never_overwrite_anything(camera, tmp_path, kind, output):
    source = camera[0]
    destination = tmp_path / ("report.json" if output == "report" else "preview.png")
    if kind == "source":
        destination = source
    elif kind == "file":
        destination.write_bytes(b"existing")
    elif kind == "symlink":
        symlink_or_skip(destination, source)
    elif kind == "dangling_symlink":
        symlink_or_skip(destination, tmp_path / "missing-target")
    elif kind == "hardlink":
        os.link(source, destination)
    elif kind == "directory":
        destination.mkdir()
    before = source.read_bytes()
    kwargs = {"report_path": tmp_path / "report.json", "preview_path": tmp_path / "preview.png"}
    kwargs[output + "_path"] = destination
    with pytest.raises((ValueError, OSError)):
        diagnostic.export_af_diagnostic(source, **kwargs)
    assert source.read_bytes() == before
    assert camera[2] == []
    if kind == "file":
        assert destination.read_bytes() == b"existing"
    if kind == "dangling_symlink":
        assert destination.is_symlink() and not (tmp_path / "missing-target").exists()


def test_output_pair_collision_rejected(camera, tmp_path):
    target = tmp_path / "same.png"
    with pytest.raises(ValueError):
        diagnostic.export_af_diagnostic(camera[0], report_path=target, preview_path=target)
    assert not target.exists()


def test_second_exclusive_create_failure_removes_only_our_new_file(camera, tmp_path, monkeypatch):
    original = diagnostic.os.open
    def create(path, flags, *args, **kwargs):
        if Path(path).name == "preview.png":
            Path(path).write_bytes(b"another writer won")
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(diagnostic.os, "open", create)
    with pytest.raises(FileExistsError):
        export(camera, tmp_path)
    assert not (tmp_path / "report.json").exists()
    assert (tmp_path / "preview.png").read_bytes() == b"another writer won"


def test_write_failure_rolls_back_both_new_outputs(camera, tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic.os, "write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        export(camera, tmp_path)
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "preview.png").exists()


def test_cleanup_does_not_delete_replacement(camera, tmp_path, monkeypatch):
    def fail(*args):
        report = tmp_path / "report.json"
        # Keep the original inode alive so a filesystem cannot immediately reuse it.
        rename_or_skip_windows_sharing(report, tmp_path / "moved-our-report.json")
        report.write_bytes(b"other writer replacement")
        raise OSError("write failed")
    monkeypatch.setattr(diagnostic.os, "write", fail)
    with pytest.raises(OSError, match="write failed"):
        export(camera, tmp_path)
    assert (tmp_path / "report.json").read_bytes() == b"other writer replacement"
    assert not (tmp_path / "preview.png").exists()


def test_raw_input_information_only_never_opens_pixels_or_finds_sibling(camera, tmp_path, monkeypatch):
    raw = tmp_path / "photo.rw2"
    raw.write_bytes(b"RAW bytes")
    Image.new("RGB", (100, 100)).save(raw.with_suffix(".jpg"))
    monkeypatch.setattr(diagnostic.Image, "open", lambda *a, **kw: pytest.fail("RAW must not decode"))
    with pytest.raises(ValueError, match="JPEG"):
        diagnostic.export_af_diagnostic(raw, report_path=tmp_path / "reject.json", preview_path=tmp_path / "reject.png")
    result = diagnostic.export_af_diagnostic(raw, report_path=tmp_path / "info.json")
    assert result["status"] == "unsupported"
    assert result["preview"] is None
    assert len(camera[2]) == 1 and camera[2][0][0] == raw.resolve()
    assert not (tmp_path / "reject.json").exists()
    assert not (tmp_path / "reject.png").exists()


def test_renamed_non_jpeg_is_not_decoded_as_jpeg(camera, tmp_path):
    Image.new("RGB", (10, 10)).save(camera[0], format="PNG")
    with pytest.raises(ValueError, match="JPEG"):
        export(camera, tmp_path)
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "preview.png").exists()


@pytest.mark.parametrize("flag,value", [("--timeout", "nan"), ("--timeout", "inf"), ("--timeout", "0"),
                                         ("--timeout", "121"), ("--max-edge", "0"), ("--max-edge", "4097")])
def test_cli_rejects_bad_limits(tmp_path, flag, value):
    with pytest.raises(SystemExit):
        diagnostic.main(["source.jpg", "--report", str(tmp_path / "new.json"), "--preview", str(tmp_path / "new.png"), flag, value])


def test_cli_exports_explicit_pair_and_keeps_metadata_record(camera, tmp_path, capsys):
    assert diagnostic.main([str(camera[0]), "--report", str(tmp_path / "new.json"),
                            "--preview", str(tmp_path / "new.png"), "--exiftool", "trusted-parser", "--timeout", "2.5"]) == 0
    result = json.loads((tmp_path / "new.json").read_text(encoding="utf-8"))
    assert result["metadata"]["raw_tags"]["Panasonic:AFPointPosition"] == "0.25 0.6"
    assert camera[2][0][1]["timeout"] == 2.5
    assert camera[2][0][1]["exiftool_path"] == Path("trusted-parser")
    assert "UNVALIDATED" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["source", "parser", "source_removed"])
def test_change_during_render_removes_geometry_before_publish(camera, tmp_path, monkeypatch, change):
    render = diagnostic._render
    calls = []
    def changed(image, geometry):
        result = render(image, geometry)
        calls.append(geometry["center_drawn"])
        if len(calls) == 1:
            if change == "source":
                camera[3](8)
            elif change == "parser":
                (tmp_path / "exiftool").write_text("changed during rendering")
            else:
                camera[0].unlink()
        return result
    monkeypatch.setattr(diagnostic, "_render", changed)
    result = export(camera, tmp_path)
    assert calls == [True, False]
    assert result["geometry"]["center_drawn"] is False
    expected = "parser_changed_or_unbound" if change == "parser" else "source_changed_or_unbound"
    assert result["geometry"]["reason"] == expected


def test_windows_rollback_closes_handles_before_unlink(camera, tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "_UNLINK_REQUIRES_CLOSED_FILES", True)
    opened, closed = [], []
    original_open, original_close, original_unlink = os.open, os.close, Path.unlink
    def open_file(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        opened.append(fd)
        return fd
    def close_file(fd):
        closed.append(fd)
        original_close(fd)
    def unlink(path, *args, **kwargs):
        assert set(opened).issubset(closed)
        original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(diagnostic.os, "open", open_file)
    monkeypatch.setattr(diagnostic.os, "close", close_file)
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(diagnostic.os, "write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        export(camera, tmp_path)
    assert opened == closed
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "preview.png").exists()


@pytest.mark.parametrize("change", ["replacement", "modified_bytes", "symlink"])
def test_success_requires_written_output_identity_and_bytes(camera, tmp_path, monkeypatch, change):
    fsync = diagnostic.os.fsync
    count = []
    def changed(fd):
        fsync(fd)
        count.append(fd)
        if len(count) == 2:
            preview = tmp_path / "preview.png"
            if change == "modified_bytes":
                preview.write_bytes(b"corrupted by concurrent writer")
            else:
                rename_or_skip_windows_sharing(preview, tmp_path / "moved-our-preview.png")
                if change == "replacement":
                    preview.write_bytes(b"other writer output")
                else:
                    symlink_or_skip(preview, tmp_path / "moved-our-preview.png")
    monkeypatch.setattr(diagnostic.os, "fsync", changed)
    with pytest.raises(OSError, match="changed during export"):
        export(camera, tmp_path)
    assert not (tmp_path / "report.json").exists()
    preview = tmp_path / "preview.png"
    if change == "replacement":
        assert preview.read_bytes() == b"other writer output"
    elif change == "symlink":
        assert preview.is_symlink()
    else:
        assert not preview.exists()


@pytest.mark.parametrize("fail_write", [False, True])
def test_export_closes_decoded_pixels_on_success_and_failure(camera, tmp_path, monkeypatch, fail_write):
    decoded = []
    original = diagnostic._decode_jpeg
    def decode(*args, **kwargs):
        result = original(*args, **kwargs)
        decoded.append(result[0])
        return result
    monkeypatch.setattr(diagnostic, "_decode_jpeg", decode)
    if fail_write:
        monkeypatch.setattr(diagnostic, "_write_outputs", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            export(camera, tmp_path)
    else:
        export(camera, tmp_path)
    with pytest.raises(ValueError, match="closed"):
        decoded[0].getpixel((0, 0))


def test_prepare_returns_owned_clean_pixels_and_never_writes(camera, tmp_path):
    before = set(tmp_path.iterdir())
    image, result = diagnostic.prepare_af_diagnostic(camera[0])
    try:
        assert image is not None and image.mode == "RGB"
        assert result["preview"]["annotation_applied"] is False
        assert result["geometry"]["center_drawn"] is False
        assert result["geometry"]["box_drawn"] is False
        assert result["geometry"]["center_xy_hypothesis"] == [.25, .6]
        assert result["preview"]["clean_pixels_sha256"] == pixel_digest(image)
        assert result["preview"]["decoded_bytes_sha256"] == result["binding"]["before"]["sha256"]
        assert set(tmp_path.iterdir()) == before
    finally:
        image.close()


def test_camera_mpo_jpeg_decodes_only_full_primary_frame(camera, tmp_path):
    exif = Image.Exif()
    exif[274] = 1
    with Image.new("RGB", (160, 96), "blue") as primary, Image.new("RGB", (40, 24), "red") as secondary:
        primary.save(camera[0], format="MPO", save_all=True, append_images=[secondary], exif=exif)
    result = export(camera, tmp_path)
    decoder = result["preview"]["decoder"]
    assert decoder["format"] == "MPO"
    assert decoder["primary_frame_index"] == 0
    assert decoder["container_frame_count"] == 2
    assert decoder["encoded_snapshot"] is True
    assert result["preview"]["decoded_source_size"] == [160, 96]
    assert result["binding"]["source_unchanged"] is True


def test_encoded_snapshot_prevents_same_stat_aba_pixel_substitution(camera, tmp_path, monkeypatch):
    source = camera[0]
    original = source.read_bytes()
    changed = bytearray(original)
    quantization = changed.index(b"\xff\xdb") + 5
    changed[quantization] = max(1, changed[quantization] - 1) if changed[quantization] > 1 else 2
    original_stat = af._stat_identity(source.stat())
    stat_identity = af._stat_identity
    monkeypatch.setattr(af, "_stat_identity", lambda value: original_stat if value.st_ino == original_stat["inode"] else stat_identity(value))
    decode = diagnostic._decode_jpeg
    def substitute(path, max_edge):
        source.write_bytes(changed)
        try:
            return decode(path, max_edge)
        finally:
            source.write_bytes(original)
    monkeypatch.setattr(diagnostic, "_decode_jpeg", substitute)
    image, result = diagnostic.prepare_af_diagnostic(source)
    assert image is None
    assert result["binding"]["before"] == result["binding"]["after"]
    assert result["binding"]["source_unchanged"] is False
    assert result["preview"]["decoded_bytes_sha256"] != result["binding"]["before"]["sha256"]
    assert result["geometry"]["center_xy_hypothesis"] is None
    assert source.read_bytes() == original


@pytest.mark.parametrize("field, value", [("sha256", "0" * 64), ("path", "/unrelated/same.jpg"), ("unchanged_during_read", False)])
def test_prepare_rejects_unbound_metadata_source(camera, monkeypatch, field, value):
    reader = af.read_af_metadata
    def unbound(path, **kwargs):
        record = reader(path, **kwargs)
        record["source"][field] = value
        return record
    monkeypatch.setattr(af, "read_af_metadata", unbound)
    image, result = diagnostic.prepare_af_diagnostic(camera[0])
    assert image is None and result["status"] == "error"
    assert result["binding"]["metadata_source_matches"] is False
    assert result["geometry"]["center_xy_hypothesis"] is None


@pytest.mark.parametrize("tag", ["Panasonic:PanasonicImageWidth", "Panasonic:PanasonicImageHeight",
                                  "ExifIFD:ExifImageWidth", "ExifIFD:ExifImageHeight"])
@pytest.mark.parametrize("issue", ["missing", "mismatch", "duplicate", "invalid"])
def test_dimensions_require_both_unambiguous_camera_and_exif_pairs(camera, tmp_path, tag, issue):
    if issue == "missing":
        camera[1]["omit"] = [tag]
    elif issue == "mismatch":
        camera[1]["raw_changes"] = {tag: 6000}
    elif issue == "duplicate":
        group, name = tag.split(":")
        camera[1]["raw_changes"] = {f"{group}:Copy1:{name}": 160 if name.endswith("Width") else 96}
    else:
        camera[1]["raw_changes"] = {tag: True}
    result = export(camera, tmp_path)
    assert result["geometry"]["reason"] == "camera_dimensions_missing_ambiguous_or_mismatched"
    assert result["geometry"]["center_xy_hypothesis"] is None
    assert result["preview"]["dimension_evidence"]["status"] == "unknown"
    assert result["preview"]["dimension_evidence"]["camera_original_authenticated"] is False


def test_dimension_agreement_never_authenticates_camera_original(camera, tmp_path):
    result = export(camera, tmp_path)
    dimensions = result["preview"]["dimension_evidence"]
    assert dimensions["status"] == "not_validated"
    assert dimensions["camera_original_required"] is True
    assert dimensions["camera_original_authenticated"] is False
    assert "same-size" in dimensions["limitation"]
