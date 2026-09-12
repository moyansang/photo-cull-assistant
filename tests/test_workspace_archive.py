from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from ai_cull_assistant.ai_project import ReviewProject
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.preview import ensure_preview
from ai_cull_assistant.session_store import load_session, save_session
from ai_cull_assistant.workflow import run_scan
from ai_cull_assistant.workspace_archive import compact_workspace, restore_workspace


def _completed_workspace(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (120, 180), "white").save(photos / "A.jpg")
    workspace = tmp_path / "workspace"
    result = run_scan(photos, workspace, technical_screening=False)
    save_session(result)
    project = ReviewProject(workspace)
    crops = CropSettings()
    task = project.create_task(result.assets, crops, {})
    batch = task["batches"][0]
    answer = {"task_id": task["id"], "batch_id": batch["id"], "photos": [{
        "photo_id": pid, "rating": 4, "suggest_reject": False,
        "reason": "清晰", "review_items": [],
    } for pid in batch["photo_ids"]]}
    assert project.ingest(task, batch, json.dumps(answer)) == []
    export = project.export_final(ai_ratings=True)
    return photos, workspace, result, project, task, batch, export, crops


def test_completed_workspace_compacts_and_roundtrips_without_invalidating_ai(tmp_path):
    photos, workspace, result, project, task, batch, export, crops = _completed_workspace(tmp_path)
    snapshot_hashes = list(batch["image_hashes"])
    export_bytes = export.read_bytes()
    progress = []

    stats = compact_workspace(workspace, progress.append)

    assert stats["compacted"] and stats["removed_previews"] == 1
    assert progress[0] == 0 and progress[-1] == 100
    assert not (workspace / "scan-session.json").exists()
    assert not (workspace / "previews").exists()
    assert export.read_bytes() == export_bytes
    assert (workspace / "contact_sheets").is_dir()
    assert (workspace / "ai_tasks").is_dir()

    restored_stats = restore_workspace(workspace)
    restored = load_session(workspace, photos)
    reopened = ReviewProject(workspace)
    reopened.refresh(restored.assets, crops)
    current = reopened.current_task()
    current_batch = current["batches"][0]

    assert restored_stats["restored"]
    assert current["id"] == task["id"] and current_batch["status"] == "complete"
    assert current_batch["image_hashes"] == snapshot_hashes
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in reopened.batch_images(current, current_batch)] == snapshot_hashes
    assert all(not photo["stale"] and photo["ai"]["rating"] == 4 for photo in reopened.data["photos"].values())
    assert not Path(restored.assets[0].preview_path).exists()
    assert ensure_preview(restored.assets[0]).is_file()


def test_unfinished_or_stale_workspace_is_never_compacted(tmp_path):
    _photos, workspace, _result, project, task, _batch, _export, _crops = _completed_workspace(tmp_path)
    task["batches"][0]["status"] = "pending"
    project.save()
    preview = next((workspace / "previews").rglob("*.jpg"))

    stats = compact_workspace(workspace)

    assert not stats["compacted"] and "未完成" in stats["reason"]
    assert preview.is_file() and (workspace / "ai_project.json").is_file()


def test_corrupt_archive_does_not_remove_state_or_overwrite_on_restore(tmp_path):
    _photos, workspace, _result, _project, _task, _batch, _export, _crops = _completed_workspace(tmp_path)
    compact_workspace(workspace)
    archive = workspace / ".workspace-archive.zip"
    archive.write_bytes(archive.read_bytes()[:40])

    try:
        restore_workspace(workspace)
    except Exception:
        pass
    else:
        raise AssertionError("损坏的压缩包必须拒绝恢复")

    assert archive.is_file()
    assert not (workspace / "scan-session.json").exists()


def test_workspace_overlapping_source_is_not_touched(tmp_path):
    photos, workspace, _result, _project, _task, _batch, _export, _crops = _completed_workspace(tmp_path)
    session_path = workspace / "scan-session.json"
    data = json.loads(session_path.read_text("utf-8"))
    data["input_dir"] = str(tmp_path)
    session_path.write_text(json.dumps(data), encoding="utf-8")
    preview = next((workspace / "previews").rglob("*.jpg"))

    stats = compact_workspace(workspace)

    assert not stats["compacted"] and "重叠" in stats["reason"]
    assert preview.is_file() and session_path.is_file()
