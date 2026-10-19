"""Synthetic/mocked AF adapter tests; these do not validate any real camera."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from ai_cull_assistant import af_metadata as af


@pytest.fixture
def sample(tmp_path):
    source = tmp_path / "中文照片 -overwrite_original.RW2"
    source.write_bytes(b"synthetic bytes, not a real RAW")
    tool = tmp_path / "exiftool"
    tool.write_text("not executed; subprocess is always mocked", encoding="utf-8")
    return source, tool


def tags(source, **changes):
    result = {"SourceFile": str(source.resolve()), "ExifTool:ExifToolVersion": 13.59,
              "IFD0:Make": "Panasonic", "IFD0:Model": "DC-S5M2",
              "IFD0:Orientation": 1, "ExifIFD:ImageWidth": 6000, "ExifIFD:ImageHeight": 4000,
              "Panasonic:PanasonicImageWidth": 6048, "Panasonic:PanasonicImageHeight": 4032,
              "Panasonic:AFPointPosition": "0.25 0.6", "Panasonic:AFAreaSize": "0.1 0.2",
              "Panasonic:FocusMode": 6, "Panasonic:AFAreaMode": "16 0",
              "Panasonic:AFSubjectDetection": 1}
    result.update(changes)
    return result


def mock_tool(monkeypatch, sample, *, payload=None, effect=None, version="13.59", returncode=0, stderr=""):
    source, tool = sample
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[-1] == "-ver":
            return subprocess.CompletedProcess(argv, 0, version + "\n", "")
        if effect:
            effect()
        output = payload if isinstance(payload, str) else json.dumps([tags(source) if payload is None else payload], ensure_ascii=False)
        return subprocess.CompletedProcess(argv, returncode, output, stderr)
    monkeypatch.setattr(af.subprocess, "run", run)
    monkeypatch.setattr(af.shutil, "which", lambda name: str(tool) if name == "exiftool" else None)
    return calls


def test_read_only_fixed_arguments_and_identity(monkeypatch, sample):
    source, tool = sample
    before = source.read_bytes(), af._stat_identity(source.stat())
    calls = mock_tool(monkeypatch, sample)
    result = af.read_af_metadata(source, timeout=2.5)
    assert result["status"] == "not_validated"
    assert result["usable_for_subject_selection"] is False
    assert result["usable_for_keep_reject"] is False
    assert result["source"]["sha256"] == hashlib.sha256(before[0]).hexdigest()
    assert result["source"]["stat_identity"] == before[1]
    assert result["source"]["unchanged_during_read"] is True
    assert (source.read_bytes(), af._stat_identity(source.stat())) == before
    assert result["parser"]["version"] == "13.59"
    assert result["parser"]["adapter_version"] == af.ADAPTER_VERSION
    assert result["parser"]["path"] == str(tool.resolve())
    assert result["parser"]["cache_used"] is False
    assert len(calls) == 2
    assert calls[0][0] == [str(tool.resolve()), "-config", "", "-ver"]
    assert calls[1][0] == [str(tool.resolve()), *af.READ_OPTIONS,
                            *("-" + tag for tag in af.READ_TAGS), str(source.resolve())]
    for argv, options in calls:
        assert argv[1:3] == ["-config", ""]
        assert options["shell"] is False and options["check"] is False
        assert options["stdin"] == subprocess.DEVNULL
        assert options["timeout"] == 2.5 and options["encoding"] == "utf-8"
        assert options["cwd"] == str(tool.parent)
        assert not ({"-@", "-execute", "-stay_open", "-o", "-overwrite_original"} & set(argv))
    assert source.is_absolute()
    assert result["raw_tags"] == tags(source)
    assert result["diagnostics"]["dimensions"]["status"] == "unknown"
    assert result["diagnostics"]["subject_association"]["person_id"] is None
    assert sorted(p.name for p in source.parent.iterdir()) == sorted([source.name, tool.name])


def test_environment_disables_startup_and_module_injection(monkeypatch, sample):
    for key in ("PERL5OPT", "PERL5LIB", "PERLLIB", "PERL_USE_UNSAFE_INC", "EXIFTOOL_HOME", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"):
        monkeypatch.setenv(key, "malicious")
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    calls = mock_tool(monkeypatch, sample)
    af.read_af_metadata(sample[0])
    for _, options in calls:
        assert "malicious" not in options["env"].values()
        assert options["env"]["SystemRoot"] == "C:\\Windows"


@pytest.mark.skipif(os.name != "nt", reason="Windows stat/fstat ctime semantics")
def test_windows_path_and_handle_ctime_difference_is_not_a_source_change(monkeypatch, sample):
    source = sample[0]
    original_fstat = os.fstat

    def fstat_with_windows_ctime(fd):
        value = original_fstat(fd)
        fields = {name: getattr(value, name) for name in
                  ("st_size", "st_mtime_ns", "st_ctime_ns", "st_dev", "st_ino")}
        fields["st_ctime_ns"] += 1_000_000
        return SimpleNamespace(**fields)

    monkeypatch.setattr(af.os, "fstat", fstat_with_windows_ctime)
    identity = af._source_identity(source)
    assert identity["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert identity["stat_identity"] == af._stat_identity(source.stat())


def test_handle_identity_change_while_hashing_is_rejected(monkeypatch, sample):
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

    monkeypatch.setattr(af.os, "fstat", changing_fstat)
    with pytest.raises(af._SourceChanged):
        af._source_identity(sample[0])


def test_option_like_relative_unicode_filename_uses_absolute_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    source = Path("-execute 中文.RW2")
    source.write_bytes(b"synthetic")
    tool = tmp_path / "exiftool"
    tool.touch()
    calls = mock_tool(monkeypatch, (source, tool))
    result = af.read_af_metadata(source, exiftool_path=tool)
    assert result["status"] == "not_validated"
    assert calls[1][0][-1] == str(source.resolve())
    assert not calls[1][0][-1].startswith("-")


def test_missing_tool_downgrades_without_subprocess(monkeypatch, sample):
    monkeypatch.setattr(af.shutil, "which", lambda _: None)
    monkeypatch.setattr(af.subprocess, "run", lambda *a, **kw: pytest.fail("must not run"))
    result = af.read_af_metadata(sample[0])
    assert (result["status"], result["reason"]) == ("error", "exiftool_unavailable")
    assert result["source"]["sha256"]


@pytest.mark.parametrize("suffix", [".bat", ".cmd", ".CMD"])
def test_no_implicit_windows_shell_wrapper(monkeypatch, sample, suffix):
    tool = sample[1].with_suffix(suffix)
    tool.touch()
    monkeypatch.setattr(af.subprocess, "run", lambda *a, **kw: pytest.fail("must not run"))
    assert af.read_af_metadata(sample[0], exiftool_path=tool)["reason"] == "exiftool_unavailable"


@pytest.mark.parametrize("source_kind", ["absent", "directory"])
def test_unreadable_source_does_not_launch_tool(monkeypatch, sample, source_kind):
    source = sample[0].parent if source_kind == "directory" else sample[0].with_name("absent")
    monkeypatch.setattr(af.subprocess, "run", lambda *a, **kw: pytest.fail("must not run"))
    assert af.read_af_metadata(source)["reason"] == "source_unavailable"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "10", 10**1000])
def test_invalid_timeout_is_controlled(sample, timeout):
    assert af.read_af_metadata(sample[0], timeout=timeout)["reason"] == "invalid_timeout"


@pytest.mark.parametrize("stage", ["version", "metadata"])
def test_timeout_is_unknown_not_missing(monkeypatch, sample, stage):
    calls = mock_tool(monkeypatch, sample)
    original = af.subprocess.run
    def run(argv, **kwargs):
        if (argv[-1] == "-ver") == (stage == "version"):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return original(argv, **kwargs)
    monkeypatch.setattr(af.subprocess, "run", run)
    result = af.read_af_metadata(sample[0])
    assert result["status"] == "error"
    assert result["reason"] == "exiftool_" + stage + "_timeout"
    assert result["diagnostics"]["coordinates"]["status"] == "unknown"


@pytest.mark.parametrize("error", [OSError("cannot execute"), subprocess.SubprocessError("failed"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")])
def test_process_exception_degrades(monkeypatch, sample, error):
    mock_tool(monkeypatch, sample)
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(af.subprocess, "run", fail)
    assert af.read_af_metadata(sample[0])["status"] == "error"


@pytest.mark.parametrize("payload", ["not json", "{}", "[]", "[{}, {}]", '[{"x": NaN}]',
                                     '[{"x": Infinity}]', '[{"x": 1e999}]', '[{"x": -1e999}]',
                                     '[{"x": 1, "x": 2}]', '[{"x": "\\ud800"}]'])
def test_invalid_or_ambiguous_json_rejected(monkeypatch, sample, payload):
    mock_tool(monkeypatch, sample, payload=payload)
    assert af.read_af_metadata(sample[0])["reason"] == "exiftool_invalid_output"


@pytest.mark.parametrize("version", ["", "13.59 extra", "not ExifTool"])
def test_bad_version_rejected(monkeypatch, sample, version):
    calls = mock_tool(monkeypatch, sample, version=version)
    assert af.read_af_metadata(sample[0])["reason"] == "exiftool_version_error"
    assert len(calls) == 1


def test_reported_version_mismatch(monkeypatch, sample):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], **{"ExifTool:ExifToolVersion": 12.0}))
    assert af.read_af_metadata(sample[0])["reason"] == "exiftool_version_mismatch"


def test_source_mismatch(monkeypatch, sample):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], SourceFile="/different/source.RW2"))
    assert af.read_af_metadata(sample[0])["reason"] == "exiftool_source_mismatch"


def test_read_error_keeps_raw_tags_without_interpretation(monkeypatch, sample):
    payload = tags(sample[0], **{"ExifTool:Error": "damaged metadata"})
    mock_tool(monkeypatch, sample, payload=payload, returncode=1, stderr="failed")
    result = af.read_af_metadata(sample[0])
    assert result["reason"] == "exiftool_read_error"
    assert result["raw_tags"] == payload
    assert result["diagnostics"]["coordinates"]["status"] == "unknown"


@pytest.mark.parametrize("tag, reason", [("Warning", "exiftool_read_warning"), ("Error", "exiftool_read_error")])
def test_structural_warning_or_error_with_exit_zero_is_nonusable(monkeypatch, sample, tag, reason):
    payload = tags(sample[0], **{"ExifTool:" + tag: "JPEG format error"})
    mock_tool(monkeypatch, sample, payload=payload, returncode=0)
    result = af.read_af_metadata(sample[0])
    assert result["status"] == "error"
    assert result["reason"] == reason
    assert result["raw_tags"] == payload
    assert result["diagnostics"]["coordinates"]["status"] == "unknown"
    assert result["usable_for_subject_selection"] is False
    if tag == "Warning":
        assert "JPEG format error" in result["warnings"]


def test_missing_and_unsupported_are_distinct(monkeypatch, sample):
    payload = tags(sample[0])
    del payload["Panasonic:AFPointPosition"], payload["Panasonic:AFAreaSize"]
    mock_tool(monkeypatch, sample, payload=payload)
    assert af.read_af_metadata(sample[0])["status"] == "missing"
    payload["IFD0:Make"] = "Canon"
    mock_tool(monkeypatch, sample, payload=payload)
    result = af.read_af_metadata(sample[0])
    assert result["status"] == "unsupported"
    assert result["raw_tags"] == payload


@pytest.mark.parametrize("value", ["none", "n/a", "16777216 16777216", "4194303.99902344 4194303.99902344", "65535 65535", "-0.1 0.5", "1.1 0.5", "0.1 0.2 0.3 0.4", [[0.1, 0.2], [0.3, 0.4]], [True, 0.2], "NaN 0.5", {"x": .5, "y": .5}])
def test_invalid_sentinel_or_multipoint_never_produces_geometry(monkeypatch, sample, value):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], **{"Panasonic:AFPointPosition": value}))
    result = af.read_af_metadata(sample[0])
    assert result["status"] == "not_validated"
    assert result["raw_tags"]["Panasonic:AFPointPosition"] == value
    coordinates = result["diagnostics"]["coordinates"]
    assert coordinates["status"] == "unknown"
    assert coordinates["center"]["value"] is None
    assert coordinates["oriented_center_xy_hypothesis"] is None


@pytest.mark.parametrize("area", ["0 0.1", "-0.1 0.1", "1.1 0.1", "n/a", "0.1 0.2 0.3 0.4"])
def test_invalid_area_is_not_replaced_by_a_face(monkeypatch, sample, area):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], **{"Panasonic:AFAreaSize": area}))
    result = af.read_af_metadata(sample[0])
    assert result["diagnostics"]["coordinates"]["status"] == "unknown"
    assert result["diagnostics"]["coordinates"]["oriented_bbox_xywh_hypothesis"] is None
    assert result["diagnostics"]["subject_association"]["status"] == "unknown"


@pytest.mark.parametrize("changes", [{"Panasonic:FocusMode": 2}, {"Panasonic:FocusMode": 999},
                                     {"Panasonic:AFAreaMode": "0 225"}, {"Panasonic:AFAreaMode": "64 0"},
                                     {"Panasonic:AFAreaMode": "240 1"}, {"Panasonic:AFMode": 1},
                                     {"Panasonic:AFSubjectDetection": 999}, {"Panasonic:AFSubjectDetection": True}])
def test_manual_unknown_and_unsupported_area_modes_remain_unknown(monkeypatch, sample, changes):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], **changes))
    result = af.read_af_metadata(sample[0])
    assert result["diagnostics"]["mode"]["status"] == "unknown"
    assert result["diagnostics"]["coordinates"]["status"] == "unknown"
    assert result["diagnostics"]["coordinates"]["oriented_center_xy_hypothesis"] is None


@pytest.mark.parametrize("duplicate", ["0.8 0.4", "0.25 0.6", [.25, .6]])
def test_duplicate_instance_preserved_and_not_chosen(monkeypatch, sample, duplicate):
    payload = tags(sample[0], **{"Panasonic:Copy1:AFPointPosition": duplicate})
    mock_tool(monkeypatch, sample, payload=payload)
    result = af.read_af_metadata(sample[0])
    assert result["raw_tags"] == payload
    assert result["diagnostics"]["coordinates"]["center"]["reason"] == "ambiguous_instances"
    assert result["diagnostics"]["coordinates"]["oriented_center_xy_hypothesis"] is None


@pytest.mark.parametrize("duplicate", ["0.1 0.2", [.1, .2]])
def test_identical_af_area_instances_remain_ambiguous(monkeypatch, sample, duplicate):
    payload = tags(sample[0], **{"Panasonic:Copy1:AFAreaSize": duplicate})
    mock_tool(monkeypatch, sample, payload=payload)
    result = af.read_af_metadata(sample[0])
    assert result["raw_tags"] == payload
    coordinates = result["diagnostics"]["coordinates"]
    assert coordinates["area_size"]["reason"] == "ambiguous_instances"
    assert coordinates["oriented_center_xy_hypothesis"] is None
    assert coordinates["oriented_bbox_xywh_hypothesis"] is None


def test_repeated_agreeing_camera_identity_is_supported_but_not_validated(monkeypatch, sample):
    payload = tags(sample[0], **{"IFD0:Copy1:Make": "Panasonic", "IFD0:Copy1:Model": "DC-S5M2"})
    mock_tool(monkeypatch, sample, payload=payload)
    result = af.read_af_metadata(sample[0])
    assert result["status"] == "not_validated"
    assert result["raw_tags"] == payload
    assert result["diagnostics"]["camera"]["is_s5m2_candidate"] is True


def test_conflicting_camera_make_has_no_identity_consensus(monkeypatch, sample):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], **{"IFD0:Copy1:Make": "Canon"}))
    result = af.read_af_metadata(sample[0])
    assert result["status"] == "unsupported"
    assert result["diagnostics"]["camera"]["make_issue"] == "ambiguous_instances"


def test_arbitrary_make_substring_not_camera_identity(monkeypatch, sample):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], **{"IFD0:Make": "Definitely not Panasonic"}))
    assert af.read_af_metadata(sample[0])["status"] == "unsupported"


def test_conflicting_model_is_never_claimed_s5m2(monkeypatch, sample):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], **{"IFD0:Copy1:Model": "DMC-LX3"}))
    result = af.read_af_metadata(sample[0])
    assert result["status"] == "not_validated"
    assert result["diagnostics"]["camera"]["model_issue"] == "ambiguous_instances"
    assert result["diagnostics"]["camera"]["is_s5m2_candidate"] is False


@pytest.mark.parametrize("orientation, point", [(1, [.25, .6]), (2, [.75, .6]), (3, [.75, .4]), (4, [.25, .4]),
                                                 (5, [.6, .25]), (6, [.4, .25]), (7, [.4, .75]), (8, [.6, .75])])
@pytest.mark.parametrize("repeated", [False, True])
def test_eight_orientation_math_is_diagnostic_only(monkeypatch, sample, orientation, point, repeated):
    payload = tags(sample[0], **{"IFD0:Orientation": orientation})
    if repeated:
        payload["IFD0:Copy2:Orientation"] = str(orientation)
        payload["PanasonicRaw:Orientation"] = float(orientation)
    mock_tool(monkeypatch, sample, payload=payload)
    result = af.read_af_metadata(sample[0])
    assert result["status"] == "not_validated"
    assert result["raw_tags"] == payload
    assert result["usable_for_subject_selection"] is False
    assert result["usable_for_keep_reject"] is False
    diagnostics = result["diagnostics"]
    assert diagnostics["coordinates"]["oriented_center_xy_hypothesis"] == pytest.approx(point)
    assert diagnostics["coordinates"]["oriented_bbox_xywh_hypothesis"] == pytest.approx(
        af.orient_normalized_box_hypothesis([.2, .5, .1, .2], orientation))
    assert diagnostics["orientation"]["status"] == "not_validated"
    assert diagnostics["orientation"]["instance_resolution"] == (
        "agreeing_valid_main_frame_instances" if repeated else "single_main_frame_instance")
    assert diagnostics["orientation"]["coordinate_frame_validated"] is False
    assert diagnostics["coordinates"]["coordinate_frame_validated"] is False
    assert diagnostics["subject_association"]["status"] == "unknown"


@pytest.mark.parametrize("orientation, expected", [(1, [.1, .2, .3, .4]), (2, [.6, .2, .3, .4]),
    (3, [.6, .4, .3, .4]), (4, [.1, .4, .3, .4]), (5, [.2, .1, .4, .3]),
    (6, [.4, .1, .4, .3]), (7, [.4, .6, .4, .3]), (8, [.2, .6, .4, .3])])
def test_box_orientation_uses_four_corners(orientation, expected):
    assert af.orient_normalized_box_hypothesis([.1, .2, .3, .4], orientation) == pytest.approx(expected)


@pytest.mark.parametrize("orientation", [0, 9, True, 1.5, None, "unknown", "1.0000000000000001"])
def test_invalid_orientation_no_guessed_transform(monkeypatch, sample, orientation):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], **{"IFD0:Orientation": orientation}))
    result = af.read_af_metadata(sample[0])
    assert result["diagnostics"]["orientation"]["status"] == "unknown"
    assert result["diagnostics"]["coordinates"]["oriented_center_xy_hypothesis"] is None
    with pytest.raises(ValueError):
        af.orient_normalized_point_hypothesis([.5, .5], orientation)


@pytest.mark.parametrize("secondary", [{"IFD1:Orientation": 6},
    {"IFD1:Orientation": 1, "IFD1:Copy1:Orientation": "1", "XMP-tiff:Orientation": 1,
     "Orientation": 1}])
def test_thumbnail_orientation_not_used_for_original(monkeypatch, sample, secondary):
    payload = tags(sample[0])
    del payload["IFD0:Orientation"]
    payload.update(secondary)
    mock_tool(monkeypatch, sample, payload=payload)
    diagnostics = af.read_af_metadata(sample[0])["diagnostics"]
    assert diagnostics["orientation"]["reason"] == "missing"
    assert diagnostics["orientation"]["main_frame_fields"] == {}
    assert diagnostics["orientation"]["raw_fields"] == secondary
    assert diagnostics["orientation"]["instance_resolution"] is None
    assert diagnostics["coordinates"]["oriented_center_xy_hypothesis"] is None
    assert diagnostics["coordinates"]["oriented_bbox_xywh_hypothesis"] is None


@pytest.mark.parametrize("values", [(1, 1), ("1", "1"), ("8", 8.0), ("8.0", "8e0")])
@pytest.mark.parametrize("reverse", [False, True])
def test_agreeing_main_orientation_preserves_raw_instances_and_sources(monkeypatch, sample, values, reverse):
    primary, duplicate = values
    payload = tags(sample[0], **{"IFD0:Orientation": primary,
                                "IFD0:Copy2:Orientation": duplicate,
                                "IFD1:Copy1:Orientation": primary})
    if reverse:
        payload = dict(reversed(list(payload.items())))
    mock_tool(monkeypatch, sample, payload=payload)
    result = af.read_af_metadata(sample[0])
    orientation = result["diagnostics"]["orientation"]
    assert result["raw_tags"] == payload
    assert orientation["status"] == "not_validated"
    assert orientation["exif_value"] == int(float(primary))
    assert orientation["raw_fields"] == {"IFD0:Orientation": primary,
                                           "IFD0:Copy2:Orientation": duplicate,
                                           "IFD1:Copy1:Orientation": primary}
    assert orientation["main_frame_fields"] == {"IFD0:Orientation": primary,
                                                  "IFD0:Copy2:Orientation": duplicate}
    assert orientation["instance_resolution"] == "agreeing_valid_main_frame_instances"
    assert orientation["reason"] == "exif_math_only"
    assert orientation["coordinate_frame_validated"] is False


@pytest.mark.parametrize("values", [(1, 8), ("1", "8"), (0, 0), (9, 9), (True, True),
    (True, 1), (None, None), ("unknown", "unknown"), ("1 1", "1 1"), ([1], [1]),
    (1.5, 1.5), (1, "1.0000000000000001"), (1, "NaN"), ("inf", "inf")])
def test_conflicting_or_invalid_main_orientation_instances_remain_unknown(monkeypatch, sample, values):
    payload = tags(sample[0], **{"IFD0:Orientation": values[0],
                                "IFD0:Copy2:Orientation": values[1],
                                "IFD1:Orientation": 1})
    mock_tool(monkeypatch, sample, payload=payload)
    result = af.read_af_metadata(sample[0])
    orientation = result["diagnostics"]["orientation"]
    assert result["raw_tags"] == payload
    assert orientation["status"] == "unknown"
    assert orientation["reason"] == "ambiguous_instances"
    assert orientation["exif_value"] is None
    assert orientation["instance_resolution"] is None
    assert result["diagnostics"]["coordinates"]["oriented_center_xy_hypothesis"] is None
    assert result["diagnostics"]["coordinates"]["oriented_bbox_xywh_hypothesis"] is None


@pytest.mark.parametrize("secondary", ["IFD1:Orientation", "XMP-tiff:Orientation", "IFD0:Copy1:Orientation",
                                      "PanasonicRaw:Orientation"])
@pytest.mark.parametrize("repeated", [False, True])
def test_conflicting_orientation_evidence_never_picks_one(monkeypatch, sample, secondary, repeated):
    payload = tags(sample[0], **{secondary: 6})
    if repeated:
        payload["IFD0:Copy2:Orientation"] = "1"
    mock_tool(monkeypatch, sample, payload=payload)
    diagnostics = af.read_af_metadata(sample[0])["diagnostics"]
    assert diagnostics["orientation"]["status"] == "unknown"
    assert diagnostics["orientation"]["raw_fields"][secondary] == 6
    assert diagnostics["coordinates"]["oriented_center_xy_hypothesis"] is None
    assert diagnostics["coordinates"]["oriented_bbox_xywh_hypothesis"] is None


def test_same_thumbnail_orientation_remains_unvalidated(monkeypatch, sample):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], **{"IFD1:Orientation": 1}))
    orientation = af.read_af_metadata(sample[0])["diagnostics"]["orientation"]
    assert orientation["status"] == "not_validated"
    assert len(orientation["raw_fields"]) == 2


@pytest.mark.parametrize("center, area", [("0 0", "0.1 0.1"), ("1 1", "0.1 0.1"), ("0.1 0.5", "0.3 0.1")])
def test_outside_area_not_clamped(monkeypatch, sample, center, area):
    mock_tool(monkeypatch, sample, payload=tags(sample[0], **{"Panasonic:AFPointPosition": center, "Panasonic:AFAreaSize": area}))
    coordinates = af.read_af_metadata(sample[0])["diagnostics"]["coordinates"]
    assert coordinates["reason"] == "area_extends_outside_frame"
    assert coordinates["raw_bbox_xywh_hypothesis"] is None
    assert coordinates["oriented_center_xy_hypothesis"] is None


@pytest.mark.parametrize("point", [[0, 0], [1, 1], [0, 1], [1, 0]])
def test_normalized_boundary_points_are_math_only(point):
    assert af.orient_normalized_point_hypothesis(point, 1) == point


def test_source_mutation_during_read_rejects_evidence(monkeypatch, sample):
    mock_tool(monkeypatch, sample, effect=lambda: sample[0].write_bytes(b"changed source"))
    result = af.read_af_metadata(sample[0])
    assert result["reason"] == "source_changed_during_read"
    assert result["source"]["unchanged_during_read"] is False
    assert result["raw_tags"] == {}


def test_hash_detects_changed_bytes_even_if_stat_identity_frozen(monkeypatch, sample):
    frozen = af._stat_identity(sample[0].stat())
    monkeypatch.setattr(af, "_stat_identity", lambda _: frozen)
    mock_tool(monkeypatch, sample, effect=lambda: sample[0].write_bytes(b"changed source"))
    assert af.read_af_metadata(sample[0])["reason"] == "source_changed_during_read"


def test_parser_changed_during_read_rejects_evidence(monkeypatch, sample):
    mock_tool(monkeypatch, sample, effect=lambda: sample[1].write_text("modified parser", encoding="utf-8"))
    result = af.read_af_metadata(sample[0])
    assert result["reason"] == "exiftool_changed_during_read"
    assert result["status"] == "error"


def test_oversized_metadata_output_is_controlled(monkeypatch, sample):
    mock_tool(monkeypatch, sample, payload="x" * (af._MAX_OUTPUT + 1))
    result = af.read_af_metadata(sample[0])
    assert result["reason"] == "exiftool_output_too_large"
    assert result["raw_tags"] == {}


def test_repeated_reads_do_not_cache_old_content_direction_or_parser(monkeypatch, sample):
    source, _ = sample
    calls = mock_tool(monkeypatch, sample)
    first = af.read_af_metadata(source)
    source.write_bytes(b"changed original bytes")
    payload = tags(source, **{"IFD0:Orientation": 6, "ExifTool:ExifToolVersion": 13.60})
    second_calls = mock_tool(monkeypatch, sample, payload=payload, version="13.60")
    second = af.read_af_metadata(source)
    assert first["source"]["sha256"] != second["source"]["sha256"]
    assert first["source"]["stat_identity"] != second["source"]["stat_identity"]
    assert first["parser"]["version"] != second["parser"]["version"]
    assert second["diagnostics"]["orientation"]["exif_value"] == 6
    assert second["diagnostics"]["coordinates"]["oriented_center_xy_hypothesis"] == pytest.approx([.4, .25])
    assert len(calls) == len(second_calls) == 2


def test_missing_tool_is_not_cached(monkeypatch, sample):
    monkeypatch.setattr(af.shutil, "which", lambda _: None)
    assert af.read_af_metadata(sample[0])["status"] == "error"
    calls = mock_tool(monkeypatch, sample)
    assert af.read_af_metadata(sample[0])["status"] == "not_validated"
    assert len(calls) == 2


def test_hash_streams_and_no_write_file_modes(monkeypatch, sample):
    source = sample[0]
    source.write_bytes(b"x" * (af._CHUNK_SIZE * 2 + 17))
    original_open = Path.open
    reads = []
    class Stream:
        def __enter__(self):
            self.stream = original_open(source, "rb")
            return self
        def __exit__(self, *args):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def read(self, count):
            reads.append(count)
            return self.stream.read(count)
    def read_only(path, mode="r", *args, **kwargs):
        assert mode == "rb" and path == source
        return Stream()
    monkeypatch.setattr(Path, "open", read_only)
    mock_tool(monkeypatch, sample)
    assert af.read_af_metadata(source)["status"] == "not_validated"
    assert reads == [af._CHUNK_SIZE] * 8


@pytest.mark.parametrize('present', [True, False])
def test_nikon_dispatch_preserves_raw_and_no_quality_authority(monkeypatch, sample, present):
    source, tool = sample
    raw = {'SourceFile': str(source.resolve()), 'ExifTool:ExifToolVersion': 13.59,
           'IFD0:Make': 'NIKON CORPORATION', 'IFD0:Model': 'NIKON Z5_2', 'IFD0:Orientation': 1}
    if present:
        raw.update({'Nikon:AFInfo2Version': '0402', 'Nikon:AFCoordinatesAvailable': 1,
                    'Nikon:AFAreaXPosition': 3048, 'Nikon:AFAreaYPosition': 1671})
    before = source.read_bytes()
    mock_tool(monkeypatch, sample, payload=raw)
    result = af.read_af_metadata(source, exiftool_path=tool)
    assert result['status'] == ('not_validated' if present else 'missing')
    assert result['diagnostics']['camera']['vendor'] == 'nikon'
    assert result['raw_tags'] == raw
    assert result['usable_for_subject_selection'] is False
    assert result['usable_for_keep_reject'] is False
    assert source.read_bytes() == before
