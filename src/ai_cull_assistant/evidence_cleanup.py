"""Inspect and quarantine image-only focus evidence without changing review state."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid
import zipfile

from PIL import Image

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_HISTORY = 'evidence-cleanup.json'


def _plain(value) -> Path:
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('路径不明确，请先选择绝对路径工作区和原照片目录。')
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('路径包含符号链接或目录联接，为保护数据未清理。')
    return path


def _hash(payload):
    return hashlib.sha256(payload).hexdigest()


def _json(path):
    _plain(path)
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('记录异常过大，未清理。')
    return json.loads(path.read_text('utf-8-sig'))


@contextmanager
def evidence_operation(workspace):
    """Nonblocking in-process and OS lock, also held throughout focus API calls."""
    root = _plain(workspace)
    root.mkdir(parents=True, exist_ok=True)
    key = str(root).casefold()
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ValueError('清晰度证据正在被任务使用，请等待任务结束。')
    stream = None
    acquired = False
    try:
        path = _plain(root / '.evidence-operation.lock')
        stream = path.open('a+b')
        if not path.stat().st_size:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise ValueError('另一个程序正在使用清晰度证据，请等待任务结束。') from exc
        yield
    finally:
        if stream is not None:
            if acquired:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
        lock.release()


def guard_focus_review(function):
    @wraps(function)
    def guarded(asset, settings, profile, root, *args, **kwargs):
        from .focus_audit import audit_root
        with evidence_operation(audit_root(root).parent):
            return function(asset, settings, profile, root, *args, **kwargs)
    return guarded


@dataclass(frozen=True)
class EvidenceFile:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class CleanupPlan:
    workspace: Path
    input_dir: Path
    files: tuple[EvidenceFile, ...]
    preserved: int
    state_digest: str
    missing_sources: int
    changed_sources: int

    @property
    def bytes(self):
        return sum(item.size for item in self.files)


def _state(root, name):
    active = _plain(root / name)
    archive = _plain(root / '.workspace-archive.zip')
    if active.is_file():
        if archive.exists():
            raise ValueError('同时存在活动记录和归档，状态不确定，未清理。')
        return _json(active)
    if not archive.is_file():
        if name == 'ai_project.json':
            return None
        raise ValueError('缺少完整扫描记录，无法证明原片可重建，未清理。')
    with zipfile.ZipFile(archive) as bundle:
        info = bundle.getinfo('.archive-manifest.json')
        if info.file_size > 1024 * 1024:
            raise ValueError('归档清单异常，未清理。')
        manifest = json.loads(bundle.read(info))
        if manifest.get('version') != 1:
            raise ValueError('归档版本不支持，未清理。')
        expected = manifest.get('files', {}).get(name)
        if expected is None and name == 'ai_project.json':
            return None
        info = bundle.getinfo(name)
        if info.file_size > 128 * 1024 * 1024:
            raise ValueError('归档记录异常过大，未清理。')
        payload = bundle.read(info)
        if not expected or len(payload) != expected.get('size') or _hash(payload) != expected.get('sha256'):
            raise ValueError('归档校验失败，未清理。')
        return json.loads(payload)


def _validate_sources(root, source, session):
    if session.get('version') != 1 or _plain(session.get('workspace_dir', '')) != root:
        raise ValueError('扫描记录与当前工作区路径不一致，未清理。')
    if _plain(session.get('input_dir', '')) != source:
        raise ValueError('原照片目录与工作区不一致，未清理。')
    if root == source or root in source.parents or source in root.parents:
        raise ValueError('工作区与原照片目录重叠，未清理。')
    inventory = session.get('source_stats')
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError('缺少完整原片清单，未清理。')
    missing = changed = 0
    for value, expected in inventory.items():
        path = _plain(value)
        if not path.is_relative_to(source):
            raise ValueError('原片清单包含照片目录以外的路径，未清理。')
        try:
            info = path.stat()
        except FileNotFoundError:
            missing += 1
            continue
        if not stat.S_ISREG(info.st_mode) or [info.st_size, info.st_mtime_ns] != expected:
            changed += 1
    return missing, changed


def inspect_evidence(workspace, input_dir) -> CleanupPlan:
    """Read-only; never call restore_workspace/load_session, even for archives."""
    root, source = _plain(workspace), _plain(input_dir)
    if not root.is_dir():
        raise ValueError('当前工作区不存在。')
    for name in ['.processing/active.json', 'cache/processing/active.json']:
        if _plain(root / name).exists():
            raise ValueError('工作区仍有处理或暂停任务，请结束任务后再清理。')
    session = _state(root, 'scan-session.json')
    project = _state(root, 'ai_project.json')
    missing, changed = _validate_sources(root, source, session)
    if project:
        for task in project.get('tasks', []):
            for batch in task.get('batches', []) + task.get('web_submissions', []):
                if str(batch.get('status', '')).lower() in {'running', 'preparing', 'in_progress'}:
                    raise ValueError('AI 任务仍在使用工作区，请结束任务后再清理。')
    request_state = {}
    requests = _plain(root / 'focus-evidence' / 'requests')
    if requests.exists():
        for path in requests.iterdir():
            _plain(path)
            if path.suffix == '.json' and path.is_file():
                request_state[path.name] = _json(path)
                if 'response' not in request_state[path.name]:
                    raise ValueError('存在尚未保存回答的清晰度请求，无法确认任务已结束，未清理。')
    blobs = _plain(root / 'focus-evidence' / 'blobs')
    files = []; preserved = 0
    if blobs.exists():
        for path in sorted(blobs.iterdir()):
            _plain(path)
            if not path.is_file() or not re.fullmatch(r'[0-9a-f]{64}\.zip', path.name) or path.stat().st_nlink != 1:
                preserved += 1
                continue
            try:
                if path.stat().st_size > 256 * 1024 * 1024:
                    raise ValueError('oversized package')
                payload = path.read_bytes()
                with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
                    infos = bundle.infolist()
                    if len(infos) != 1 or bundle.comment:
                        raise ValueError('mixed archive')
                    info = infos[0]
                    if info.filename not in {'image.png', 'image.jpg', 'image.jpeg', 'image.webp'} or info.file_size > 256 * 1024 * 1024 or stat.S_ISLNK(info.external_attr >> 16) or info.comment or info.extra:
                        raise ValueError('not an image-only evidence package')
                    image = bundle.read(info)
                    if _hash(image) != path.stem:
                        raise ValueError('payload identity mismatch')
                    with Image.open(io.BytesIO(image)) as decoded:
                        decoded.verify()
                files.append(EvidenceFile(path.name, len(payload), _hash(payload)))
            except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
                preserved += 1
    signature = _hash(json.dumps([session, project, request_state], sort_keys=True, ensure_ascii=False).encode())
    return CleanupPlan(root, source, tuple(files), preserved, signature, missing, changed)


def _write_json(path, value):
    _plain(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    _plain(path)
    temporary.replace(path)


def delete_evidence(plan: CleanupPlan) -> dict:
    """Delete the explicitly confirmed workspace-wide manual image scope."""
    with evidence_operation(plan.workspace):
        return _delete_evidence(plan)


def _delete_evidence(plan, eligible_names=None, *, action='permanent_delete'):
    """Caller holds the evidence lock; revalidate full state before filtering.

    Automatic reconciliation supplies only proven orphan names. The manual UI
    confirms the complete workspace scope, including shared historical images.
    """
    if inspect_evidence(plan.workspace, plan.input_dir) != plan:
        raise ValueError('确认期间工作区或证据已变化，请重新统计后再试。')
    if eligible_names is not None:
        from dataclasses import replace
        plan = replace(plan, files=tuple(f for f in plan.files if f.name in eligible_names))
    result = dict(deleted=0, bytes=0, failed=[])
    if not plan.files:
        return result
    marker = _plain(plan.workspace / 'focus-evidence' / _HISTORY)
    history = _json(marker) if marker.exists() else dict(version=1, operations=[])
    if history.get('version') != 1 or not isinstance(history.get('operations'), list):
        raise ValueError('证据清理记录异常，未删除。')
    event = dict(id=uuid.uuid4().hex, at=datetime.now(timezone.utc).isoformat(),
                 action=action, workspace=str(plan.workspace),
                 files=[vars(item) for item in plan.files])
    history['operations'].append(event)
    # Persist the approved scope before deleting. Even interruption leaves
    # a record explaining missing historical images, never a false backup.
    _write_json(marker, history)
    for item in plan.files:
        source = _plain(plan.workspace / 'focus-evidence' / 'blobs' / item.name)
        try:
            if _hash(source.read_bytes()) != item.sha256:
                raise ValueError('文件已变化，已保留')
            _plain(source)
            if source.stat().st_nlink != 1:
                raise ValueError('文件存在其他硬链接，已保留')
            source.unlink()
            result['deleted'] += 1
            result['bytes'] += item.size
        except (OSError, ValueError) as exc:
            result['failed'].append(f'{item.name}: {exc}')
    event['result'] = result
    _write_json(marker, history)
    return result


def quarantine_evidence(plan: CleanupPlan, destination) -> dict:
    """Revalidate after confirmation, then move only approved image ZIPs; no deletes."""
    with evidence_operation(plan.workspace):
        return _quarantine_evidence(plan, destination)


def _quarantine_evidence(plan, destination, eligible_names=None):
    """Caller holds evidence_operation; always revalidate the complete plan."""
    if inspect_evidence(plan.workspace, plan.input_dir) != plan:
        raise ValueError('确认期间工作区或证据已变化，请重新统计后再试。')
    if eligible_names is not None:
        from dataclasses import replace
        plan = replace(plan, files=tuple(f for f in plan.files if f.name in eligible_names))
    if not plan.files:
        return dict(moved=0, bytes=0, failed=[], destination=None)
    trash = _plain(destination)
    if (trash == plan.workspace or trash in plan.workspace.parents or plan.workspace in trash.parents
            or trash == plan.input_dir or trash in plan.input_dir.parents or plan.input_dir in trash.parents):
        raise ValueError('待手动删除目录与工作区或原片重叠，未清理。')
    operation_id = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    folder = trash / (operation_id + '-证据图片') / plan.workspace.name / 'focus-evidence' / 'blobs'
    _plain(folder); folder.mkdir(parents=True, exist_ok=False)
    if folder.stat().st_dev != plan.workspace.stat().st_dev:
        raise ValueError('待手动删除目录必须与工作区在同一磁盘，未清理。')
    event = dict(id=operation_id, at=datetime.now(timezone.utc).isoformat(), workspace=str(plan.workspace),
                 destination=str(folder), files=[vars(item) for item in plan.files])
    _write_json(folder.parent / 'manifest.json', event)
    marker = plan.workspace / 'focus-evidence' / _HISTORY
    history = _json(marker) if marker.exists() else dict(version=1, operations=[])
    if history.get('version') != 1 or not isinstance(history.get('operations'), list):
        raise ValueError('证据清理记录异常，未清理。')
    history['operations'].append(event)
    # Persist intent before the first move: interruption leaves a recovery map.
    _write_json(marker, history)
    result = dict(moved=0, bytes=0, failed=[], destination=str(folder.parents[2]))
    for item in plan.files:
        source = plan.workspace / 'focus-evidence' / 'blobs' / item.name
        target = folder / item.name
        try:
            _plain(source); _plain(target)
            if target.exists() or _hash(source.read_bytes()) != item.sha256:
                raise ValueError('文件已变化，已保留')
            _plain(source); _plain(target)
            if source.stat().st_nlink != 1:
                raise ValueError('文件存在其他硬链接，已保留')
            source.rename(target)
            if _hash(target.read_bytes()) != item.sha256:
                raise ValueError('移动后校验失败，请保留两处数据并检查清单')
            result['moved'] += 1; result['bytes'] += item.size
        except (OSError, ValueError) as exc:
            result['failed'].append(f'{item.name}: {exc}')
    return result


def evidence_status(workspace):
    """Explain missing historical images after cleanup, including interrupted moves."""
    try:
        root = _plain(workspace)
        marker = root / 'focus-evidence' / _HISTORY
        if not marker.exists():
            return ''
        history = _json(marker)
        names = {item['name'] for event in history['operations'] for item in event['files']
                 if re.fullmatch(r'[0-9a-f]{64}\.zip', item['name'])}
        missing = sum(not _plain(root / 'focus-evidence' / 'blobs' / name).exists() for name in names)
        return f'已清理 {missing} 个历史证据图片包；请求回答及选片结果保留。' if missing else ''
    except (OSError, ValueError, KeyError, TypeError):
        return '证据清理记录暂不可读；请保留工作区并检查迁移清单。'


def default_quarantine_root(program_dir):
    program = Path(program_dir)
    if program.parent.name == '当前版本' and program.parent.parent.name == '本地测试':
        return program.parent.parent.parent / '待手动删除'
    return program.parent / '待手动删除'


def _audit_refs(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'audit' and isinstance(child, str):
                child = child.replace('\\', '/')
                # Exact relative namespace only; never follow arbitrary paths.
                if re.fullmatch(r'focus-evidence/requests/[A-Za-z0-9_-]+\.json', child):
                    yield child
                else:
                    raise ValueError('证据引用路径不明确，未同步整理。')
            else:
                yield from _audit_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _audit_refs(child)


def sync_removed_evidence(workspace, input_dir, removed):
    """Called under evidence lock BEFORE the existing record-pruning operation.

    Legacy audit links identify owners; new manifests also retain source paths
    for older retries. Unattributed requests protect their blobs conservatively.
    """
    root, source = _plain(workspace), _plain(input_dir)
    if not source.is_dir():
        raise ValueError('照片文件夹不可访问，原记录及证据保留。')
    plan = inspect_evidence(root, source)
    session = _state(root, 'scan-session.json')
    project = _state(root, 'ai_project.json') or {}
    removed = {str(_plain(p)) for p in removed}
    if any(_plain(p).exists() for p in removed):
        raise ValueError('原片状态已变化，请重新打开工作区。')
    marker = _plain(root / 'focus-evidence' / 'source-removals.json')
    history = _json(marker) if marker.exists() else dict(version=1, sources=[], refs={})
    if history.get('version') != 1 or not isinstance(history.get('sources'), list) or not isinstance(history.get('refs'), dict):
        raise ValueError('证据同步记录异常，未同步整理。')
    historical = {_plain(p) for p in history['sources']}
    if any(not p.is_relative_to(source) for p in historical):
        raise ValueError('证据同步记录需核对原片位置，未同步整理。')
    removed |= {str(p) for p in historical if not p.exists()}
    dead_refs, live_refs = set(), set()
    for ref, owners in history['refs'].items():
        list(_audit_refs({'audit': ref}))
        if not isinstance(owners, list) or not owners:
            raise ValueError('历史证据归属异常，未同步整理。')
        (dead_refs if set(owners) <= removed else live_refs).add(ref)

    for row in session.get('assets', []) + list(project.get('photos', {}).values()):
        primary = row.get('primary_path') or row.get('path')
        refs = set(_audit_refs(row))
        (dead_refs if primary in removed else live_refs).update(refs)
        if primary in removed:
            for ref in refs:
                history['refs'][ref] = sorted(set(history['refs'].get(ref, [])) | {primary})
    live_refs.update(_audit_refs({k: v for k, v in project.items() if k != 'photos'}))
    dead_hashes, protected_hashes = set(), set()
    requests = _plain(root / 'focus-evidence' / 'requests')
    for path in requests.iterdir() if requests.exists() else []:
        _plain(path)
        if not path.is_file() or path.suffix != '.json':
            raise ValueError('证据请求目录包含未知项目，未同步整理。')
        document = _json(path)
        hashes = set()
        for item in document['images']:
            identity = item['sha256']
            if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{64}', identity):
                raise ValueError('证据图片标识异常，未同步整理。')
            hashes.add(identity + '.zip')
        ref = path.relative_to(root).as_posix()
        owners = document.get('source_paths')
        if owners is not None:
            if not isinstance(owners, list) or not owners:
                raise ValueError('证据原片归属不明确，未同步整理。')
            owners = {str(_plain(p)) for p in owners}
            if any(not Path(p).is_relative_to(source) for p in owners):
                # Relocated or legacy ownership cannot prove deletion.
                protected_hashes.update(hashes)
                continue
        dead = ref in dead_refs or (owners and owners <= removed)
        live = ref in live_refs or (owners and not owners <= removed)
        (dead_hashes if dead and not live else protected_hashes).update(hashes)
    eligible = dead_hashes - protected_hashes
    if eligible:
        if not source.is_dir() or any(_plain(p).exists() for p in removed):
            raise ValueError('原片状态已变化，未同步整理。')
        result = _delete_evidence(plan, eligible, action='source_sync_permanent_delete')
        if result['failed']:
            raise ValueError('部分证据暂不能删除，原照片记录保留，下次打开工作区将重试。')
    else:
        result = dict(deleted=0, bytes=0, failed=[])
    history['sources'] = sorted(removed)
    _write_json(marker, history)
    return result
