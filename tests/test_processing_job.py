from pathlib import Path
from threading import Event
import json

from PIL import Image
import pytest

from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.processing_job import SourceChangedError, load_job, start_job


def _photos(tmp_path: Path, count: int = 5) -> Path:
    folder = tmp_path / "photos"
    folder.mkdir()
    for index in range(count):
        Image.new("RGB", (80, 100), (30 + index, 60, 90)).save(folder / f"P{index:04d}.jpg")
    return folder


def _options(**changes):
    values = dict(
        grouping_preset="standard",
        photos_per_page=2,
        columns=2,
        technical_screening=False,
    )
    values.update(changes)
    return values


def test_stop_resume_uses_new_settings_only_for_unfinished_photos(tmp_path, monkeypatch):
    photos = _photos(tmp_path, 3)
    workspace = tmp_path / "workspace"
    job = start_job(photos, workspace, _options(technical_screening=True), CropSettings())
    calls = []

    def fake_screen(assets, **kwargs):
        from ai_cull_assistant.screening import ScreeningResult
        calls.append(assets[0].stem)
        assets[0].screening_reason = "checked"
        return {assets[0].stem: ScreeningResult(False, "checked", False)}

    monkeypatch.setattr("ai_cull_assistant.processing_job.screen_assets", fake_screen)
    stop = Event()

    def stop_after_first(value):
        if value > 0:
            stop.set()

    assert job.run(_options(technical_screening=True), CropSettings(), stop, stop_after_first) is None
    assert calls == ["P0000"]
    restored = load_job(workspace, photos)
    assert restored is not None

    progress = []
    result = restored.run(_options(technical_screening=False), CropSettings(), Event(), progress.append)
    assert result is not None
    assert calls == ["P0000"]
    assert restored._data["photo_options"] == {
        "0": {"technical_screening": True},
        "1": {"technical_screening": False},
        "2": {"technical_screening": False},
    }
    assert progress == sorted(progress)
    assert progress[-1] == 100
    assert restored.grouping_preset == "standard"
    assert (workspace / "scan-session.json").is_file()
    assert (workspace / "processing-settings.json").is_file()


def test_changed_original_invalidates_only_interrupted_job(tmp_path):
    photos = _photos(tmp_path, 2)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prior = workspace / "prior-result.txt"
    prior.write_text("keep", encoding="utf-8")
    unrelated = workspace / ".processing" / "unrelated"
    unrelated.mkdir(parents=True)
    (unrelated / "keep.txt").write_text("keep", encoding="utf-8")
    job = start_job(photos, workspace, _options(), CropSettings())
    job_root = job.root
    stop = Event()
    stop.set()
    assert job.run(_options(), CropSettings(), stop, None) is None

    (photos / "P0000.jpg").write_bytes(b"changed")
    with pytest.raises(SourceChangedError, match="照片文件夹中的照片出现问题"):
        load_job(workspace, photos)
    assert not job_root.exists()
    assert not (workspace / ".processing" / "active.json").exists()
    assert prior.read_text("utf-8") == "keep"
    assert (unrelated / "keep.txt").read_text("utf-8") == "keep"


def test_added_photo_does_not_invalidate_resumable_job(tmp_path):
    photos = _photos(tmp_path, 2)
    workspace = tmp_path / "workspace"
    job = start_job(photos, workspace, _options(), CropSettings())
    stop = Event()
    stop.set()
    assert job.run(_options(), CropSettings(), stop, None) is None
    Image.new("RGB", (80, 100), "red").save(photos / "NEW.jpg")
    restored = load_job(workspace, photos)
    assert restored is not None
    result = restored.run(_options(), CropSettings(), Event(), None)
    assert result is not None
    assert [asset.stem for asset in result.assets] == ["P0000", "P0001"]


