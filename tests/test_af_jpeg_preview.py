"""Synthetic binding/geometry/lifecycle checks, never camera calibration."""
from copy import deepcopy
import hashlib
from pathlib import Path
import threading
import time

from PIL import Image
import pytest

from ai_cull_assistant import af_diagnostic as diagnostic
from ai_cull_assistant import af_jpeg_preview as preview
from ai_cull_assistant import af_metadata as af


@pytest.fixture
def prepared(tmp_path):
    source = tmp_path / "原片 -execute.JPG"
    source.write_bytes(b"synthetic encoded JPEG identity; preparer is mocked")
    parser = tmp_path / "exiftool"
    parser.write_bytes(b"synthetic parser identity; never executed")
    images = []

    def make(*, orientation=1, center=(.25, .6), area=(.1, .2), source_path=None,
             stored_size=(6000, 4000), changes=None):
        path = source_path or source
        raw = {"SourceFile": str(path.resolve()), "ExifTool:ExifToolVersion": 13.59,
               "IFD0:Make": "Panasonic", "IFD0:Model": "DC-S5M2", "IFD0:Orientation": orientation,
               "Panasonic:FocusMode": 6, "Panasonic:AFAreaMode": "16 0",
               "Panasonic:AFPointPosition": list(center),
               "Panasonic:PanasonicImageWidth": stored_size[0], "Panasonic:PanasonicImageHeight": stored_size[1],
               "ExifIFD:ExifImageWidth": stored_size[0], "ExifIFD:ExifImageHeight": stored_size[1]}
        if area is not None:
            raw["Panasonic:AFAreaSize"] = list(area)
        raw.update(changes or {})
        source_identity = af._source_identity(path)
        metadata = {"status": "not_validated", "reason": "real_camera_coordinate_mapping_not_validated",
                    "source": {**source_identity, "unchanged_during_read": True},
                    "parser": {"name": "ExifTool", "path": str(parser.resolve()), "version": "13.59",
                               "adapter_version": af.ADAPTER_VERSION, "stat_identity": af._stat_identity(parser.stat())},
                    "raw_tags": raw, "diagnostics": af._diagnostics(raw),
                    "usable_for_subject_selection": False, "usable_for_keep_reject": False}
        frame_size = stored_size[::-1] if orientation >= 5 else stored_size
        image = Image.new("RGB", (128, 192) if orientation >= 5 else (192, 128), (29, 49, 79))
        images.append(image)
        raster = {"raster_size": list(image.size), "display_frame_size": list(frame_size),
                  "decoded_source_size": list(stored_size), "pixel_mode": "RGB", "crop": "full_frame",
                  "exif_transposed": True, "exif_transpose_count": 1, "exif_orientation": orientation,
                  "metadata_orientation": orientation, "orientation_agrees": True,
                  "annotation_applied": False, "decoded_bytes_sha256": source_identity["sha256"],
                  "clean_pixels_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
                  "decode_stat_before": deepcopy(source_identity["stat_identity"]),
                  "decode_stat_after": deepcopy(source_identity["stat_identity"]),
                  "dimension_evidence": diagnostic._dimension_evidence(metadata, list(stored_size)),
                  "processing_evidence": diagnostic._processing_evidence(metadata),
                  "decoder": {"name": "Pillow", "format": "JPEG", "primary_frame_index": 0,
                              "container_frame_count": 1, "encoded_snapshot": True}}
        binding = {"before": deepcopy(source_identity), "after": deepcopy(source_identity),
                   "source_unchanged": True, "metadata_source_matches": True, "parser_unchanged": True}
        geometry = diagnostic._geometry(metadata, binding, raster)
        geometry.update(center_drawn=False, box_drawn=False)
        report = {"schema": diagnostic.SCHEMA_VERSION, "status": "not_validated", "metadata": metadata,
                  "binding": binding, "preview": raster, "geometry": geometry,
                  "automatic_decisions_enabled": False,
                  "usable_for_subject_selection": False, "usable_for_keep_reject": False}
        return image, report

    yield source, parser, make, images
    for image in images:
        image.close()


def bundle_for(prepared, **kwargs):
    image, report = prepared[2](**kwargs)
    return preview._bind_prepared(str(prepared[0]), image, report)


