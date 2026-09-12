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
        assert kwargs['cache_dir'] == workspace / 'cache' / 'analysis'
        a = assets[0]
        a.auto_rejected = True
        a.screening_reason = 'obvious_subject_blur'
        return {a.stem: ScreeningResult(True, a.screening_reason, True)}
    monkeypatch.setattr('ai_cull_assistant.processing_job.screen_assets', screen)
    def fail_preview(*args, **kwargs):
        raise AssertionError('regeneration should reuse preview')
    monkeypatch.setattr('ai_cull_assistant.processing_job.build_preview', fail_preview)
    monkeypatch.setattr('ai_cull_assistant.ai_focus.review_focus', fail_preview)

    updated = start_job(photos, workspace, _options(technical_screening=True), CropSettings(), result=first).run(
        _options(technical_screening=True), CropSettings(), Event(), None, focus_profile={"id": "p"})
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
        assert cache_dir == workspace / "cache" / "analysis"
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

    # Editing face crops and rebuilding sheets must preserve every AI outcome,
    # including uncertain, even after a restart and a profile change.
    crops = CropSettings(1.8, -.2, '1:1', .8)
    rebuilt = start_job(photos, workspace, _options(technical_screening=True), crops, result=result)
    rebuilt = load_job(workspace, photos).run(
        _options(technical_screening=True), crops, Event(), None,
        focus_profile={"id": "different-profile"}, on_log=logs.append,
    )
    assert calls == ["P0000", "P0001", "P0002"]
    assert [a.ai_focus_result for a in rebuilt.assets] == list(answers.values())
    assert [a.auto_rejected for a in rebuilt.assets] == [False, True, False]
    assert [a.screening_reason for a in rebuilt.assets] == [
        "ai_focus_clear", "ai_focus_blur", "ai_focus_uncertain"]
    rebuilt.assets[1].ai_focus_dirty = True
    reviewed_again = start_job(photos, workspace, _options(technical_screening=True), CropSettings(), result=rebuilt)
    reviewed_again = load_job(workspace, photos).run(
        _options(technical_screening=True), CropSettings(), Event(), None,
        focus_profile=profile,
    )
    assert calls == ["P0000", "P0001", "P0002", "P0001"]
    assert not any(a.ai_focus_dirty for a in reviewed_again.assets)
    assert [a.ai_focus_result for a in reviewed_again.assets] == list(answers.values())




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


