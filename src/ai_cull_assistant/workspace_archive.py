"""Safely compact completed workspaces and restore their persisted state.

Only application-owned files are touched.  Original photos, contact sheets, AI
submission snapshots, and Lightroom exports always remain outside the archive.
"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import uuid
import zipfile

from .ai_project import atomic_json
from .workspace_layout import workspace_path


ARCHIVE_NAME = ".workspace-archive.zip"
STATE_NAME = ".workspace-storage.json"
_ARCHIVE_MANIFEST = ".archive-manifest.json"
_DONE = {"complete", "completed", "done", "已完成"}
_STATE_FILES = (
    "scan-session.json",
    "ai_project.json",
    "groups.json",
    "screening_results.json",
    "processing-settings.json",
    "workspace-settings.json",
    "workflow-stage.json",
)
_ALLOWED_ARCHIVE_NAMES = frozenset(_STATE_FILES) | {"session.log", "logs/session.log"}
_MAX_ARCHIVED_BYTES = 512 * 1024 * 1024


def _notify(callback: Callable[[int], None] | None, percent: int) -> None:
    if callback is not None:
        try:
            callback(max(0, min(100, int(percent))))
        except Exception:
            pass


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"工作区文件格式无效：{path.name}")
    return value


def _contains_link(path: Path) -> bool:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return True
    if not path.is_dir():
        return False
    return any(item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction())
               for item in path.rglob("*"))


def _assert_owned(root: Path, path: Path) -> None:
    root = root.resolve()
    try:
        relative = path.absolute().relative_to(root)
    except ValueError as exc:
        raise ValueError("拒绝操作工作区以外的路径") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() and (current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction())):
            raise ValueError(f"工作区数据包含链接，未自动整理：{current}")
    if _contains_link(path):
        raise ValueError(f"工作区数据包含链接，未自动整理：{path}")


def _active_processing_job(workspace: Path) -> bool:
    for candidate in (workspace / ".processing" / "active.json", workspace / "cache" / "processing" / "active.json"):
        if candidate.is_symlink():
            return True
        if candidate.is_file():
            return True
    return False


def _eligible(workspace: Path) -> tuple[bool, str]:
    if _active_processing_job(workspace):
        return False, "工作区仍有未完成任务"
    project_path = workspace / "ai_project.json"
    export_path = workspace_path(workspace, "lightroom_results.json")
    if not project_path.is_file() or not export_path.is_file():
        return False, "尚未完成 AI 选片并导出 Lightroom 结果"
    try:
        project = _read_json(project_path)
        exported = _read_json(export_path)
        session = _read_json(workspace / "scan-session.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"工作区结果无法验证：{exc}"
    try:
        input_dir = Path(session["input_dir"]).resolve()
    except (KeyError, TypeError, OSError) as exc:
        return False, f"扫描记录无法验证：{exc}"
    # A custom workspace may have been placed inside the source tree.  In that
    # case even a directory named ``previews`` cannot safely be assumed owned.
    if workspace == input_dir or workspace in input_dir.parents or input_dir in workspace.parents:
        return False, "工作区与照片文件夹重叠，未自动整理"
    stage_path = workspace / "workflow-stage.json"
    if stage_path.is_file():
        try:
            if _read_json(stage_path).get("contact_sheets_ready") is not True:
                return False, "联系表尚未按当前结果生成"
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return False, f"流程状态无法验证：{exc}"
    export_id = project.get("last_export_id")
    if not export_id or project.get("export_dirty") is True or exported.get("export_id") != export_id:
        return False, "Lightroom 导出结果不是当前版本"
    tasks = project.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return False, "尚未建立 AI 选片任务"
    current_id = project.get("current_task_id")
    current = next((task for task in tasks if isinstance(task, dict) and task.get("id") == current_id), None)
    if current is None:
        return False, "找不到当前 AI 选片任务"
    batches = current.get("batches")
    if not isinstance(batches, list) or any(not isinstance(batch, dict) or batch.get("status") not in _DONE for batch in batches):
        return False, "AI 选片仍有未完成批次"
    if any(isinstance(photo, dict) and photo.get("stale") for photo in project.get("photos", {}).values()):
        return False, "AI 选片结果已经过时"
    try:
        if not _analysis_matches_project(workspace, session, project):
            return False, "当前分组、人脸设置或照片状态尚未重新评审导出"
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return False, f"当前分析无法验证：{exc}"
    return True, ""


def _analysis_matches_project(workspace: Path, session: dict, project: dict) -> bool:
    """Verify persisted scan/crop state against the fingerprints AI reviewed."""
    from .ai_project import fingerprint, photo_id
    from .crop_settings import CropSettings
    from .session_store import load_session

    input_dir = Path(session["input_dir"])
    result = load_session(workspace, input_dir)
    if result is None or any(asset.ai_focus_dirty for asset in result.assets):
        return False
    settings_path = workspace / "workspace-settings.json"
    settings = _read_json(settings_path) if settings_path.is_file() else {}
    crops = CropSettings.from_dict(settings.get("face_crop", {}))
    groups: dict[int, list[tuple[str, str]]] = {}
    for asset in result.assets:
        groups.setdefault(asset.group_id, []).append((photo_id(asset), fingerprint(asset, crops)))
    expected = {}
    for asset in result.assets:
        pid = photo_id(asset)
        expected[pid] = hashlib.sha256(json.dumps(sorted(groups[asset.group_id])).encode()).hexdigest()
    photos = project.get("photos")
    if not isinstance(photos, dict) or set(photos) != set(expected):
        return False
    for pid, current in expected.items():
        photo = photos.get(pid)
        if not isinstance(photo, dict) or photo.get("fingerprint") != current:
            return False
        if photo.get("technical_rejected") or photo.get("technical_reason"):
            continue
        final = photo.get("final", {})
        human_current = (isinstance(final, dict) and final.get("confirmed") is True
                         and final.get("fingerprint") == current)
        ai = photo.get("ai", {})
        ai_current = isinstance(ai, dict) and ai.get("fingerprint") == current
        if not (human_current or ai_current):
            return False
    return True


def _archive_candidates(workspace: Path) -> list[tuple[Path, str]]:
    candidates = [(workspace / name, name) for name in _STATE_FILES]
    candidates.extend([
        (workspace / "session.log", "session.log"),
        (workspace / "logs" / "session.log", "logs/session.log"),
    ])
    return [(path, name) for path, name in candidates if path.is_file()]


def compact_workspace(workspace: str | Path, progress: Callable[[int], None] | None = None) -> dict:
    """Compact a finished workspace, returning counts and reclaimed bytes.

    Ineligible workspaces are left untouched and return ``compacted=False`` with
    a human-readable reason.
    """
    root = Path(workspace).resolve()
    stats = {"compacted": False, "reason": "", "archived_files": 0,
             "removed_previews": 0, "removed_bytes": 0, "archive_bytes": 0}
    if not root.is_dir():
        stats["reason"] = "工作区不存在"
        return stats
    _notify(progress, 0)
    ok, reason = _eligible(root)
    if not ok:
        stats["reason"] = reason
        return stats
    archive = root / ARCHIVE_NAME
    state_path = root / STATE_NAME
    if archive.exists():
        stats["reason"] = "工作区已经压缩"
        return stats
    candidates = _archive_candidates(root)
    for path, _ in candidates:
        _assert_owned(root, path)
    preview_roots = [root / "previews"]
    cache_roots = [root / ".analysis-cache", root / "cache" / "analysis"]
    removable = [path for path in preview_roots + cache_roots if path.exists()]
    for path in removable:
        _assert_owned(root, path)

    manifest = {"version": 1, "files": {}}
    temporary = root / f".{ARCHIVE_NAME}.{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            total = max(1, len(candidates))
            for index, (source, archive_name) in enumerate(candidates, 1):
                payload = source.read_bytes()
                manifest["files"][archive_name] = {
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                bundle.writestr(archive_name, payload)
                _notify(progress, 5 + 35 * index // total)
            bundle.writestr(_ARCHIVE_MANIFEST, json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        with zipfile.ZipFile(temporary, "r") as bundle:
            if bundle.testzip() is not None:
                raise ValueError("工作区压缩包校验失败")
            saved_manifest = json.loads(bundle.read(_ARCHIVE_MANIFEST))
            if saved_manifest != manifest:
                raise ValueError("工作区压缩清单校验失败")
            for name, expected in manifest["files"].items():
                payload = bundle.read(name)
                if len(payload) != expected["size"] or hashlib.sha256(payload).hexdigest() != expected["sha256"]:
                    raise ValueError("工作区压缩内容校验失败")
        temporary.replace(archive)
        _notify(progress, 50)

        preview_count = 0
        removed_bytes = 0
        for path in removable:
            files = [item for item in path.rglob("*") if item.is_file()]
            removed_bytes += sum(item.stat().st_size for item in files)
            if path in preview_roots:
                preview_count += len(files)
            shutil.rmtree(path)
        for source, _ in candidates:
            removed_bytes += source.stat().st_size
            source.unlink()
        atomic_json(state_path, {
            "version": 1,
            "status": "compacted",
            "archive": ARCHIVE_NAME,
            "preview_removed": True,
            "archived_files": len(candidates),
        })
        stats.update(compacted=True, archived_files=len(candidates),
                     removed_previews=preview_count, removed_bytes=removed_bytes,
                     archive_bytes=archive.stat().st_size)
        _notify(progress, 100)
        return stats
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _safe_member(name: str) -> Path:
    pure = PurePosixPath(name)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("工作区压缩包包含不安全路径")
    return Path(*pure.parts)


def restore_workspace(workspace: str | Path, progress: Callable[[int], None] | None = None) -> dict:
    """Restore archived state files while leaving regenerated previews absent."""
    root = Path(workspace).resolve()
    archive = root / ARCHIVE_NAME
    state_path = root / STATE_NAME
    if not archive.is_file():
        return {"restored": False, "restored_files": 0, "preview_removed": previews_were_compacted(root)}
    _assert_owned(root, archive)
    _notify(progress, 0)
    stage = root / f".workspace-restore-{uuid.uuid4().hex}"
    stage.mkdir(exist_ok=False)
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            infos = bundle.infolist()
            for info in infos:
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError("工作区压缩包包含链接")
                _safe_member(info.filename)
            if sum(info.file_size for info in infos) > _MAX_ARCHIVED_BYTES:
                raise ValueError("工作区压缩包内容异常过大")
            if bundle.testzip() is not None:
                raise ValueError("工作区压缩包已损坏")
            manifest = json.loads(bundle.read(_ARCHIVE_MANIFEST))
            files = manifest.get("files") if isinstance(manifest, dict) else None
            if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(files, dict):
                raise ValueError("工作区压缩清单版本不支持")
            if not set(files).issubset(_ALLOWED_ARCHIVE_NAMES):
                raise ValueError("工作区压缩包包含未知文件")
            if set(files) | {_ARCHIVE_MANIFEST} != {info.filename for info in infos}:
                raise ValueError("工作区压缩清单与内容不一致")
            total = max(1, len(files))
            for index, (name, expected) in enumerate(files.items(), 1):
                relative = _safe_member(name)
                payload = bundle.read(name)
                if (not isinstance(expected, dict) or len(payload) != expected.get("size")
                        or hashlib.sha256(payload).hexdigest() != expected.get("sha256")):
                    raise ValueError("工作区压缩内容校验失败")
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                _notify(progress, 10 + 60 * index // total)
        # Refuse the whole restore before moving anything when the user or a
        # previous process has created different state at one destination.
        for name in files:
            relative = _safe_member(name)
            source = stage / relative
            target = root / relative
            _assert_owned(root, target)
            if target.exists() and (not target.is_file() or target.read_bytes() != source.read_bytes()):
                raise ValueError(f"工作区已有不同内容，未覆盖：{relative}")
        restored = 0
        for name in files:
            relative = _safe_member(name)
            source = stage / relative
            target = root / relative
            _assert_owned(root, target)
            if target.exists():
                source.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)
            restored += 1
        atomic_json(state_path, {
            "version": 1,
            "status": "active",
            "preview_removed": True,
            "restored_files": restored,
        })
        archive.unlink()
        _notify(progress, 100)
        return {"restored": True, "restored_files": restored, "preview_removed": True}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def previews_were_compacted(workspace: str | Path) -> bool:
    try:
        state = _read_json(Path(workspace) / STATE_NAME)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return state.get("version") == 1 and state.get("preview_removed") is True