def project(bundle, rect=None):
    if rect is None:
        w, h = bundle["clean_image"].size
        rect = (20, 40, 20+w*2, 40+h*2)
    return preview.project_jpeg_diagnostic(bundle, rect)


def assert_hidden(value, reason=None):
    assert value.status == "hidden"
    assert value.center is None and value.box is None and value.clip_rect is None
    if reason is not None:
        assert value.reason == reason


def assert_closed(image):
    with pytest.raises(ValueError, match="closed"):
        image.getpixel((0, 0))


def is_closed(image):
    try:
        image.getpixel((0, 0))
        return False
    except ValueError:
        return True


def wait_result(loader, timeout=3):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        result = loader.pop_completed()
        if result:
            return result
        time.sleep(.005)
    pytest.fail("AF JPEG worker produced no completion")


def wait_until(predicate, timeout=3):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    pytest.fail("AF JPEG condition timed out")


@pytest.mark.parametrize("orientation, center, box", [
    (1, (.25, .6), (.2, .5, .1, .2)), (2, (.75, .6), (.7, .5, .1, .2)),
    (3, (.75, .4), (.7, .3, .1, .2)), (4, (.25, .4), (.2, .3, .1, .2)),
    (5, (.6, .25), (.5, .2, .2, .1)), (6, (.4, .25), (.3, .2, .2, .1)),
    (7, (.4, .75), (.3, .7, .2, .1)), (8, (.6, .75), (.5, .7, .2, .1)),
])
def test_eight_prepared_orientations_are_projected_without_rotation(prepared, orientation, center, box, monkeypatch):
    bundle = bundle_for(prepared, orientation=orientation)
    monkeypatch.setattr(af, "orient_normalized_point_hypothesis", lambda *a: pytest.fail("second EXIF rotation"))
    monkeypatch.setattr(af, "orient_normalized_box_hypothesis", lambda *a: pytest.fail("second EXIF rotation"))
    result = project(bundle)
    w, h = bundle["clean_image"].size
    assert result.status == "diagnostic"
    assert result.center == pytest.approx((20+center[0]*w*2, 40+center[1]*h*2))
    x, y, bw, bh = box
    assert result.box == pytest.approx((20+x*w*2, 40+y*h*2, 20+(x+bw)*w*2, 40+(y+bh)*h*2))
    assert result.clip_rect == (20, 40, 20+w*2, 40+h*2)
    assert bundle["metadata"]["status"] == "not_validated"
    assert bundle["geometry"]["status"] == "not_validated"
    assert bundle["metadata"]["usable_for_keep_reject"] is False
    assert "validated_display_mapping" not in bundle["metadata"]
    assert "未校准" in bundle["status_text"]


@pytest.mark.parametrize("center", [(0, 0), (0, 1), (1, 0), (1, 1), (.001, .999)])
def test_boundary_cross_is_not_clamped_and_has_image_clip(prepared, center):
    bundle = bundle_for(prepared, center=center, area=None)
    result = project(bundle, (10, 20, 16, 24))
    assert result.status == "diagnostic"
    assert result.center == pytest.approx((10+center[0]*6, 20+center[1]*4))
    assert result.box is None
    assert result.clip_rect == (10, 20, 16, 24)


def test_full_frame_boundary_box_is_preserved(prepared):
    result = project(bundle_for(prepared, center=(.5, .5), area=(1, 1)))
    assert result.status == "diagnostic"
    assert result.box == result.clip_rect


@pytest.mark.parametrize("center", [(float("nan"), .5), (.5, float("inf")), (False, .5), (-.01, .5), (1.01, .5)])
def test_invalid_normalized_center_never_gets_proof(prepared, center):
    bundle = bundle_for(prepared, center=center, area=None)
    assert_hidden(project(bundle))


@pytest.mark.parametrize("area", [(0, .1), (-.1, .1), (.9, .8), (float("nan"), .1)])
def test_invalid_area_never_gets_proof(prepared, area):
    assert_hidden(project(bundle_for(prepared, area=area)))


@pytest.mark.parametrize("rect", [(0, 0, 0, 100), (0, 0, 100, 0), (10, 0, 0, 100),
                                   (0, 0, float("nan"), 100), (0, 0, 100, float("inf")),
                                   (0, False, 100, 50), (0, 0, 100), (0, 0, 10**1000, 100)])
def test_invalid_display_rectangle(prepared, rect):
    assert_hidden(project(bundle_for(prepared), rect), "invalid_image_rectangle")


