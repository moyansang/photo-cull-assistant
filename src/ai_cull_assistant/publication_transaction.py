"""Recoverable workspace publication, including abrupt process termination.

The journal is written before any public path moves. Rollback is repeatable and
keeps immutable backups until the original snapshot has been verified. A commit
record verifies the new snapshot before interrupted cleanup may finish. This is
process-crash recovery, not a filesystem/power-loss durability guarantee.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import re
import threading
import hashlib
import json
from pathlib import Path
import shutil

from .ai_project import atomic_json

JOURNAL = 'publication.json'
_locks = {}
_locks_guard = threading.Lock()

_PASSIVE = ('scan-session.json', 'processing-settings.json', '.source-location.json')
_OPTIONAL_PASSIVE = ('scan-review.json', 'workspace-settings.json')


class PublicationRecoveryError(RuntimeError):
    """Keep all transaction files for inspection rather than invalidate a job."""


@contextmanager
def operation(workspace: Path):
    """Serialize job execution/recovery across threads and application processes."""
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    with _locks_guard:
        lock = _locks.setdefault(str(workspace).casefold(), threading.Lock())
    if not lock.acquire(blocking=False):
        raise PublicationRecoveryError('工作区任务正在运行，请稍后重试')
    stream = None
    acquired = False
    try:
        path = workspace / '.publication-operation.lock'
        if path.is_symlink():
            raise PublicationRecoveryError('工作区锁路径异常')
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
            raise PublicationRecoveryError('另一个程序正在处理工作区，请稍后重试') from exc
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


def _snapshot(path: Path):
    if path.is_symlink():
        raise PublicationRecoveryError('发布路径不允许符号链接')
    if not path.exists():
        return None
    if path.is_file():
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        return {'file': digest.hexdigest()}
    if path.is_dir():
        return {'directory': {p.name: _snapshot(p) for p in sorted(path.iterdir())}}
    raise PublicationRecoveryError('不支持的发布文件类型')


def _validate_snapshot(value):
    if value is None:
        return
    if not isinstance(value, dict) or len(value) != 1:
        raise PublicationRecoveryError('发布快照格式错误；保留现场')
    if set(value) == {'file'} and isinstance(value['file'], str) and re.fullmatch('[0-9a-f]{64}', value['file']):
        return
    if set(value) != {'directory'} or not isinstance(value['directory'], dict):
        raise PublicationRecoveryError('发布快照格式错误；保留现场')
    for name, child in value['directory'].items():
        if not isinstance(name, str) or not name or name in {'.', '..'} or '/' in name or '\\' in name or child is None:
            raise PublicationRecoveryError('发布目录快照错误；保留现场')
        _validate_snapshot(child)


def _remove(path: Path):
    if path.is_symlink():
        raise PublicationRecoveryError('恢复路径不允许符号链接')
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def pending(root: Path) -> bool:
    backup = root / 'backup'
    return (root / JOURNAL).exists() or (backup.exists() and any(backup.iterdir()))


def _paths(root, workspace, job_id, entry):
    name = entry.get('destination')
    allowed = {'contact_sheets', 'screening_results.json', 'previews',
               'previews/' + job_id, 'groups.json', *_PASSIVE, *_OPTIONAL_PASSIVE}
    if name not in allowed:
        raise PublicationRecoveryError('发布记录路径不匹配；保留现场')
    destination = workspace / name
    source_name = entry.get('source')
    allowed_source = None if name in (*_PASSIVE, *_OPTIONAL_PASSIVE) else ('previews' if name.startswith('previews') else name)
    if source_name != allowed_source:
        raise PublicationRecoveryError('发布暂存路径不匹配；保留现场')
    source = root / 'output' / source_name if source_name else None
    index = entry.get('index')
    if type(index) is not int or not 0 <= index < 16:
        raise PublicationRecoveryError('发布备份索引错误；保留现场')
    backup = root / 'backup' / f'{index:03d}-{destination.name}'
    for path, boundary in [(destination, workspace), (backup, root), *(([(source, root)] if source else []))]:
        if not path.resolve().is_relative_to(boundary.resolve()) or path.is_symlink():
            raise PublicationRecoveryError('发布路径超出工作区；保留现场')
    return source, destination, backup


def begin(root: Path, workspace: Path, job_id: str, destinations, *, passive=()):
    if pending(root):
        raise PublicationRecoveryError('存在未恢复的发布事务；保留现场')
    if any(name not in _OPTIONAL_PASSIVE for name in passive) or len(set(passive)) != len(passive):
        raise PublicationRecoveryError('不支持的发布JSON目标')
    entries = []
    for index, (source, destination) in enumerate(list(destinations) + [(None, workspace / n) for n in (*_PASSIVE, *passive)]):
        entry = {'index': index, 'source': source.relative_to(root / 'output').as_posix() if source else None,
                 'destination': destination.relative_to(workspace).as_posix(),
                 'old': _snapshot(destination), 'staged': _snapshot(source) if source else None}
        _paths(root, workspace, job_id, entry)
        entries.append(entry)
    state = {'version': 1, 'job_id': job_id, 'phase': 'publishing', 'entries': entries}
    (root / 'backup').mkdir(exist_ok=True)
    atomic_json(root / JOURNAL, state)
    return state


def write_json(root, workspace, state, path, value, writer=atomic_json):
    """Record exact expected passive bytes before atomic replacement."""
    path = Path(path)
    entry = next((e for e in state['entries'] if workspace / e['destination'] == path), None)
    if entry is None or entry['source'] is not None:
        raise PublicationRecoveryError('发布JSON目标不在事务内')
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    # atomic_json uses text mode, whose newline translation differs on Windows.
    if os.linesep != '\n':
        payload = payload.replace(b'\n', os.linesep.encode())
    entry['written'] = {'file': hashlib.sha256(payload).hexdigest()}
    atomic_json(root / JOURNAL, state)
    writer(path, value)


def commit(root: Path, workspace: Path, job_id: str, state):
    for entry in state['entries']:
        _, destination, _ = _paths(root, workspace, job_id, entry)
        actual = _snapshot(destination)
        expected = entry['staged'] if entry['source'] else entry.get('written')
        if actual != expected:
            raise PublicationRecoveryError('发布结果与预期内容不一致；保留现场')
        entry['new'] = actual
    state['phase'] = 'committed'
    atomic_json(root / JOURNAL, state)


def recover(root: Path, workspace: Path, job_id: str) -> str:
    journal = root / JOURNAL
    if not journal.exists():
        if pending(root):
            raise PublicationRecoveryError('旧发布备份缺少事务记录；保留现场，请勿清除')
        return 'none'
    try:
        state = json.loads(journal.read_text('utf-8'))
        if state.get('version') != 1 or state.get('job_id') != job_id:
            raise PublicationRecoveryError('发布记录身份错误；保留现场')
        entries = state['entries']
        if not isinstance(entries, list) or not entries or len(entries) > 9:
            raise PublicationRecoveryError('发布记录条目错误；保留现场')
        if len({e['destination'] for e in entries}) != len(entries) or [e['index'] for e in entries] != list(range(len(entries))):
            raise PublicationRecoveryError('发布记录重复或顺序错误；保留现场')
        names = {e['destination'] for e in entries}
        sources = [e['source'] for e in entries if e['source'] is not None]
        if not set(_PASSIVE).issubset(names) or 'contact_sheets' not in names or len(sources) != len(set(sources)):
            raise PublicationRecoveryError('发布记录不完整或暂存重复；保留现场')
        if any(a != b and b.startswith(a + '/') for a in names for b in names):
            raise PublicationRecoveryError('发布目标重叠；保留现场')
        for entry in entries:
            for key in ('old', 'staged'):
                _validate_snapshot(entry[key])
            for key in ('new', 'written'):
                if key in entry:
                    _validate_snapshot(entry[key])
        paths = [_paths(root, workspace, job_id, e) for e in entries]
        phase = state.get('phase')
        if phase == 'committed':
            for entry, (_, destination, _) in zip(entries, paths):
                if 'new' not in entry or _snapshot(destination) != entry['new']:
                    raise PublicationRecoveryError('已发布文件与提交记录不一致；保留现场')
            return 'committed'
        if phase not in {'publishing', 'rollback_old', 'rolled_back'}:
            raise PublicationRecoveryError('未知发布阶段；保留现场')
        # Once rolled back, backups may already be partly removed by cleanup.
        # Verify restored originals/staging below, not the disposable remainder.
        if phase != 'rolled_back':
            # Refuse external edits before any recovery mutation.
            for entry, (_, destination, backup) in zip(entries, paths):
                expected = [None, entry['old'], entry['staged']]
                if 'written' in entry:
                    expected.append(entry['written'])
                if _snapshot(destination) not in expected:
                    raise PublicationRecoveryError('工作区文件已被其他操作修改；保留现场')
                if backup.exists() and _snapshot(backup) != entry['old']:
                    raise PublicationRecoveryError('原发布备份已变化；保留现场')
        if phase == 'publishing':
            # Return every moved new artifact before restoring any old path.
            # An atomic rename is either wholly before or after this operation.
            for entry, (source, destination, _) in zip(entries, paths):
                expected = entry['staged']
                if source is None or expected is None:
                    continue
                if source.exists():
                    if _snapshot(source) != expected:
                        raise PublicationRecoveryError('暂存文件已变化；保留现场')
                elif _snapshot(destination) == expected:
                    source.parent.mkdir(parents=True, exist_ok=True)
                    destination.replace(source)
                else:
                    raise PublicationRecoveryError('发布文件无法定位；保留现场')
            state['phase'] = 'rollback_old'
            atomic_json(journal, state)
        if state['phase'] == 'rollback_old':
            for entry, (_, destination, backup) in zip(entries, paths):
                old = entry['old']
                if _snapshot(destination) == old:
                    continue
                if old is None:
                    # Never recursively remove a public directory in place:
                    # atomic displacement also survives interruption mid-cleanup.
                    discarded = root / f'recovery-discard-{entry["index"]}'
                    _remove(discarded)
                    destination.replace(discarded)
                elif backup.exists():
                    if _snapshot(backup) != old:
                        raise PublicationRecoveryError('原发布备份已变化；保留现场')
                    # Copy, never consume the sole old snapshot: rollback itself
                    # may be interrupted between removing and replacing a path.
                    temporary = root / f'recovery-copy-{entry["index"]}'
                    _remove(temporary)
                    if backup.is_dir():
                        shutil.copytree(backup, temporary)
                    else:
                        shutil.copy2(backup, temporary)
                    if destination.exists():
                        discarded = root / f'recovery-discard-{entry["index"]}'
                        _remove(discarded)
                        destination.replace(discarded)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary.replace(destination)
                elif _snapshot(destination) != old:
                    raise PublicationRecoveryError('原发布文件和备份均不可验证；保留现场')
            state['phase'] = 'rolled_back'
            atomic_json(journal, state)
        for entry, (source, destination, _) in zip(entries, paths):
            if _snapshot(destination) != entry['old'] or (source is not None and _snapshot(source) != entry['staged']):
                raise PublicationRecoveryError('恢复快照校验失败；保留现场')
        _remove(root / 'backup')
        for entry in entries:
            _remove(root / f'recovery-discard-{entry["index"]}')
            _remove(root / f'recovery-copy-{entry["index"]}')
        journal.unlink()
        return 'rolled_back'
    except PublicationRecoveryError:
        raise
    except Exception as exc:
        raise PublicationRecoveryError('发布恢复失败；保留全部事务文件') from exc
