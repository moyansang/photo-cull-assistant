from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat
import zipfile
import pytest

from PIL import Image

from ai_cull_assistant.ai_project import ReviewProject
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.preview import ensure_preview
from ai_cull_assistant.session_store import load_session, save_session
from ai_cull_assistant.workflow import run_scan
from ai_cull_assistant.workspace_archive import previews_were_compacted, restore_workspace
from workspace_archive_fixtures import create_legacy_workspace_archive


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


def test_completed_legacy_workspace_restores_without_invalidating_ai(tmp_path):
    photos, workspace, result, project, task, batch, export, crops = _completed_workspace(tmp_path)
    snapshot_hashes = list(batch["image_hashes"])
    export_bytes = export.read_bytes()
    progress = []
    from ai_cull_assistant.focus_audit import record_inputs, audit_root
    audit_cache = workspace / 'cache' / 'analysis'
    record_inputs(audit_cache, 'proof', [project.batch_images(task, batch)[0]], 'test', {}, 'v2')
    evidence = {p.relative_to(audit_root(audit_cache)): p.read_bytes()
                for p in audit_root(audit_cache).rglob('*') if p.is_file()}

    assert list((workspace / 'previews').rglob('.sheet-thumbs/*.png'))
    create_legacy_workspace_archive(workspace)

    assert previews_were_compacted(workspace)
    assert not (workspace / "scan-session.json").exists()
    assert not (workspace / "previews").exists()
    assert export.read_bytes() == export_bytes
    assert (workspace / "contact_sheets").is_dir()
    assert (workspace / "ai_tasks").is_dir()
    assert all((audit_root(audit_cache)/name).read_bytes() == payload for name, payload in evidence.items())

    restored_stats = restore_workspace(workspace, progress.append)
    restored = load_session(workspace, photos)
    reopened = ReviewProject(workspace)
    reopened.refresh(restored.assets, crops)
    current = reopened.current_task()
    current_batch = current["batches"][0]

    assert restored_stats["restored"]
    assert progress[0] == 0 and progress[-1] == 100
    assert previews_were_compacted(workspace)
    assert current["id"] == task["id"] and current_batch["status"] == "complete"
    assert current_batch["image_hashes"] == snapshot_hashes
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in reopened.batch_images(current, current_batch)] == snapshot_hashes
    assert all(not photo["stale"] and photo["ai"]["rating"] == 4 for photo in reopened.data["photos"].values())
    assert not Path(restored.assets[0].preview_path).exists()
    assert ensure_preview(restored.assets[0]).is_file()