def test_aspect_rejects_stretch_but_allows_one_pixel_rounding(prepared):
    bundle = bundle_for(prepared)
    assert project(bundle, (0, 0, 301, 200)).status == "diagnostic"
    assert project(bundle, (0, 0, 300, 201)).status == "diagnostic"
    assert_hidden(project(bundle, (0, 0, 300, 205)), "display_aspect_mismatch")
    assert_hidden(project(bundle, (0, 0, 200, 200)), "display_aspect_mismatch")


def test_matching_dimensions_or_pixel_copy_is_not_source_binding(prepared):
    bundle = bundle_for(prepared)
    assert_hidden(project(dict(bundle)), "bound_diagnostic_unavailable")
    image = bundle["clean_image"]
    bundle["clean_image"] = image.copy()
    try:
        assert_hidden(project(bundle), "preview_binding_mismatch")
    finally:
        bundle["clean_image"].close()
        bundle["clean_image"] = image
    image.putpixel((0, 0), (255, 0, 0))
    assert_hidden(project(bundle), "preview_binding_mismatch")


@pytest.mark.parametrize("target", ["metadata", "source", "parser", "geometry", "preview", "request_path"])
def test_evidence_cannot_be_swapped_after_preparation(prepared, target):
    bundle = bundle_for(prepared)
    if target == "metadata":
        bundle["metadata"]["status"] = "validated"
    elif target == "source":
        bundle["provenance"]["source"]["sha256"] = "0"*64
    elif target == "parser":
        bundle["provenance"]["parser"]["version"] = "999"
    elif target == "geometry":
        bundle["geometry"]["center_xy_hypothesis"] = [.9, .9]
    elif target == "preview":
        bundle["provenance"]["preview"]["crop"] = "different crop"
    else:
        bundle["request_path"] = "different.jpg"
    assert_hidden(project(bundle), "preview_binding_mismatch")


def test_closed_popped_image_fails_closed(prepared):
    bundle = bundle_for(prepared)
    bundle["clean_image"].close()
    assert_hidden(project(bundle), "preview_binding_mismatch")


@pytest.mark.parametrize("changes", [{"IFD0:Model": "DC-S5M2X"}, {"IFD0:Model": "DC-GH6"},
                                      {"Panasonic:AFAreaMode": "16 16"}, {"Panasonic:AFAreaMode": "128 0"},
                                      {"Panasonic:FocusMode": 2}, {"ExifIFD:ExifImageWidth": 5998},
                                      {"Panasonic:PanasonicImageHeight": 3998}])
def test_profile_does_not_expand_beyond_observed_s5m2_mode_dimensions(prepared, changes):
    bundle = bundle_for(prepared, changes=changes)
    assert_hidden(project(bundle))
    assert bundle["clean_image"] is not None
    assert bundle["metadata"]["status"] == "not_validated"


def test_different_resolution_retains_clean_pixels_without_overlay(prepared):
    bundle = bundle_for(prepared, stored_size=(3000, 2000))
    assert bundle["reason"] == "unsupported_jpeg_dimensions"
    assert_hidden(project(bundle))


@pytest.mark.parametrize("target", ["decoded_bytes_sha256", "clean_pixels_sha256", "exif_transpose_count", "crop",
                                     "annotation_applied", "raster_size", "primary_frame_index", "encoded_snapshot"])
def test_invalid_pixel_provenance_closes_image(prepared, target):
    image, report = prepared[2]()
    raster = report["preview"]
    changes = {"decoded_bytes_sha256": "0"*64, "clean_pixels_sha256": "0"*64,
               "exif_transpose_count": 2, "crop": "thumbnail", "annotation_applied": True,
               "raster_size": [1, 1], "primary_frame_index": 1, "encoded_snapshot": False}
    (raster["decoder"] if target in {"primary_frame_index", "encoded_snapshot"} else raster)[target] = changes[target]
    with pytest.raises(ValueError):
        preview._bind_prepared(str(prepared[0]), image, report)
    assert_closed(image)


def test_primary_mpo_frame_is_allowed_only_with_snapshot_binding(prepared):
    image, report = prepared[2]()
    report["preview"]["decoder"].update(format="MPO", container_frame_count=2)
    bundle = preview._bind_prepared(str(prepared[0]), image, report)
    assert project(bundle).status == "diagnostic"