def test_staged_scan_only_builds_previews_screens_and_groups(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from ai_cull_assistant.screening import ScreeningResult

    photos = _photos(tmp_path, 2)
    workspace = tmp_path / "workspace"
    stale = workspace / "contact_sheets" / "main" / "old.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    calls = []

    def screen(assets, **_kwargs):
        asset = assets[0]
        calls.append(asset.stem)
        asset.auto_rejected = False
        asset.screening_reason = "subject_not_obviously_blurred"
        asset.face_found = True
        return {asset.stem: ScreeningResult(False, asset.screening_reason, True)}

    def forbidden_review(*_args, **_kwargs):
        raise AssertionError("scan stage must not call the API")

    monkeypatch.setattr("ai_cull_assistant.processing_job.screen_assets", screen)
    monkeypatch.setitem(sys.modules, "ai_cull_assistant.ai_focus", SimpleNamespace(review_focus=forbidden_review))
    result = start_job(
        photos, workspace, _options(technical_screening=True), CropSettings(), mode="scan"
    ).run(
        _options(technical_screening=True), CropSettings(), Event(), None,
        focus_profile={"id": "configured-but-unused"},
    )

    assert result is not None
    assert calls == ["P0000", "P0001"]
    assert all(asset.preview_path.is_file() for asset in result.assets)
    assert (workspace / "groups.json").is_file()
    assert (workspace / "scan-session.json").is_file()
    assert result.main_pages == [] and result.rejected_pages == []
    assert not (workspace / "contact_sheets").exists()


def test_staged_rescan_only_changed_assets_and_preserves_manual_groups(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from ai_cull_assistant.screening import ScreeningResult

    photos = _photos(tmp_path, 3)
    workspace = tmp_path / "workspace"
    first = start_job(photos, workspace, _options(), CropSettings(), mode="scan").run(
        _options(), CropSettings(), Event(), None
    )
    for index, asset in enumerate(first.assets):
        asset.group_id = 10 + index
    from ai_cull_assistant.workflow import persist_manual_groups
    persist_manual_groups(first)
    groups_before = (workspace / "groups.json").read_bytes()
    first.assets[1].ai_focus_result = {"status": "blur", "reason": "old crop"}
    first.assets[1].ai_focus_dirty = True
    first.assets[1].auto_rejected = True
    first.assets[1].screening_reason = "ai_focus_blur"
    calls = []

    def screen(assets, **_kwargs):
        asset = assets[0]
        calls.append(asset.stem)
        asset.auto_rejected = False
        asset.screening_reason = "no_reliable_face"
        asset.face_found = False
        return {asset.stem: ScreeningResult(False, asset.screening_reason, False)}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("rescan must neither rebuild previews nor call API")

    monkeypatch.setattr("ai_cull_assistant.processing_job.screen_assets", screen)
    monkeypatch.setattr("ai_cull_assistant.processing_job.build_preview", forbidden)
    monkeypatch.setitem(sys.modules, "ai_cull_assistant.ai_focus", SimpleNamespace(review_focus=forbidden))
    result = start_job(
        photos, workspace, _options(technical_screening=True), CropSettings(),
        result=first, mode="rescan",
    ).run(
        _options(technical_screening=True), CropSettings(), Event(), None,
        focus_profile={"id": "unused"},
    )

    assert calls == ["P0001"]
    assert [asset.group_id for asset in result.assets] == [10, 11, 12]
    assert (workspace / "groups.json").read_bytes() == groups_before
    changed = result.assets[1]
    assert changed.ai_focus_result is None and changed.ai_focus_dirty is False
    assert changed.screening_reason == "no_reliable_face"
    assert result.main_pages == [] and result.rejected_pages == []


def test_staged_focus_reviews_only_pending_without_repeating_valid_result(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    photos = _photos(tmp_path, 3)
    workspace = tmp_path / "workspace"
    first = start_job(photos, workspace, _options(), CropSettings(), mode="scan").run(
        _options(), CropSettings(), Event(), None
    )
    first.assets[0].screening_reason = "no_reliable_face"
    from ai_cull_assistant.subject import SubjectFeatures
    first.assets[0].subject_checked = True
    first.assets[0].subject_features = SubjectFeatures("", "", None, (.25, .2, .2, .2))
    first.assets[1].screening_reason = "ai_focus_uncertain"
    first.assets[1].ai_focus_result = {"status": "uncertain", "reason": "already reviewed"}
    first.assets[2].screening_reason = "subject_not_obviously_blurred"
    stale = workspace / "contact_sheets" / "main" / "stale.jpg"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"stale")
    calls = []

    def review(asset, *_args):
        calls.append(asset.stem)
        return {"status": "blur", "reason": "face is blurred"}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("focus stage must not rerun local analysis")

    monkeypatch.setattr("ai_cull_assistant.processing_job.screen_assets", forbidden)
    monkeypatch.setattr("ai_cull_assistant.processing_job.build_preview", forbidden)
    monkeypatch.setitem(sys.modules, "ai_cull_assistant.ai_focus", SimpleNamespace(review_focus=review))
    job = start_job(photos, workspace, _options(), CropSettings(), result=first, mode="focus")
    result = job.run(
        _options(), CropSettings(), Event(), None, focus_profile={"id": "profile"}
    )

    assert calls == ["P0000"]
    assert result.assets[0].auto_rejected is True
    assert result.assets[1].ai_focus_result == {"status": "uncertain", "reason": "already reviewed"}
    assert result.main_pages == [] and result.rejected_pages == []
    assert not (workspace / "contact_sheets").exists()
    assert (workspace / "scan-session.json").is_file()


def test_staged_focus_skips_pending_photo_without_reliable_face(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    photos = _photos(tmp_path, 1)
    workspace = tmp_path / "workspace"
    first = start_job(photos, workspace, _options(), CropSettings(), mode="scan").run(
        _options(), CropSettings(), Event(), None
    )
    asset = first.assets[0]
    asset.screening_reason = "no_reliable_face"
    asset.face_found = False
    asset.subject_checked = True
    asset.subject_features = None
    calls = []
    logs = []

    def review(*_args):
        calls.append("called")
        return {"status": "clear", "reason": "unexpected"}

    monkeypatch.setitem(sys.modules, "ai_cull_assistant.ai_focus", SimpleNamespace(
        review_focus=review,
        _subject_face=lambda *_args: None,
    ))
    result = start_job(
        photos, workspace, _options(), CropSettings(), result=first, mode="focus"
    ).run(
        _options(), CropSettings(), Event(), None,
        focus_profile={"id": "profile"}, on_log=logs.append,
    )

    assert calls == []
    assert result.assets[0].ai_focus_result is None
    assert result.assets[0].screening_reason == "no_reliable_face"
    assert len(logs) == 1 and "未检测到可靠人脸" in logs[0]


def test_staged_focus_stop_resume_keeps_completed_api_result(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from ai_cull_assistant.subject import SubjectFeatures

    photos = _photos(tmp_path, 2)
    workspace = tmp_path / "workspace"
    first = start_job(photos, workspace, _options(), CropSettings(), mode="scan").run(
        _options(), CropSettings(), Event(), None
    )
    for asset in first.assets:
        asset.screening_reason = "face_focus_uncertain"
        asset.subject_checked = True
        asset.subject_features = SubjectFeatures("", "", None, (.25, .2, .2, .2))
    calls = []

    def review(asset, *_args):
        calls.append(asset.stem)
        return {"status": "clear", "reason": "face clear"}

    monkeypatch.setitem(sys.modules, "ai_cull_assistant.ai_focus", SimpleNamespace(review_focus=review))
    job = start_job(photos, workspace, _options(), CropSettings(), result=first, mode="focus")
    stop = Event()

    def stop_after_first(_percent):
        if len(calls) == 1:
            stop.set()

    assert job.run(
        _options(), CropSettings(), stop, stop_after_first, focus_profile={"id": "profile"}
    ) is None
    assert calls == ["P0000"]
    restored = load_job(workspace, photos)
    assert restored is not None and restored.mode == "focus"
    result = restored.run(
        _options(), CropSettings(), Event(), None, focus_profile={"id": "profile"}
    )
    assert result is not None
    assert calls == ["P0000", "P0001"]
    assert [asset.ai_focus_result["status"] for asset in result.assets] == ["clear", "clear"]


def test_staged_focus_collects_errors_then_retries_only_failed_photos(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from ai_cull_assistant.subject import SubjectFeatures

    photos = _photos(tmp_path, 3)
    workspace = tmp_path / "workspace"
    first = start_job(photos, workspace, _options(), CropSettings(), mode="scan").run(
        _options(), CropSettings(), Event(), None
    )
    for asset in first.assets:
        asset.screening_reason = "face_focus_uncertain"
        asset.subject_checked = True
        asset.subject_features = SubjectFeatures("", "", None, (.25, .2, .2, .2))

    calls = []

    def review(asset, *_args):
        calls.append(asset.stem)
        if asset.stem == "P0001" and calls.count("P0001") == 1:
            raise ValueError("返回的不是严格 JSON")
        status = "blur" if asset.stem == "P0002" else "clear"
        return {"status": status, "reason": f"{asset.stem} verdict"}

    monkeypatch.setitem(sys.modules, "ai_cull_assistant.ai_focus", SimpleNamespace(review_focus=review))
    logs = []
    job = start_job(photos, workspace, _options(), CropSettings(), result=first, mode="focus")
    assert job.run(
        _options(), CropSettings(), Event(), None,
        focus_profile={"id": "profile"}, on_log=logs.append,
    ) is None

    assert calls == ["P0000", "P0001", "P0002"]
    assert job.awaiting_focus_error_decision is True
    assert job.pending_focus_errors == ({
        "index": 1,
        "filename": "P0001.jpg",
        "message": "返回的不是严格 JSON",
    },)
    assert job.assets[0].ai_focus_result["status"] == "clear"
    assert job.assets[1].ai_focus_result is None
    assert job.assets[1].screening_reason == "face_focus_uncertain"
    assert job.assets[1].auto_rejected is False
    assert job.assets[2].ai_focus_result["status"] == "blur"
    assert any("将继续处理其余照片" in line for line in logs)

    restored = load_job(workspace, photos)
    assert restored is not None and restored.awaiting_focus_error_decision
    # Resume without a UI decision waits and never spends another API request.
    assert restored.run(
        _options(), CropSettings(), Event(), None, focus_profile={"id": "profile"}
    ) is None
    assert calls == ["P0000", "P0001", "P0002"]

    restored.retry_failed()
    result = restored.run(
        _options(), CropSettings(), Event(), None, focus_profile={"id": "profile"}
    )
    assert result is not None
    assert calls == ["P0000", "P0001", "P0002", "P0001"]
    assert [asset.ai_focus_result["status"] for asset in result.assets] == [
        "clear", "clear", "blur"
    ]


def test_staged_focus_can_skip_failed_photos_and_keep_them_pending(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from ai_cull_assistant.subject import SubjectFeatures

    photos = _photos(tmp_path, 2)
    workspace = tmp_path / "workspace"
    first = start_job(photos, workspace, _options(), CropSettings(), mode="scan").run(
        _options(), CropSettings(), Event(), None
    )
    for asset in first.assets:
        asset.screening_reason = "face_focus_uncertain"
        asset.subject_checked = True
        asset.subject_features = SubjectFeatures("", "", None, (.25, .2, .2, .2))

    calls = []

    def review(asset, *_args):
        calls.append(asset.stem)
        if asset.stem == "P0000":
            raise RuntimeError("service unavailable")
        return {"status": "clear", "reason": "清晰"}

    monkeypatch.setitem(sys.modules, "ai_cull_assistant.ai_focus", SimpleNamespace(review_focus=review))
    job = start_job(photos, workspace, _options(), CropSettings(), result=first, mode="focus")
    assert job.run(
        _options(), CropSettings(), Event(), None, focus_profile={"id": "profile"}
    ) is None
    assert calls == ["P0000", "P0001"]

    restored = load_job(workspace, photos)
    restored.skip_failed()
    result = restored.run(
        _options(), CropSettings(), Event(), None, focus_profile=None
    )
    assert result is not None
    assert calls == ["P0000", "P0001"]
    failed = result.assets[0]
    assert failed.ai_focus_result is None
    assert failed.screening_reason == "face_focus_uncertain"
    assert failed.auto_rejected is False
    assert result.assets[1].ai_focus_result["status"] == "clear"


def test_staged_sheets_only_renders_current_result(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    photos = _photos(tmp_path, 3)
    workspace = tmp_path / "workspace"
    first = start_job(photos, workspace, _options(), CropSettings(), mode="scan").run(
        _options(), CropSettings(), Event(), None
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("sheet stage must not rerun analysis or API")

    monkeypatch.setattr("ai_cull_assistant.processing_job.screen_assets", forbidden)
    monkeypatch.setattr("ai_cull_assistant.processing_job.build_preview", forbidden)
    monkeypatch.setitem(sys.modules, "ai_cull_assistant.ai_focus", SimpleNamespace(review_focus=forbidden))
    result = start_job(
        photos, workspace, _options(), CropSettings(), result=first, mode="sheets"
    ).run(
        _options(), CropSettings(), Event(), None, focus_profile={"id": "unused"}
    )

    assert result.main_pages and all(path.is_file() for path in result.main_pages)
    assert result.rejected_pages == []
    assert (workspace / "scan-session.json").is_file()