def test_publish_is_staged_and_regenerate_keeps_completed_page_settings(tmp_path, monkeypatch):
    photos = _photos(tmp_path, 5)
    workspace = tmp_path / "workspace"
    first = start_job(photos, workspace, _options(), CropSettings()).run(
        _options(), CropSettings(), Event(), None
    )
    assert first is not None
    old_page = first.main_pages[0]
    old_page.write_bytes(b"old-public-output")

    job = start_job(photos, workspace, _options(), CropSettings(), result=first)
    import ai_cull_assistant.processing_job as module
    render = module._render_page
    stop = Event()
    rendered = 0

    def stop_after_page(*args, **kwargs):
        nonlocal rendered
        render(*args, **kwargs)
        rendered += 1
        if rendered == 1:
            stop.set()

    monkeypatch.setattr(module, "_render_page", stop_after_page)
    assert job.run(_options(), CropSettings(), stop, None) is None
    assert old_page.read_bytes() == b"old-public-output"
    completed_page = job.output / job._data["pages"][0]["path"]
    completed_bytes = completed_page.read_bytes()

    restored = load_job(workspace, photos)
    assert restored is not None
    result = restored.run(_options(photos_per_page=3, columns=1), CropSettings(), Event(), None)
    assert result is not None
    assert len(restored._data["pages"]) == 2
    assert restored._data["pages"][0]["options"] == {"photos_per_page": 2, "columns": 2}
    assert restored._data["pages"][1]["options"] == {"photos_per_page": 3, "columns": 1}
    assert result.main_pages[0].read_bytes() == completed_bytes
    assert old_page not in result.main_pages or old_page.read_bytes() != b"old-public-output"


def test_stored_manual_groups_keep_provenance(tmp_path):
    from ai_cull_assistant.group_store import save_groups
    from ai_cull_assistant.scanner import scan_folder

    photos = _photos(tmp_path, 2)
    workspace = tmp_path / "workspace"
    assets = scan_folder(photos)
    assets[0].group_id = 8
    assets[1].group_id = 9
    save_groups(assets, workspace / "groups.json", source="manual", collection_key=str(photos.resolve()))

    job = start_job(photos, workspace, _options(), CropSettings())
    result = job.run(_options(), CropSettings(), Event(), None)
    assert result is not None
    assert result.groups_loaded_from_store is True
    stored = json.loads((workspace / "groups.json").read_text("utf-8"))
    assert stored["source"] == "manual"
    assert [asset.group_id for asset in result.assets] == [8, 9]


def test_publish_failure_restores_public_output_and_keeps_resumable_stage(tmp_path, monkeypatch):
    photos = _photos(tmp_path, 2)
    workspace = tmp_path / "workspace"
    public = workspace / "contact_sheets"
    public.mkdir(parents=True)
    marker = public / "old.txt"
    marker.write_text("old", encoding="utf-8")
    job = start_job(photos, workspace, _options(), CropSettings())

    def fail_save(*_args, **_kwargs):
        raise OSError("simulated publish failure")

    monkeypatch.setattr("ai_cull_assistant.processing_job.save_session", fail_save)
    with pytest.raises(OSError, match="simulated"):
        job.run(_options(), CropSettings(), Event(), None)
    assert marker.read_text("utf-8") == "old"
    restored = load_job(workspace, photos)
    assert restored is not None
    assert all(Path(asset.preview_path).is_file() for asset in restored.assets)


def test_invalid_active_job_path_does_not_delete_outside_processing_root(tmp_path):
    photos = _photos(tmp_path, 1)
    workspace = tmp_path / "workspace"
    outside = workspace / "outside"
    outside.mkdir(parents=True)
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    active = workspace / ".processing" / "active.json"
    active.parent.mkdir(parents=True)
    active.write_text(json.dumps({"job_id": "../outside", "input_dir": str(photos.resolve())}), encoding="utf-8")

    with pytest.raises(Exception):
        load_job(workspace, photos)
    assert marker.read_text("utf-8") == "keep"


def test_regenerate_refreshes_focus_without_rebuilding_previews(tmp_path, monkeypatch):
    from ai_cull_assistant.screening import ScreeningResult
    photos = _photos(tmp_path, 1)
    workspace = tmp_path / 'workspace'
    first = start_job(photos, workspace, _options(), CropSettings()).run(
        _options(), CropSettings(), Event(), None)
    def screen(assets, **kwargs):
        assert kwargs['cache_dir'] == workspace / '.analysis-cache'
        a = assets[0]
        a.auto_rejected = True
        a.screening_reason = 'obvious_subject_blur'
        return {a.stem: ScreeningResult(True, a.screening_reason, True)}
    monkeypatch.setattr('ai_cull_assistant.processing_job.screen_assets', screen)
    def fail_preview(*args, **kwargs):
        raise AssertionError('regeneration should reuse preview')
    monkeypatch.setattr('ai_cull_assistant.processing_job.build_preview', fail_preview)
    updated = start_job(photos, workspace, _options(technical_screening=True), CropSettings(), result=first).run(
        _options(technical_screening=True), CropSettings(), Event(), None)
    assert updated.assets[0].auto_rejected
    assert not updated.main_pages and updated.rejected_pages
    report = json.loads((workspace / 'screening_results.json').read_text('utf-8'))
    assert 'obvious_subject_blur' in json.dumps(report)