def test_wrong_source_same_size_and_same_bytes_is_rejected(prepared):
    source, _, make, _ = prepared
    different = source.with_name("different.JPG")
    different.write_bytes(source.read_bytes())
    image, report = make(source_path=different)
    with pytest.raises(ValueError, match="source_changed_or_unbound"):
        preview._bind_prepared(str(source), image, report)
    assert_closed(image)


@pytest.mark.parametrize("which", ["source", "parser"])
def test_changed_identity_after_preparation_closes_image(prepared, which):
    image, report = prepared[2]()
    prepared[0 if which == "source" else 1].write_bytes(b"new bytes")
    with pytest.raises(ValueError, match="changed_after_prepare"):
        preview._bind_prepared(str(prepared[0]), image, report)
    assert_closed(image)


def test_loader_preparation_and_all_file_checks_run_off_ui(prepared, monkeypatch):
    source, parser, make, _ = prepared
    main = threading.get_ident()
    io_threads, calls = [], []
    original_stat = af._stat_identity
    def stat_identity(value):
        io_threads.append(threading.get_ident())
        return original_stat(value)
    def prepare(path, **kwargs):
        calls.append((path, kwargs, threading.get_ident()))
        return make()
    monkeypatch.setattr(af, "_stat_identity", stat_identity)
    loader = preview.AfJpegPreviewLoader(preparer=prepare, exiftool_path=parser, timeout=.5)
    try:
        token = loader.request(source)
        found = wait_result(loader)
        assert len(found) == 1 and found[0][0] == token
        bundle = found[0][1]
        assert bundle["request_path"] == str(source)
        assert calls == [(str(source), {"exiftool_path": parser, "timeout": .5, "max_edge": 768}, calls[0][2])]
        assert calls[0][2] != main and io_threads and all(t != main for t in io_threads)
        assert loader._thread.daemon is True
        monkeypatch.setattr(Path, "stat", lambda *a, **kw: pytest.fail("UI projection stat"))
        monkeypatch.setattr(Path, "resolve", lambda *a, **kw: pytest.fail("UI projection resolve"))
        assert project(bundle).status == "diagnostic"
        assert loader.pop_completed() == []
        bundle["clean_image"].close()
    finally:
        loader.close()


