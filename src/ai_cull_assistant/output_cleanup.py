"""Safely clear generated contact sheets and the persisted application log."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile

from .workspace_layout import workspace_path


_PUBLIC_PAGE = re.compile(r"(?:sheet|contact_sheet)_\d{3,}(?:_[^/\\]+)?\.jpg", re.IGNORECASE)
_TASK_ID = re.compile(r"[0-9a-f]{32}")
_BATCH_ID = re.compile(r"B\d{3,}")
_WEB_ID = re.compile(r"W\d{3,}")
_TASK_PAGE = re.compile(r"sheet_\d{3,}(?:_[^/\\]+)?\.jpg", re.IGNORECASE)
_WEB_PAGE = re.compile(r"B\d{3,}_sheet_\d{3,}\.jpg", re.IGNORECASE)


@dataclass(frozen=True)
class CleanupResult:
    removed_contact_sheets: int
    log_removed: bool
    session_updated: bool
    errors: tuple[str, ...]


def _safe_file(path: Path, workspace: Path) -> bool:
    """Accept an ordinary file only when its resolved target remains in workspace."""
    try:
        resolved = path.resolve(strict=True)
        return (
            path.is_file()
            and not path.is_symlink()
            and resolved == path.absolute()
            and resolved.is_relative_to(workspace)
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _safe_directory(path: Path, workspace: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
        return (
            path.is_dir()
            and not path.is_symlink()
            and resolved == path.absolute()
            and resolved.is_relative_to(workspace)
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _matching_files(folder: Path, workspace: Path, pattern: re.Pattern[str]):
    if not _safe_directory(folder, workspace):
        return
    try:
        children = tuple(folder.iterdir())
    except OSError:
        return
    for child in children:
        if pattern.fullmatch(child.name) and _safe_file(child, workspace):
            yield child


def _generated_contact_sheets(workspace: Path):
    public = workspace / "contact_sheets"
    yield from _matching_files(public, workspace, _PUBLIC_PAGE)
    for name in ("main", "rejected_review"):
        yield from _matching_files(public / name, workspace, _PUBLIC_PAGE)

    task_root = workspace / "ai_tasks"
    if not _safe_directory(task_root, workspace):
        return
    try:
        task_dirs = tuple(task_root.iterdir())
    except OSError:
        return
    for task_dir in task_dirs:
        if not _TASK_ID.fullmatch(task_dir.name) or not _safe_directory(task_dir, workspace):
            continue
        try:
            children = tuple(task_dir.iterdir())
        except OSError:
            continue
        for batch_dir in children:
            if _BATCH_ID.fullmatch(batch_dir.name):
                yield from _matching_files(batch_dir, workspace, _TASK_PAGE)
        web_root = task_dir / "web"
        if not _safe_directory(web_root, workspace):
            continue
        try:
            submissions = tuple(web_root.iterdir())
        except OSError:
            continue
        for submission in submissions:
            if _WEB_ID.fullmatch(submission.name) and _safe_directory(submission, workspace):
                yield from _matching_files(submission / "images", workspace, _WEB_PAGE)


def _read_saved_session(workspace: Path) -> dict | None:
    session_path = workspace / "scan-session.json"
    if not session_path.exists():
        return None
    if not _safe_file(session_path, workspace):
        raise OSError("扫描记录不在当前工作区的安全范围内")
    data = json.loads(session_path.read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("扫描记录格式无效")
    return data


def _invalidate_saved_pages(workspace: Path, data: dict | None) -> bool:
    if data is None:
        return False
    session_path = workspace / "scan-session.json"
    data["main_pages"] = []
    data["rejected_pages"] = []
    descriptor, name = tempfile.mkstemp(prefix=".scan-session-clear-", suffix=".tmp", dir=workspace)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        temporary.replace(session_path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return True


def _protected_paths(data: dict | None, extra_paths) -> set[Path]:
    values = list(extra_paths)
    if data:
        source_stats = data.get("source_stats")
        if isinstance(source_stats, dict):
            values.extend(source_stats)
        for asset in data.get("assets", []) if isinstance(data.get("assets"), list) else []:
            if not isinstance(asset, dict):
                continue
            for key in ("display_path", "primary_path", "raw_path", "jpg_path"):
                if asset.get(key):
                    values.append(asset[key])
            if isinstance(asset.get("rating_target_paths"), list):
                values.extend(asset["rating_target_paths"])
    protected = set()
    for value in values:
        try:
            protected.add(Path(value).resolve())
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    return protected


def clear_generated_outputs(workspace: str | Path, *, protected_paths=()) -> CleanupResult:
    """Delete only recognised generated sheets, clear the log, and invalidate saved pages.

    Source photos, previews, analysis data, grouping/crop settings, AI replies, and
    Lightroom exports are outside the enumerated locations and are left untouched.
    """
    root = Path(workspace).resolve()
    removed = 0
    errors: list[str] = []
    session_data = None
    session_safe = True
    try:
        session_data = _read_saved_session(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        session_safe = False
        errors.append(f"{root / 'scan-session.json'}: {exc}")

    protected = _protected_paths(session_data, protected_paths)
    if session_safe:
        for path in tuple(_generated_contact_sheets(root)):
            if path.resolve() in protected:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                errors.append(f"{path}: {exc}")

    session_updated = False
    if session_safe:
        try:
            session_updated = _invalidate_saved_pages(root, session_data)
        except (OSError, ValueError) as exc:
            errors.append(f"{root / 'scan-session.json'}: {exc}")

    log_path = workspace_path(root, "session.log")
    log_removed = False
    try:
        if log_path.exists():
            if not _safe_file(log_path, root):
                raise OSError("日志不在当前工作区的安全范围内")
            log_path.unlink()
            log_removed = True
    except OSError as exc:
        errors.append(f"{log_path}: {exc}")

    return CleanupResult(removed, log_removed, session_updated, tuple(errors))