def test_api_focus_review_is_checkpointed_once_and_never_serialises_profile(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from ai_cull_assistant.screening import ScreeningResult

    photos = _photos(tmp_path, 3)
    workspace = tmp_path / "workspace"
    calls = []

    def screen(assets, **_kwargs):
        asset = assets[0]
        asset.auto_rejected = False
        asset.screening_reason = "no_reliable_face"
        asset.face_found = False
        return {asset.stem: ScreeningResult(False, "no_reliable_face", False)}

    answers = {
        "P0000": {"status": "clear", "reason": "主体边缘清晰"},
        "P0001": {"status": "blur", "reason": "主体明显虚焦"},
        "P0002": {"status": "uncertain", "reason": "主体太小"},
    }

    def review(asset, crop_settings, profile, cache_dir):
        calls.append(asset.stem)
        assert crop_settings == CropSettings()
        assert profile["id"] == "saved-profile"
        assert cache_dir == workspace / ".analysis-cache"
        return answers[asset.stem]

    monkeypatch.setattr("ai_cull_assistant.processing_job.screen_assets", screen)
    monkeypatch.setitem(sys.modules, "ai_cull_assistant.ai_focus", SimpleNamespace(review_focus=review))
    job = start_job(photos, workspace, _options(technical_screening=True), CropSettings())
    stop = Event()

    def stop_after_first(_percent):
        if len(calls) == 1:
            stop.set()

    profile = {"id": "saved-profile", "name": "测试", "secret": "must-never-persist"}
    logs = []
    assert job.run(
        _options(technical_screening=True), CropSettings(), stop, stop_after_first,
        focus_profile=profile, on_log=logs.append,
    ) is None
    assert calls == ["P0000"]
    assert "must-never-persist" not in (job.root / "job.json").read_text("utf-8")

    restored = load_job(workspace, photos)
    result = restored.run(
        _options(technical_screening=True), CropSettings(), Event(), None,
        focus_profile=profile, on_log=logs.append,
    )
    assert result is not None
    assert calls == ["P0000", "P0001", "P0002"]
    assert [asset.screening_reason for asset in result.assets] == [
        "ai_focus_clear", "ai_focus_blur", "ai_focus_uncertain"
    ]
    assert [asset.auto_rejected for asset in result.assets] == [False, True, False]
    assert result.assets[2].ai_focus_result == answers["P0002"]
    assert len(logs) == 3 and all("AI 清晰度复核" in line for line in logs)


def test_api_failure_keeps_local_analysis_for_resumable_retry(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from ai_cull_assistant.processing_job import FocusReviewError
    from ai_cull_assistant.screening import ScreeningResult

    photos = _photos(tmp_path, 1)
    workspace = tmp_path / "workspace"
    local_calls = []
    review_calls = []

    def screen(assets, **_kwargs):
        asset = assets[0]
        local_calls.append(asset.stem)
        asset.auto_rejected = False
        asset.screening_reason = "no_reliable_face"
        return {asset.stem: ScreeningResult(False, "no_reliable_face", False)}

    def review(asset, *_args):
        review_calls.append(asset.stem)
        if len(review_calls) == 1:
            raise RuntimeError("offline")
        return {"status": "clear", "reason": "复核清晰"}

    monkeypatch.setattr("ai_cull_assistant.processing_job.screen_assets", screen)
    monkeypatch.setitem(sys.modules, "ai_cull_assistant.ai_focus", SimpleNamespace(review_focus=review))
    job = start_job(photos, workspace, _options(technical_screening=True), CropSettings())
    with pytest.raises(FocusReviewError, match="任务进度已保存"):
        job.run(_options(technical_screening=True), CropSettings(), Event(), None, focus_profile={"id": "p"})
    assert job._data["completed_photos"] == 0
    assert job._data["local_screening_pending"] == 0

    restored = load_job(workspace, photos)
    result = restored.run(
        _options(technical_screening=True), CropSettings(), Event(), None, focus_profile={"id": "p"}
    )
    assert result is not None
    assert local_calls == ["P0000"]
    assert review_calls == ["P0000", "P0000"]
    assert result.assets[0].screening_reason == "ai_focus_clear"
