"""New verification of the recovered candidate, authored on 2026-10-14.

These tests are not reconstructions of the unavailable historical cloud tests.
All input photos and workspace records are synthetic and temporary.
"""
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import json

from PIL import Image
import pytest

from ai_cull_assistant import image_decode, processing_job
from ai_cull_assistant.crop_settings import CropSettings


@pytest.mark.parametrize("orient", [True, False], ids=["oriented", "already-oriented"])
def test_rgb_normalization_transfers_owned_image_without_extra_copy_or_convert(monkeypatch, orient):
    original = Image.new("RGB", (13, 17), (23, 45, 67))
    original.info["sentinel"] = "caller metadata"
    owned = original.copy()
    acquisitions = []

    def acquire(image):
        assert image is original
        acquisitions.append(image)
        return owned

    def forbidden(*_args, **_kwargs):
        raise AssertionError("An already owned RGB image must be transferred, not copied or converted")

    # Model the single allocation that establishes ownership before normalization.
    # Any additional full-image copy/convert on that owner is a regression.
    monkeypatch.setattr(owned, "copy", forbidden)
    monkeypatch.setattr(owned, "convert", forbidden)
    if orient:
        monkeypatch.setattr(image_decode.ImageOps, "exif_transpose", acquire)
        monkeypatch.setattr(original, "copy", forbidden)
    else:
        monkeypatch.setattr(original, "copy", lambda: acquire(original))
        monkeypatch.setattr(image_decode.ImageOps, "exif_transpose", forbidden)
    try:
        warnings = []
        result = image_decode._normalize_rgb(original, warnings, orient=orient)
        assert result is owned
        assert acquisitions == [original]
        assert result.getpixel((0, 0)) == (23, 45, 67)
        assert result.info == {}
        assert warnings == []
        assert original.info == {"sentinel": "caller metadata"}
        result.close()
        with pytest.raises(ValueError, match="closed"):
            owned.getpixel((0, 0))
        assert original.getpixel((0, 0)) == (23, 45, 67)
    finally:
        owned.close()
        original.close()


def _write_photo(path, color):
    with Image.new("RGB", (48, 64), color) as image:
        image.save(path)


_PUBLISHED_ROOTS = (
    "contact_sheets", "previews", "screening_results.json", "groups.json",
    "scan-session.json", "processing-settings.json", ".source-location.json",
    "ai_project.json", "crop-settings.json", "export", "focus-evidence",
)


def _published_bytes(workspace):
    files = {}
    for name in _PUBLISHED_ROOTS:
        root = workspace / name
        paths = root.rglob("*") if root.is_dir() else [root]
        for path in paths:
            if path.is_file():
                files[str(path.relative_to(workspace))] = path.read_bytes()
    return files


def _photo_bytes(source):
    return {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}