def test_latest_only_closes_running_stale_and_skips_middle_pending(prepared):
    source, _, make, images = prepared
    started, release = threading.Event(), threading.Event()
    calls = []
    def prepare(path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            started.set()
            assert release.wait(3)
        return make()
    loader = preview.AfJpegPreviewLoader(preparer=prepare)
    try:
        first = loader.request(source)
        assert started.wait(3)
        for _ in range(100):
            last = loader.request(source)
        assert last > first
        assert loader._pending == (last, str(source))
        release.set()
        result = wait_result(loader)
        assert result[0][0] == last
        assert len(calls) == 2
        assert_closed(images[0])
        assert images[1].getpixel((0, 0)) == (29, 49, 79)
        loader.close()
        # A popped image is now UI-owned, so teardown cannot close it.
        assert images[1].getpixel((0, 0)) == (29, 49, 79)
        images[1].close()
    finally:
        release.set()
        loader.close()


@pytest.mark.parametrize("action", ["invalidate", "close", "request"])
def test_queued_unpopped_completion_is_closed(prepared, action):
    source, _, make, images = prepared
    loader = preview.AfJpegPreviewLoader(preparer=lambda *a, **kw: make())
    try:
        loader.request(source)
        wait_until(lambda: loader._completed is not None)
        image = images[0]
        if action == "request":
            loader.request(source)
        else:
            getattr(loader, action)()
        assert_closed(image)
        if action != "request":
            assert loader.pop_completed() == []
    finally:
        loader.close()
        loader._thread.join(3)


@pytest.mark.parametrize("action", ["invalidate", "close"])
def test_running_completion_is_closed_and_close_is_nonblocking(prepared, action):
    source, _, make, images = prepared
    started, release = threading.Event(), threading.Event()
    def prepare(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return make()
    loader = preview.AfJpegPreviewLoader(preparer=prepare)
    try:
        loader.request(source)
        assert started.wait(3)
        loader.request(source)
        getattr(loader, action)()
        assert loader._thread.is_alive()
        assert loader._pending is None
        assert loader.pop_completed() == []
        if action == "close":
            with pytest.raises(RuntimeError, match="closed"):
                loader.request(source)
        release.set()
        wait_until(lambda: images and is_closed(images[0]))
        assert_closed(images[0])
        assert loader.pop_completed() == []
        loader.invalidate()
        loader.close()
    finally:
        release.set()
        loader.close()
        loader._thread.join(3)


def test_failure_does_not_kill_worker_and_no_success_or_failure_cache(prepared):
    source, parser, make, _ = prepared
    calls = []
    def prepare(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise OSError("synthetic read failure")
        return make()
    loader = preview.AfJpegPreviewLoader(preparer=prepare)
    try:
        loader.request(source)
        assert wait_result(loader)[0][1]["reason"] == "jpeg_prepare_error"
        loader.request(source)
        first = wait_result(loader)[0][1]
        parser.write_bytes(b"new parser")
        source.write_bytes(b"new source")
        loader.request(source)
        second = wait_result(loader)[0][1]
        assert first["provenance"]["source"] != second["provenance"]["source"]
        assert first["provenance"]["parser"] != second["provenance"]["parser"]
        assert len(calls) == 3
        first["clean_image"].close()
        second["clean_image"].close()
    finally:
        loader.close()


def test_raw_request_uses_dedicated_source_not_jpeg_or_sibling(prepared, monkeypatch):
    from ai_cull_assistant import af_raw_source
    calls = []
    def raw_loader(path, **kwargs):
        calls.append(str(path))
        return None, {"schema": "af-raw-source-v1", "viewable_preview": False,
                      "reason": "synthetic_no_preview", "metadata": {}, "source": {}, "binding": {}}
    monkeypatch.setattr(af_raw_source, "load_raw_embedded_preview", raw_loader)
    loader = preview.AfJpegPreviewLoader(preparer=lambda *a, **kw: pytest.fail("JPEG preparation"))
    try:
        path = prepared[0].with_suffix(".RW2")
        loader.request(path)
        result = wait_result(loader)[0][1]
        assert calls == [str(path)]
        assert result["reason"] == "synthetic_no_preview"
        assert result["clean_image"] is None
    finally:
        loader.close()


def test_missing_parser_error_preserved_without_fabricated_pixels(prepared):
    image, report = prepared[2]()
    image.close()
    report["metadata"].update(status="error", reason="exiftool_unavailable")
    report["metadata"]["source"]["unchanged_during_read"] = False
    report["binding"].update(metadata_source_matches=False, parser_unchanged=False)
    report.update(status="error", reason="source_changed_or_unbound")
    bundle = preview._bind_prepared(str(prepared[0]), None, report)
    assert bundle["clean_image"] is None
    assert bundle["reason"] == "exiftool_unavailable"
    assert "未找到 ExifTool" in bundle["status_text"]
    assert_hidden(preview.project_jpeg_diagnostic(bundle, (0, 0, 300, 200)))


def test_actual_preparer_default_path_returns_clean_unsupported_resolution(tmp_path, monkeypatch):
    source, parser = tmp_path / "small-original.jpg", tmp_path / "exiftool"
    parser.write_bytes(b"synthetic parser")
    image = Image.new("RGB", (96, 64), (10, 20, 30))
    exif = Image.Exif()
    exif[274] = 8
    image.save(source, exif=exif)
    image.close()
    def read(path, **kwargs):
        raw = {"IFD0:Make": "Panasonic", "IFD0:Model": "DC-S5M2", "IFD0:Orientation": 8,
               "Panasonic:FocusMode": 6, "Panasonic:AFAreaMode": "16 0",
               "Panasonic:AFPointPosition": ".25 .6", "Panasonic:AFAreaSize": ".1 .2",
               "Panasonic:PanasonicImageWidth": 96, "Panasonic:PanasonicImageHeight": 64,
               "ExifIFD:ExifImageWidth": 96, "ExifIFD:ExifImageHeight": 64}
        return {"status": "not_validated", "source": {**af._source_identity(Path(path)), "unchanged_during_read": True},
                "parser": {"name": "ExifTool", "path": str(parser), "version": "13.59", "adapter_version": af.ADAPTER_VERSION,
                           "stat_identity": af._stat_identity(parser.stat())},
                "raw_tags": raw, "diagnostics": af._diagnostics(raw),
                "usable_for_subject_selection": False, "usable_for_keep_reject": False}
    monkeypatch.setattr(af, "read_af_metadata", read)
    loader = preview.AfJpegPreviewLoader()
    try:
        loader.request(source)
        bundle = wait_result(loader)[0][1]
        assert bundle["clean_image"].size == (64, 96)
        assert bundle["reason"] == "unsupported_jpeg_dimensions"
        assert bundle["provenance"]["preview"]["annotation_applied"] is False
        assert_hidden(project(bundle))
        bundle["clean_image"].close()
    finally:
        loader.close()


@pytest.mark.parametrize("failure, expected", [("missing", "exiftool_unavailable"),
                                               ("timeout", "exiftool_version_timeout"),
                                               ("warning", "exiftool_read_warning")])
def test_actual_preparer_errors_keep_specific_metadata_cause(tmp_path, monkeypatch, failure, expected):
    import json
    import subprocess
    source, parser = tmp_path / "original.jpg", tmp_path / "exiftool"
    image = Image.new("RGB", (96, 64), (10, 20, 30))
    image.save(source)
    image.close()
    parser.write_bytes(b"synthetic parser")
    def resolve(*args):
        if failure == "missing":
            raise FileNotFoundError("synthetic missing tool")
        return parser
    def run(tool, argv, timeout):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("mock parser", timeout)
        if argv[-1] == "-ver":
            return subprocess.CompletedProcess(argv, 0, "13.59", "")
        return subprocess.CompletedProcess(argv, 0, json.dumps([{"ExifTool:Warning": "synthetic warning"}]), "")
    monkeypatch.setattr(af, "_resolve_tool", resolve)
    monkeypatch.setattr(af, "_run", run)
    loader = preview.AfJpegPreviewLoader()
    try:
        loader.request(source)
        bundle = wait_result(loader)[0][1]
        assert bundle["reason"] == expected
        assert bundle["metadata"]["reason"] == expected
        assert "原件已变化" not in bundle["status_text"]
        if failure == "missing":
            assert "未找到 ExifTool" in bundle["status_text"]
        elif failure == "timeout":
            assert "超时" in bundle["status_text"]
        else:
            assert "警告" in bundle["status_text"]
        assert_hidden(preview.project_jpeg_diagnostic(bundle, (0, 0, 300, 200)))
        if bundle["clean_image"] is not None:
            bundle["clean_image"].close()
    finally:
        loader.close()


def test_source_profile_never_authenticates_original_camera_file(prepared):
    bundle = bundle_for(prepared)
    evidence = bundle["provenance"]["preview"]["dimension_evidence"]
    assert evidence["camera_original_required"] is True
    assert evidence["camera_original_authenticated"] is False
    assert "crop-and-resize" in evidence["limitation"]


def test_raster_aspect_must_match_prepared_full_frame(prepared):
    image, report = prepared[2]()
    image.close()
    wrong = Image.new("RGB", (128, 128))
    report["preview"].update(raster_size=[128, 128], clean_pixels_sha256=hashlib.sha256(wrong.tobytes()).hexdigest())
    with pytest.raises(ValueError, match="preview_source_binding_mismatch"):
        preview._bind_prepared(str(prepared[0]), wrong, report)
    assert_closed(wrong)


def test_windows_path_handle_ctime_difference_keeps_preview_binding(prepared):
    image, report = prepared[2]()
    for key in ("decode_stat_before", "decode_stat_after"):
        report["preview"][key]["ctime_ns"] += 1_000_000
    bundle = preview._bind_prepared(str(prepared[0]), image, report)
    assert bundle["status"] == "diagnostic"
    bundle["clean_image"].close()


def test_decode_handle_identity_change_still_rejects_preview(prepared):
    image, report = prepared[2]()
    report["preview"]["decode_stat_after"]["ctime_ns"] += 1
    with pytest.raises(ValueError, match="preview_source_binding_mismatch"):
        preview._bind_prepared(str(prepared[0]), image, report)
    assert_closed(image)


def test_preparation_does_not_mutate_metadata_or_report_decision_flags(prepared):
    image, report = prepared[2]()
    before = deepcopy(report)
    bundle = preview._bind_prepared(str(prepared[0]), image, report)
    project(bundle)
    assert report == before
    assert bundle["metadata"]["usable_for_subject_selection"] is False
    assert bundle["metadata"]["usable_for_keep_reject"] is False


@pytest.mark.parametrize('fields', [
    {'IFD0:Software': 'Adobe Photoshop Lightroom 11.1'},
    {'XMP-xmp:CreatorTool': 'Capture One 23'},
    {'XMP-xmpMM:HistoryChanged': '/metadata'},
    {'XMP-xmpMM:HistorySoftwareAgent': ['Adobe Photoshop Camera Raw 11.3']},
    {'XMP-xmpMM:HistoryAction': 'saved'},
])
def test_processing_history_prevents_display_without_claiming_pixel_edit(prepared, fields):
    image, report = prepared[2](changes=fields)
    evidence = report['preview']['processing_evidence']
    assert evidence['status'] == 'unknown'
    assert evidence['pixel_edit_proven'] is False
    assert evidence['camera_original_authenticated'] is False
    assert report['metadata']['usable_for_keep_reject'] is False
    bundle = preview._bind_prepared(str(prepared[0]), image, report)
    assert_hidden(project(bundle))
    assert bundle['reason'] == 'processing_geometry_unconfirmed'
    assert '处理来源' in bundle['status_text']


@pytest.mark.parametrize('software', ['Ver.1.0', 'ILCE-6600 v1.00'])
def test_camera_software_only_is_still_unvalidated_assumption(prepared, software):
    image, report = prepared[2](changes={'IFD0:Software': software})
    evidence = report['preview']['processing_evidence']
    assert evidence['status'] == 'not_validated'
    assert evidence['camera_original_authenticated'] is False
    bundle = preview._bind_prepared(str(prepared[0]), image, report)
    assert project(bundle).status == 'diagnostic'


@pytest.mark.parametrize('tamper', ['missing', 'status', 'fields'])
def test_processing_evidence_cannot_be_omitted_or_forged(prepared, tamper):
    image, report = prepared[2]()
    evidence = report['preview']['processing_evidence']
    if tamper == 'missing':
        del report['preview']['processing_evidence']
    elif tamper == 'status':
        evidence['camera_original_authenticated'] = True
    else:
        report['metadata']['raw_tags']['XMP-xmpMM:HistoryChanged'] = '/metadata'
    bundle = preview._bind_prepared(str(prepared[0]), image, report)
    assert_hidden(project(bundle))
    assert bundle['reason'] == 'processing_evidence_missing_or_mismatched'


@pytest.mark.parametrize("fields", [
    {"XMP-crs:CropLeft": .1}, {"XMP-crs:CropAngle": 1}, {"XMP-crs:HasCrop": True},
    {"XMP-crs:CropTop": 0, "XMP-crs:CropLeft": 0, "XMP-crs:CropBottom": 1,
     "XMP-crs:CropRight": 1, "XMP-crs:CropAngle": float("nan")},
])
def test_shared_processing_guard_checks_explicit_crop(prepared, fields):
    bundle = bundle_for(prepared, changes=fields)
    assert_hidden(project(bundle))
    assert bundle["reason"] == "processing_geometry_unconfirmed"


def test_shared_processing_guard_full_identity_crop_still_unvalidated(prepared):
    fields = {"XMP-crs:CropTop": 0, "XMP-crs:CropLeft": 0, "XMP-crs:CropBottom": 1,
              "XMP-crs:CropRight": 1, "XMP-crs:CropAngle": 0, "XMP-crs:HasCrop": False}
    assert project(bundle_for(prepared, changes=fields)).status == "diagnostic"


@pytest.mark.parametrize("name,value", [("CropRight", "0.999999999999999999"),
                                       ("CropAngle", "1e-400")])
def test_processing_crop_identity_requires_exact_numbers(prepared, name, value):
    fields = {"XMP-crs:CropTop": 0, "XMP-crs:CropLeft": 0, "XMP-crs:CropBottom": 1,
              "XMP-crs:CropRight": 1, "XMP-crs:CropAngle": 0}
    fields["XMP-crs:" + name] = value
    bundle = bundle_for(prepared, changes=fields)
    assert_hidden(project(bundle))
    assert bundle["reason"] == "processing_geometry_unconfirmed"


def _canon_multi_changes():
    return {'IFD0:Make': 'Canon', 'IFD0:Model': 'Canon EOS R3',
            'Canon:FocusMode': 0, 'Canon:AFAreaMode': 13,
            'Canon:NumAFPoints': 2, 'Canon:ValidAFPoints': 2,
            'Canon:CanonImageWidth': 6000, 'Canon:CanonImageHeight': 4000,
            'Canon:AFImageWidth': 6000, 'Canon:AFImageHeight': 4000,
            'Canon:AFAreaWidths': [300, 300], 'Canon:AFAreaHeights': [200, 200],
            'Canon:AFAreaXPositions': [-1500, 1500], 'Canon:AFAreaYPositions': [1000, -1000],
            'Canon:AFPointsSelected': [1], 'Canon:AFPointsInFocus': [2]}


@pytest.mark.parametrize('orientation', range(1, 9))
def test_same_jpeg_multiple_record_binding(prepared, orientation):
    image, report = prepared[2](orientation=orientation, changes=_canon_multi_changes())
    bundle = preview._bind_prepared(str(prepared[0]), image, report)
    value = project(bundle)
    assert value.status == 'diagnostic' and value.center is None
    assert [marker.index for marker in value.markers] == [0, 1]
    assert [marker.role for marker in value.markers] == ['selected', 'camera_reported_in_focus']
    assert '2条记录' in bundle['status_text']
    assert all(marker.clip_rect == value.clip_rect for marker in value.markers)
    assert_hidden(project(bundle, (0, 0, 100, 100)), 'display_aspect_mismatch')


@pytest.mark.parametrize('target', ['geometry_role', 'diagnostic_role', 'drop_record', 'first_point_fallback'])
def test_collection_cannot_be_forged_before_binding(prepared, target):
    image, report = prepared[2](changes=_canon_multi_changes())
    if target == 'geometry_role':
        report['geometry']['records'][0]['role'] = 'camera_reported_in_focus'
    elif target == 'diagnostic_role':
        report['metadata']['diagnostics']['coordinates']['display_records'][0]['selected'] = False
    elif target == 'drop_record':
        report['geometry']['records'].pop()
    else:
        report['geometry']['center_xy_hypothesis'] = [.25, .25]
    bundle = preview._bind_prepared(str(prepared[0]), image, report)
    assert_hidden(project(bundle))


def test_canon_collection_cannot_downgrade_to_forged_legacy_singleton(prepared):
    image, report = prepared[2](changes=_canon_multi_changes())
    diag = report['metadata']['diagnostics']
    record = diag['coordinates']['display_records'][0]
    diag['mode'].update(display_records_candidate=False, single_area_candidate=True)
    diag['coordinates'].update(center={'value':record['center_xy']},
        raw_bbox_xywh_hypothesis=record['raw_bbox_xywh_hypothesis'],
        oriented_center_xy_hypothesis=record['oriented_center_xy_hypothesis'],
        oriented_bbox_xywh_hypothesis=record['oriented_bbox_xywh_hypothesis'])
    report['geometry'].pop('records')
    report['geometry'].update(center_xy_hypothesis=record['oriented_center_xy_hypothesis'],
        bbox_xywh_hypothesis=record['oriented_bbox_xywh_hypothesis'])
    bundle = preview._bind_prepared(str(prepared[0]), image, report)
    assert bundle['reason'] == 'record_collection_invalid'
    assert_hidden(project(bundle))
    assert diagnostic._geometry(report['metadata'], report['binding'], report['preview'])['status'] == 'unknown'


def test_display_resource_limit_never_truncates_parser_records(prepared):
    changes = _canon_multi_changes()
    count = 4097
    changes.update({'Canon:NumAFPoints':count, 'Canon:ValidAFPoints':count,
                    'Canon:AFAreaWidths':[2]*count, 'Canon:AFAreaHeights':[2]*count,
                    'Canon:AFAreaXPositions':[0]*count, 'Canon:AFAreaYPositions':[0]*count,
                    'Canon:AFPointsSelected':[-1]*256+[1],
                    'Canon:AFPointsInFocus':[0]*257})
    image, report = prepared[2](changes=changes)
    assert len(report['metadata']['diagnostics']['coordinates']['display_records']) == count
    bundle = preview._bind_prepared(str(prepared[0]), image, report)
    assert bundle['reason'] == 'record_collection_exceeds_display_limit'
    assert '4096' in bundle['status_text']
    assert_hidden(project(bundle))
