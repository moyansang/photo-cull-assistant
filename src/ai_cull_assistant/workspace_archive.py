"""Restore workspace archives created by older application versions."""
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


ARCHIVE_NAME = ".workspace-archive.zip"
STATE_NAME = ".workspace-storage.json"
_ARCHIVE_MANIFEST = ".archive-manifest.json"
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
