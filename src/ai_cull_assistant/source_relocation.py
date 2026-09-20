"""Explicit, verified source-root relocation without discarding analysis."""
from __future__ import annotations

import json
from pathlib import Path
import uuid

from .project_storage import _atomic, _key

TRANSACTION = '.source-relocation-transaction.json'


def _owned(root, relative):
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('工作区记录包含不安全的相对路径')
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('工作区记录指向目录外，未迁移')
    current = path
    while current != root:
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise ValueError('工作区包含链接，未迁移')
        current = current.parent
    return path


def recover_relocation(workspace):
    """Roll back an interrupted multi-file publication before reading state."""
    root = Path(workspace).resolve()
    marker = _owned(root, TRANSACTION)
    if not marker.exists():
        return
    record = json.loads(marker.read_text('utf-8'))
    for relative, saved in record['files'].items():
        target = _owned(root, relative)
        if saved is None:
            target.unlink(missing_ok=True)
        else:
            original = _owned(root, saved).read_bytes()
            temporary = target.with_name(target.name + '.relocation-restore')
            temporary.write_bytes(original)
            temporary.replace(target)
    marker.unlink()


def recorded_source(workspace):
    root = Path(workspace).resolve()
    recover_relocation(root)
    from .workspace_archive import restore_workspace
    restore_workspace(root)
    for name in ('scan-session.json', 'workspace-identity.json',
                 'cache/processing/active.json', '.processing/active.json'):
        path = _owned(root, name)
        if path.is_file():
            source = json.loads(path.read_text('utf-8')).get('input_dir')
            if source:
                return source
    return None


def _state_files(root):
    # Never rewrite logs, raw model answers, credentials, or prior backups.
    names = ('scan-session.json', 'workspace-identity.json', 'workspace-settings.json',
             'ai_project.json', 'groups.json', 'screening_results.json',
             'processing-settings.json', 'workflow-stage.json', 'lightroom_results.json',
             '.source-location.json')
    for name in names:
        path = _owned(root, name)
        if path.is_file():
            yield path
    for name in ('cache/processing', '.processing', 'exports'):
        directory = _owned(root, name)
        if directory.is_dir():
            for path in directory.rglob('*.json'):
                yield _owned(root, path.relative_to(root))


def relocate_source(workspace, new_source, settings_dir):
    root = Path(workspace).resolve()
    new = Path(new_source).resolve()
    old_value = recorded_source(root)
    if not old_value:
        raise ValueError('此工作区没有可重新定位的照片记录')
    old = Path(old_value)
    if not new.is_dir():
        raise ValueError('新的照片文件夹不可访问')
    if new == root or new in root.parents or root in new.parents:
        raise ValueError('照片文件夹与工作区不能互相包含')
    documents = {p: json.loads(p.read_text('utf-8')) for p in _state_files(root)}
    session = documents.get(root / 'scan-session.json', {})
    jobs = [v for p, v in documents.items() if p.name == 'job.json']
    inventory = dict(session.get('source_stats', {}))
    for job in jobs:
        for name, stats in job.get('source_stats', {}).items():
            if name in inventory and inventory[name] != stats:
                raise ValueError('扫描记录和中断任务的原片信息不一致，未迁移')
            inventory[name] = stats
    if not inventory:
        raise ValueError('工作区没有照片校验记录，无法安全重新定位')
    old_workspace = Path(session.get('workspace_dir') or
                         (jobs[0].get('workspace') if jobs else None) or root)

    def move_path(value):
        if not isinstance(value, str) or not Path(value).is_absolute():
            return value
        candidate = Path(value)
        for before, after in ((old, new), (old_workspace, root)):
            try:
                relative = candidate.relative_to(before)
            except ValueError:
                continue
            destination = after / relative
            if not destination.resolve().is_relative_to(after.resolve()):
                raise ValueError('照片记录包含无效的相对路径')
            return str(destination)
        return value

    matched = 0
    missing = []
    for name, expected in inventory.items():
        try:
            Path(name).relative_to(old)
        except ValueError as exc:
            raise ValueError('照片清单中有不属于原照片目录的文件') from exc
        target = Path(move_path(name))
        if not target.is_file():
            missing.append(name)
            continue
        st = target.stat()
        if [st.st_size, st.st_mtime_ns] != expected:
            raise ValueError(f'照片信息不一致，未修改工作区：{target.name}。请选择同一批原片；文件大小或修改时间已经变化。')
        matched += 1
    if not matched:
        raise ValueError('没有匹配的原照片，未修改工作区')
    if missing and jobs:
        raise ValueError(f'有 {len(missing)} 个原文件缺失，中断任务暂不能迁移；请补齐原片后重试，已有进度保留')

    def transform(value, field=None):
        if field == 'source_identity_paths':
            return {move_path(k).casefold(): v for k, v in value.items()}
        if field == 'clarity_evidence':
            return value  # Immutable analysis evidence is part of the AI fingerprint.
        if isinstance(value, dict):
            result = {(move_path(k).casefold() if k == k.casefold() else move_path(k)):
                      transform(v, k) for k, v in value.items()}
            # Existing AI IDs and fingerprints must remain valid after relocation.
            if 'primary_path' in value and 'captured_at' in value and 'stem' in value:
                identities = result.setdefault('source_identity_paths', {})
                for key in ('primary_path', 'raw_path', 'jpg_path', 'display_path'):
                    original = value.get(key)
                    if original:
                        identities.setdefault(move_path(original).casefold(), original)
            return result
        if isinstance(value, list):
            return [transform(v, field) for v in value]
        return move_path(value)

    changes = {p: transform(v) for p, v in documents.items()}
    changes[root / '.source-location.json'] = {
        'version': 1, 'input_dir': str(new),
        'files': {str(Path(name).relative_to(old)): stats for name, stats in inventory.items()},
    }
    backup = _owned(root, '.source-relocation-backups/' + uuid.uuid4().hex)
    backup.mkdir(parents=True)
    backups = {}
    for path in changes:
        relative = str(path.relative_to(root))
        if path.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(path.read_bytes())
            backups[relative] = str(saved.relative_to(root))
        else:
            backups[relative] = None
    _atomic(root / TRANSACTION, {'files': backups})
    try:
        for path, value in changes.items():
            _atomic(path, value)
    except Exception:
        recover_relocation(root)
        raise
    (root / TRANSACTION).unlink()

    from .settings import read_values, save_values
    registry = read_values(settings_dir).get('workspaces', {})
    registry = registry if isinstance(registry, dict) else {}
    registry = {k: v for k, v in registry.items() if _key(v) != _key(root)}
    registry[_key(new)] = str(root)
    save_values(settings_dir, {'workspaces': registry, 'input': str(new), 'workspace': str(root)})
    return {'matched': matched, 'missing': len(missing), 'workspace': str(root), 'input_dir': str(new)}