@pytest.fixture
def mixed_workspace(tmp_path, monkeypatch):
    source = tmp_path / "photos"
    source.mkdir()
    _write_photo(source / "A.png", "orange")
    workspace = tmp_path / "workspace"
    options = {"technical_screening": False, "photos_per_page": 2, "columns": 2}
    crops = CropSettings()

    def assign_groups(assets, *_args, **_kwargs):
        next_id = max((asset.group_id for asset in assets), default=0)
        for asset in assets:
            if asset.group_id <= 0:
                next_id += 1
                asset.group_id = next_id

    def render_page(_assets, _chunk, target, **_kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic staged contact sheet")

    # Group classification and sheet pixels are covered elsewhere. Keep the
    # real scan, staging, checkpoints, source checks and publication transaction.
    monkeypatch.setattr(processing_job, "assign_groups", assign_groups)
    monkeypatch.setattr(processing_job, "_render_page", render_page)
    first = processing_job.start_job(source, workspace, options, crops, mode="scan").run(
        options, crops, Event(), None)
    assert first is not None
    for name, payload in {
        "contact_sheets/prior.jpg": b"prior published contact sheet",
        "ai_project.json": b'{"synthetic": "prior AI answers"}',
        "crop-settings.json": b'{"synthetic": "manual face selection"}',
        "export/selected.xmp": b"prior synthetic export",
        "focus-evidence/decision.json": b'{"synthetic": "retained decision"}',
    }.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    yield SimpleNamespace(source=source, workspace=workspace, first=first, options=options, crops=crops)
    image_decode.clear_decode_cache()


def _resume(state):
    resumed = processing_job.load_job(state.workspace, state.source)
    assert resumed is not None
    result = resumed.run(state.options, state.crops, Event(), None)
    assert result is not None
    assert processing_job.load_job(state.workspace, state.source) is None
    return result


def test_cancel_after_incremental_group_checkpoint_stops_before_relative_analysis(mixed_workspace, monkeypatch):
    state = mixed_workspace
    _write_photo(state.source / "B.png", "blue")
    before = _published_bytes(state.workspace)
    originals = _photo_bytes(state.source)
    job = processing_job.start_job(state.source, state.workspace, state.options, state.crops,
                                   result=state.first, mode="scan")
    stop = Event()
    checkpoints = []

    def stop_after_group(_percent):
        if job._data.get("incremental_grouped"):
            checkpoints.append("grouped")
            stop.set()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Cancellation at the grouping checkpoint must stop before relative analysis")

    with monkeypatch.context() as patch:
        patch.setattr("ai_cull_assistant.relative_focus.flag_relative_focus", forbidden)
        assert job.run(state.options, state.crops, stop, stop_after_group) is None
    assert checkpoints == ["grouped"]
    assert _published_bytes(state.workspace) == before
    assert _photo_bytes(state.source) == originals
    resumed = processing_job.load_job(state.workspace, state.source)
    assert resumed is not None and resumed._data["incremental_grouped"] is True
    assert len(_resume(state).assets) == 2
    assert _photo_bytes(state.source) == originals


@pytest.mark.parametrize("mode", ["scan", "sheets"])
def test_cancel_during_final_source_validation_never_enters_publish(mixed_workspace, monkeypatch, mode):
    state = mixed_workspace
    if mode == "scan":
        _write_photo(state.source / "B.png", "blue")
    before = _published_bytes(state.workspace)
    originals = _photo_bytes(state.source)
    job = processing_job.start_job(state.source, state.workspace, state.options, state.crops,
                                   result=state.first, mode=mode)
    stop = Event()
    real_validate = job._validate_sources
    validations = []

    def validate_then_stop():
        real_validate()
        validations.append("validated")
        if len(validations) == 2:
            stop.set()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A stop requested during final source validation must prevent publication")

    with monkeypatch.context() as patch:
        patch.setattr(job, "_validate_sources", validate_then_stop)
        patch.setattr(job, "_publish", forbidden)
        assert job.run(state.options, state.crops, stop, None) is None
    assert validations == ["validated", "validated"]
    assert _published_bytes(state.workspace) == before
    assert _photo_bytes(state.source) == originals
    result = _resume(state)
    assert len(result.assets) == (2 if mode == "scan" else 1)
    if mode == "sheets":
        assert result.main_pages and all(path.is_file() for path in result.main_pages)
    assert _photo_bytes(state.source) == originals


@pytest.mark.parametrize("source_marker_existed", [True, False], ids=["existing-marker", "new-marker"])
def test_late_publish_failure_restores_source_marker_and_mixed_workspace_then_resumes(
        mixed_workspace, monkeypatch, source_marker_existed):
    state = mixed_workspace
    marker = state.workspace / ".source-location.json"
    if not source_marker_existed:
        marker.unlink()
    _write_photo(state.source / "B.png", "blue")
    before = _published_bytes(state.workspace)
    originals = _photo_bytes(state.source)
    job = processing_job.start_job(state.source, state.workspace, state.options, state.crops,
                                   result=state.first, mode="scan")
    real_atomic_json = processing_job.atomic_json
    late_writes = []

    def write_settings_then_fail(path, data):
        real_atomic_json(path, data)
        if Path(path) == state.workspace / "processing-settings.json":
            # The actual session and portable inventory must have been published
            # before injecting this failure; an early save_session failure would
            # not exercise restoration/removal of the newly written marker.
            assert set(json.loads(marker.read_text("utf-8"))["files"]) == {"A.png", "B.png"}
            session = json.loads((state.workspace / "scan-session.json").read_text("utf-8"))
            assert len(session["assets"]) == 2
            assert not (state.workspace / "contact_sheets").exists()
            late_writes.append("session, source marker and settings written")
            raise OSError("injected failure after settings publication")

    with monkeypatch.context() as patch:
        patch.setattr(processing_job, "atomic_json", write_settings_then_fail)
        with pytest.raises(OSError, match="injected failure after settings publication"):
            job.run(state.options, state.crops, Event(), None)
    assert late_writes == ["session, source marker and settings written"]
    assert _published_bytes(state.workspace) == before
    assert marker.exists() is source_marker_existed
    assert _photo_bytes(state.source) == originals
    resumed = processing_job.load_job(state.workspace, state.source)
    assert resumed is not None
    assert all(asset.preview_path.is_file() for asset in resumed.assets)
    assert len(_resume(state).assets) == 2
    assert set(json.loads(marker.read_text("utf-8"))["files"]) == {"A.png", "B.png"}
    assert _photo_bytes(state.source) == originals
