"""Synthetic geometry and mocked reader tests, never real-camera validation."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time

import pytest

from ai_cull_assistant import af_metadata as af
from ai_cull_assistant import af_overlay as overlay


@pytest.fixture
def sample(tmp_path):
    source = tmp_path / "synthetic 原件.RW2"
    source.write_bytes(b"not a real RAW; synthetic fixture only")
    tool = tmp_path / "exiftool"
    tool.write_bytes(b"not executed; mocked parser")
    source_identity = {"path": str(source.resolve()),
                       "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                       "stat_identity": af._stat_identity(source.stat()),
                       "unchanged_during_read": True}
    parser_identity = {"name": "ExifTool", "path": str(tool.resolve()),
                       "version": "13.59", "adapter_version": af.ADAPTER_VERSION,
                       "stat_identity": af._stat_identity(tool.stat())}
    record = {"status": "not_validated", "source": source_identity,
              "parser": parser_identity,
              "reason": "real_camera_coordinate_mapping_not_validated",
              "usable_for_subject_selection": False, "usable_for_keep_reject": False}
    return source, tool, record


@pytest.fixture
def validated(sample):
    """Invented future protocol data; no camera output currently passes it."""
    record = deepcopy(sample[2])
    record["status"] = "validated"
    record["validated_display_mapping"] = {
        "status": "validated", "validation_id": "SYNTHETIC-TEST-ONLY",
        "coordinate_frame": overlay.DISPLAY_FRAME, "exif_transposed": True,
        "source": deepcopy(record["source"]), "parser": deepcopy(record["parser"]),
        "preview_id": "synthetic-display-artifact-sha256-and-crop-v1",
        "frame_size": [1000, 500], "center_xy": [0.25, 0.6],
        "area_size_wh": [0.2, 0.4],
    }
    preview = overlay.PreviewFrame(deepcopy(record["source"]),
                                   record["validated_display_mapping"]["preview_id"],
                                   (1000, 500), True)
    return record, preview


def project(validated, rect=(20, 40, 420, 240)):
    return overlay.build_overlay_geometry(*validated, rect)


def assert_rejected(geometry, reason=None):
    assert geometry.status != "validated"
    assert geometry.center is None
    assert geometry.box is None
    assert geometry.clip_rect is None
    if reason is not None:
        assert geometry.reason == reason


def test_maps_normalized_display_frame_into_image_letterbox(validated):
    result = project(validated)
    assert result.status == "validated"
    assert result.center == pytest.approx((120, 160))
    assert result.box == pytest.approx((80, 120, 160, 200))
    assert result.clip_rect == (20, 40, 420, 240)
    # The canvas/cell can be larger; only the image rectangle participates.
    moved = project(validated, (120, 140, 520, 340))
    assert moved.center == pytest.approx((220, 260))
    assert moved.box == pytest.approx((180, 220, 260, 300))


def test_does_not_rotate_already_exif_transposed_coordinates(validated):
    record, preview = validated
    record["diagnostics"] = {"orientation": {"exif_value": 6},
                             "coordinates": {"oriented_center_xy_hypothesis": [0.9, 0.9]}}
    assert project((record, preview)).center == pytest.approx((120, 160))


@pytest.mark.parametrize("mode", ["missing", "null"])
def test_center_without_validated_size_is_cross_only(validated, mode):
    mapping = validated[0]["validated_display_mapping"]
    if mode == "missing":
        del mapping["area_size_wh"]
    else:
        mapping["area_size_wh"] = None
    result = project(validated)
    assert result.status == "validated"
    assert result.center == pytest.approx((120, 160))
    assert result.box is None


@pytest.mark.parametrize("status", ["not_validated", "unknown", "error", "missing", "unsupported", None])
def test_unvalidated_record_never_draws_even_with_hypotheses(validated, status):
    record, _ = validated
    record["status"] = status
    record["diagnostics"] = {"coordinates": {
        "status": "not_validated", "oriented_center_xy_hypothesis": [0.25, 0.6],
        "oriented_bbox_xywh_hypothesis": [0.15, 0.4, 0.2, 0.4]}}
    assert_rejected(project(validated), "metadata_not_validated")


@pytest.mark.parametrize("mapping", [None, {}, {"status": "not_validated"}, {"status": True}])
def test_explicit_validated_mapping_required(validated, mapping):
    validated[0]["validated_display_mapping"] = mapping
    assert_rejected(project(validated), "display_mapping_not_validated")


@pytest.mark.parametrize("key,value,reason", [
    ("validation_id", "", "validation_evidence_missing"),
    ("validation_id", None, "validation_evidence_missing"),
    ("coordinate_frame", "sensor_normalized", "display_frame_not_validated"),
    ("exif_transposed", False, "display_frame_not_validated"),
    ("exif_transposed", 1, "display_frame_not_validated"),
    ("preview_id", "other-crop", "preview_identity_mismatch"),
    ("frame_size", [500, 1000], "preview_identity_mismatch"),
    ("frame_size", [1000.0, 500], "preview_identity_mismatch"),
    ("frame_size", [True, 500], "preview_identity_mismatch"),
])
def test_mapping_contract_rejects_ambiguous_frame(validated, key, value, reason):
    validated[0]["validated_display_mapping"][key] = value
    assert_rejected(project(validated), reason)


def test_missing_preview_proof_never_uses_dimensions_as_proof(validated):
    assert_rejected(overlay.build_overlay_geometry(validated[0], None, (20, 40, 420, 240)),
                    "preview_binding_missing")
    assert_rejected(project((validated[0], replace(validated[1], exif_transposed=False))),
                    "display_frame_not_validated")


@pytest.mark.parametrize("target", ["record", "preview", "mapping"])
@pytest.mark.parametrize("component", ["path", "sha256", "stat_identity"])
def test_source_requires_exact_path_sha_and_stat(validated, target, component):
    record, preview = validated
    source = {"record": record["source"], "preview": preview.source,
              "mapping": record["validated_display_mapping"]["source"]}[target]
    if component == "path":
        # Paired JPEG and RAW must never be conflated, even with the same stem.
        source[component] = str(Path(source[component]).with_suffix(".JPG"))
    elif component == "sha256":
        source[component] = "0" * 64
    else:
        source[component]["mtime_ns"] += 1
    assert_rejected(project(validated), "source_identity_mismatch")


@pytest.mark.parametrize("digest", [None, "", "a" * 63, "g" * 64, True])
def test_missing_or_malformed_sha_is_not_proof(validated, digest):
    validated[0]["source"]["sha256"] = digest
    assert_rejected(project(validated), "source_identity_mismatch")


def test_source_changed_while_reading_rejected(validated):
    validated[0]["source"]["unchanged_during_read"] = False
    assert_rejected(project(validated), "source_identity_mismatch")


@pytest.mark.parametrize("component,value", [("version", "14.0"), ("adapter_version", "different-adapter-version"),
                                              ("name", "OtherParser"), ("path", "/another/exiftool")])
def test_parser_identity_must_match(validated, component, value):
    validated[0]["validated_display_mapping"]["parser"][component] = value
    assert_rejected(project(validated), "parser_identity_mismatch")


@pytest.mark.parametrize("center", [None, [], [0.1], [0.1, 0.2, 0.3], [0, 0.5], [1, 0.5],
                                     [-0.01, 0.5], [1.01, 0.5], [0.5, 0], [0.5, 1],
                                     [float("nan"), 0.5], [0.5, float("inf")],
                                     [0.5, True], ["0.5", 0.5], [10**1000, 0.5]])
def test_invalid_or_boundary_center_never_clamped(validated, center):
    validated[0]["validated_display_mapping"]["center_xy"] = center
    assert_rejected(project(validated), "invalid_center")


@pytest.mark.parametrize("area", [[], [0.1], [0.1, 0.2, 0.3], [0, 0.1], [-0.1, 0.1],
                                   [1.01, 0.1], [True, 0.1], ["0.1", 0.1],
                                   [float("nan"), 0.1], [0.1, float("inf")]])
def test_invalid_area_does_not_invent_box(validated, area):
    validated[0]["validated_display_mapping"]["area_size_wh"] = area
    assert_rejected(project(validated), "invalid_area_size")


def test_outside_area_is_rejected_not_clipped(validated):
    validated[0]["validated_display_mapping"]["area_size_wh"] = [0.8, 0.4]
    assert_rejected(project(validated), "area_outside_frame")


@pytest.mark.parametrize("rect", [(0, 0, 0, 100), (0, 0, 100, 0), (10, 0, 0, 100),
                                   (0, 0, float("nan"), 100), (0, 0, 100, float("inf")),
                                   (0, False, 100, 50), (0, 0, 100), (0, 0, 10**1000, 100)])
def test_invalid_image_rectangle_rejected(validated, rect):
    assert_rejected(project(validated, rect), "invalid_image_rectangle")


def test_wrong_display_aspect_rejected_and_pixel_rounding_allowed(validated):
    assert_rejected(project(validated, (0, 0, 200, 200)), "display_aspect_mismatch")
    assert project(validated, (0, 0, 401, 200)).status == "validated"


def test_frame_size_overflow_safely_rejected(validated):
    record, preview = validated
    record["validated_display_mapping"]["frame_size"] = [10**1000, 500]
    preview = replace(preview, frame_size=(10**1000, 500))
    assert_rejected(project((record, preview)), "preview_identity_mismatch")


def test_near_edge_and_tiny_thumbnail_keep_valid_center_and_clip_rect(validated):
    mapping = validated[0]["validated_display_mapping"]
    mapping["center_xy"] = [0.001, 0.999]
    mapping["area_size_wh"] = None
    result = project(validated, (20, 40, 24, 42))
    assert result.status == "validated"
    assert result.center == pytest.approx((20.004, 41.998))
    assert result.clip_rect == (20, 40, 24, 42)
    assert result.box is None


def test_area_that_collapses_at_float_precision_is_not_a_rectangle(validated):
    validated[0]["validated_display_mapping"]["area_size_wh"] = [5e-324, 5e-324]
    assert_rejected(project(validated), "nonfinite_projection")


def test_projection_never_changes_source_record_or_decision_flags(validated):
    original = deepcopy(validated[0])
    project(validated)
    assert validated[0] == original
    assert validated[0]["usable_for_subject_selection"] is False
    assert validated[0]["usable_for_keep_reject"] is False


def wait_result(loader, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = loader.pop_completed()
        if result:
            return result
        time.sleep(0.005)
    pytest.fail("worker did not produce a completion")


def test_loader_reads_on_worker_not_request_thread(sample):
    source, _, record = sample
    main_id = threading.get_ident()
    calls = []
    def reader(path, **kwargs):
        calls.append((path, kwargs, threading.get_ident()))
        return deepcopy(record)
    loader = overlay.AfOverlayLoader(reader=reader, timeout=0.5, exiftool_path=sample[1])
    try:
        token = loader.request(source)
        assert wait_result(loader) == [(token, record)]
        assert calls[0][:2] == (str(source), {"exiftool_path": sample[1], "timeout": 0.5})
        assert calls[0][2] != main_id
        assert loader.pop_completed() == []
        assert loader._thread.daemon is True
    finally:
        loader.close()


def test_loader_bounded_latest_request_discards_running_and_queued(sample):
    source, _, record = sample
    started, release = threading.Event(), threading.Event()
    calls = []
    def reader(path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            started.set()
            assert release.wait(3)
        return deepcopy(record)
    loader = overlay.AfOverlayLoader(reader=reader)
    try:
        first = loader.request(source)
        assert started.wait(3)
        for i in range(100):
            latest = loader.request(source)
        assert latest > first
        assert loader._pending == (latest, str(source))
        release.set()
        assert wait_result(loader) == [(latest, record)]
        assert calls == [str(source), str(source)]
    finally:
        release.set()
        loader.close()


def test_invalidate_drops_running_and_pending_then_loader_can_resume(sample):
    source, _, record = sample
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def reader(path, **kwargs):
        if not started.is_set():
            started.set()
            assert release.wait(3)
        finished.set()
        return deepcopy(record)
    loader = overlay.AfOverlayLoader(reader=reader)
    try:
        first = loader.request(source)
        assert started.wait(3)
        loader.request(source)
        loader.invalidate()
        release.set()
        assert finished.wait(3)
        assert loader.pop_completed() == []
        latest = loader.request(source)
        assert latest > first
        assert wait_result(loader) == [(latest, record)]
    finally:
        release.set()
        loader.close()


def test_close_is_nonblocking_with_running_read_and_late_result_dropped(sample):
    source, _, record = sample
    started, release = threading.Event(), threading.Event()
    def reader(path, **kwargs):
        started.set()
        assert release.wait(3)
        return deepcopy(record)
    loader = overlay.AfOverlayLoader(reader=reader)
    try:
        loader.request(source)
        assert started.wait(3)
        loader.request(source)
        loader.close()
        assert loader._thread.is_alive()  # The blocked read was not joined.
        assert loader._pending is None
        assert loader.pop_completed() == []
        with pytest.raises(RuntimeError, match="closed"):
            loader.request(source)
        loader.invalidate()
        loader.close()  # Repeated teardown is safe.
    finally:
        release.set()
        loader._thread.join(3)  # Tests only; production close never joins.
    assert not loader._thread.is_alive()
    assert loader.pop_completed() == []


def test_read_failure_does_not_kill_worker(sample):
    source, _, record = sample
    count = 0
    def reader(path, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            raise OSError("synthetic failure")
        return deepcopy(record)
    loader = overlay.AfOverlayLoader(reader=reader)
    try:
        loader.request(source)
        assert wait_result(loader)[0][1]["reason"] == "metadata_read_error"
        token = loader.request(source)
        assert wait_result(loader) == [(token, record)]
    finally:
        loader.close()


@pytest.mark.parametrize("replacement", ["source", "parser"])
def test_replacement_during_read_is_rejected(sample, replacement):
    source, tool, record = sample
    def reader(path, **kwargs):
        (source if replacement == "source" else tool).write_bytes(b"replaced fixture bytes")
        return deepcopy(record)
    loader = overlay.AfOverlayLoader(reader=reader)
    try:
        loader.request(source)
        result = wait_result(loader)[0][1]
        assert result["status"] == "error"
        assert result["reason"] == ("source_changed_after_read" if replacement == "source"
                                    else "exiftool_changed_after_read")
        assert "validated_display_mapping" not in result
    finally:
        loader.close()


def test_different_source_cannot_be_returned_for_same_stem_jpeg(sample):
    source, _, record = sample
    jpeg = source.with_suffix(".JPG")
    jpeg.write_bytes(source.read_bytes())
    loader = overlay.AfOverlayLoader(reader=lambda *a, **kw: deepcopy(record))
    try:
        loader.request(jpeg)
        assert wait_result(loader)[0][1]["reason"] == "source_identity_mismatch"
    finally:
        loader.close()


def test_no_success_or_failure_cache_and_parser_identity_refreshes(sample):
    source, tool, record = sample
    calls = []
    def reader(path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            return {"status": "error", "reason": "exiftool_unavailable"}
        fresh = deepcopy(record)
        fresh["parser"]["stat_identity"] = af._stat_identity(tool.stat())
        fresh["source"] = af._source_identity(source)
        fresh["source"]["unchanged_during_read"] = True
        return fresh
    loader = overlay.AfOverlayLoader(reader=reader)
    try:
        loader.request(source)
        assert wait_result(loader)[0][1]["status"] == "error"
        loader.request(source)
        second = wait_result(loader)[0][1]
        tool.write_bytes(b"different parser version fixture")
        source.write_bytes(b"different source fixture")
        loader.request(source)
        third = wait_result(loader)[0][1]
        assert second["source"]["sha256"] != third["source"]["sha256"]
        assert second["parser"]["stat_identity"] != third["parser"]["stat_identity"]
        assert len(calls) == 3
    finally:
        loader.close()


def test_real_adapter_contract_stays_unvalidated_and_all_io_is_worker_only(sample, monkeypatch):
    source, tool, _ = sample
    thread_ids, calls = [], []
    original_hash = af._source_identity
    original_stat = af._stat_identity
    def hashing(path):
        thread_ids.append(threading.get_ident())
        return original_hash(path)
    def stat_identity(value):
        thread_ids.append(threading.get_ident())
        return original_stat(value)
    def run(argv, **kwargs):
        calls.append(argv)
        thread_ids.append(threading.get_ident())
        if argv[-1] == "-ver":
            return subprocess.CompletedProcess(argv, 0, "13.59\n", "")
        raw = {"SourceFile": str(source.resolve()), "ExifTool:ExifToolVersion": 13.59,
               "IFD0:Make": "Panasonic", "IFD0:Model": "DC-S5M2", "IFD0:Orientation": 6,
               "Panasonic:AFPointPosition": "0.25 0.6", "Panasonic:AFAreaSize": "0.2 0.4",
               "Panasonic:FocusMode": 6, "Panasonic:AFAreaMode": "16 0"}
        return subprocess.CompletedProcess(argv, 0, json.dumps([raw]), "")
    monkeypatch.setattr(af, "_source_identity", hashing)
    monkeypatch.setattr(af, "_stat_identity", stat_identity)
    monkeypatch.setattr(af.subprocess, "run", run)
    loader = overlay.AfOverlayLoader(exiftool_path=tool)
    try:
        token = loader.request(source)
        results = wait_result(loader)
        assert results[0][0] == token
        result = results[0][1]
        assert result["status"] == "not_validated"
        assert_rejected(overlay.build_overlay_geometry(result, None, (0, 0, 400, 200)))
        assert "未验证" in overlay.af_status_text(result)
        assert calls[1] == [str(tool.resolve()), *af.READ_OPTIONS,
                            *("-" + tag for tag in af.READ_TAGS), str(source.resolve())]
        assert thread_ids and all(value != threading.get_ident() for value in thread_ids)
    finally:
        loader.close()


@pytest.mark.parametrize("record,expected", [
    (None, "信息不可用"), ({"status": "not_validated"}, "未验证"),
    ({"status": "validated"}, "仍需匹配预览"), ({"status": "missing"}, "未发现"),
    ({"status": "unsupported"}, "暂不支持"),
    ({"status": "error", "reason": "exiftool_unavailable"}, "未找到 ExifTool"),
    ({"status": "error", "reason": "exiftool_read_warning"}, "元数据有警告，暂不绘制"),
    ({"status": "error", "reason": "source_changed_after_read"}, "原件已变化"),
    ({"status": "error", "reason": "source_unavailable"}, "原件无法读取"),
    ({"status": "error", "reason": "exiftool_metadata_timeout"}, "读取超时"),
])
def test_status_is_diagnostic_only(record, expected):
    text = overlay.af_status_text(record)
    assert expected in text
    assert not any(word in text for word in ("命中", "清晰", "保留", "剔除", "人物"))