def test_completed_scan_without_sheets_or_ai_restores_from_legacy_archive(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    Image.new("RGB", (120, 180), "white").save(photos / "A.jpg")
    workspace = tmp_path / "workspace"
    result = run_scan(photos, workspace, technical_screening=False)
    shutil.rmtree(result.contact_dir)
    result.main_pages = []
    result.rejected_pages = []
    result.assets[0].group_id = 7
    save_session(result)
    processing_cache = workspace / "cache" / "processing" / "obsolete-job"
    processing_cache.mkdir(parents=True)
    (processing_cache / "temporary.bin").write_bytes(b"temporary")

    create_legacy_workspace_archive(
        workspace,
        remove_paths=("previews", "cache/processing"),
    )

    assert not (workspace / "previews").exists()
    assert not (workspace / "cache" / "processing").exists()
    assert not (workspace / "contact_sheets").exists()
    assert not (workspace / "ai_project.json").exists()

    assert restore_workspace(workspace)["restored"]
    restored = load_session(workspace, photos)
    assert restored is not None
    assert restored.assets[0].group_id == 7
    assert restored.main_pages == [] and restored.rejected_pages == []


def test_completed_ai_review_restores_before_export(tmp_path):
    photos, workspace, _result, project, task, _batch, export, _crops = _completed_workspace(tmp_path)
    paid_result = json.loads(project.path.read_text("utf-8"))["photos"]
    export.unlink()

    create_legacy_workspace_archive(workspace)

    assert restore_workspace(workspace)["restored"]
    reopened = ReviewProject(workspace)
    assert reopened.current_task()["id"] == task["id"]
    assert reopened.data["photos"] == paid_result


def test_archived_ai_result_survives_snapshot_cache_cleanup_and_rebuilds_on_demand(tmp_path):
    photos, workspace, _result, project, task, batch, _export, crops = _completed_workspace(tmp_path)
    task_id = task['id']
    answers = {pid: dict(project.data['photos'][pid]['ai']) for pid in batch['photo_ids']}
    raw_responses = json.loads(json.dumps(batch['raw_responses']))
    create_legacy_workspace_archive(workspace)
    shutil.rmtree(workspace/'ai_tasks')

    assert restore_workspace(workspace)['restored']
    restored = load_session(workspace, photos)
    reopened = ReviewProject(workspace)
    reopened.refresh(restored.assets, crops)
    current = reopened.current_task()

    assert current['id'] == task_id
    assert current['batches'][0]['status'] == 'complete'
    assert current['batches'][0]['raw_responses'] == raw_responses
    assert reopened.can_reuse_task(reopened.home_sheet_signature(restored.assets, crops, []))
    assert not (workspace/'ai_tasks').exists()
    images = reopened.batch_images(current, current['batches'][0])
    assert images and all(path.is_file() for path in images)
    assert {pid: reopened.data['photos'][pid]['ai'] for pid in batch['photo_ids']} == answers
    assert current['batches'][0]['status'] == 'complete'
    assert current['batches'][0]['raw_responses'] == raw_responses


@pytest.mark.parametrize('state', ['pending', 'stale', 'changed_group', 'focus_dirty'])
def test_saved_ai_work_restores_without_losing_pending_or_stale_results(tmp_path, state):
    photos, workspace, result, project, task, batch, export, _crops = _completed_workspace(tmp_path)
    if state == 'pending':
        task['batches'][0]['status'] = 'pending'
    elif state == 'stale':
        next(iter(project.data['photos'].values()))['stale'] = True
    elif state == 'changed_group':
        result.assets[0].group_id += 1
    else:
        result.assets[0].ai_focus_dirty = True
    save_session(result)
    project.save()
    original_project = project.path.read_bytes()
    original_session = (workspace / 'scan-session.json').read_bytes()
    original_export = export.read_bytes()
    images = {Path(path): Path(path).read_bytes() for path in batch['image_paths']}
    cache = workspace / 'cache' / 'analysis'
    cache.mkdir(parents=True, exist_ok=True)
    (cache / 'temporary.bin').write_bytes(b'cache')

    create_legacy_workspace_archive(
        workspace,
        remove_paths=("previews", "cache/analysis"),
    )

    assert not (workspace / 'previews').exists()
    assert not cache.exists()
    assert export.read_bytes() == original_export
    assert all(path.read_bytes() == payload for path, payload in images.items())
    assert restore_workspace(workspace)['restored']
    assert project.path.read_bytes() == original_project
    assert (workspace / 'scan-session.json').read_bytes() == original_session
    restored = load_session(workspace, photos)
    assert restored.assets[0].group_id == result.assets[0].group_id
    assert restored.assets[0].ai_focus_dirty == result.assets[0].ai_focus_dirty


def test_corrupt_archive_does_not_remove_state_or_overwrite_on_restore(tmp_path):
    _photos, workspace, _result, _project, _task, _batch, _export, _crops = _completed_workspace(tmp_path)
    create_legacy_workspace_archive(workspace)
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


def test_restore_refuses_to_overwrite_different_active_state(tmp_path):
    _photos, workspace, *_ = _completed_workspace(tmp_path)
    create_legacy_workspace_archive(workspace)
    active = workspace / "scan-session.json"
    active.write_bytes(b'{"newer":"active state"}')

    with pytest.raises(ValueError, match="已有不同内容"):
        restore_workspace(workspace)

    assert active.read_bytes() == b'{"newer":"active state"}'
    assert (workspace / ".workspace-archive.zip").is_file()
    assert not (workspace / "ai_project.json").exists()


@pytest.mark.parametrize("kind", ["unknown", "traversal", "symlink"])
def test_restore_rejects_unsafe_legacy_archive_members(tmp_path, kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    archive = workspace / ".workspace-archive.zip"
    payload = b"{}"
    member = "unknown.json" if kind == "unknown" else "../outside.json"
    manifest = {
        "version": 1,
        "files": {
            member: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        info = zipfile.ZipInfo(member)
        if kind == "symlink":
            member = "scan-session.json"
            info = zipfile.ZipInfo(member)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            manifest["files"] = {
                member: {
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            }
        bundle.writestr(info, payload)
        bundle.writestr(".archive-manifest.json", json.dumps(manifest))

    with pytest.raises(ValueError, match="未知文件|不安全路径|包含链接"):
        restore_workspace(workspace)

    assert archive.is_file()
    assert not (workspace / "scan-session.json").exists()
    assert not (tmp_path / "outside.json").exists()
