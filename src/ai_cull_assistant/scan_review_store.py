"""Local scan questions and explicit recheck history; never scores or writes photos.

Legacy measurement fields remain absent. Source stats below are feedback-time
identity observations, NOT retrospective measurement receipts.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
import copy
import hashlib
import json
import math
import uuid

from .ai_project import atomic_json, photo_id
from .evidence_cleanup import evidence_operation
from .models import source_identity_path


class ReviewConflict(ValueError):
    """The caller must refresh rather than overwrite a newer record."""


def _plain(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f'不能序列化证据类型：{type(value).__name__}')


def _digest(value):
    return hashlib.sha256(json.dumps(_plain(value), ensure_ascii=False,
                                     sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()


def asset_key(asset):
    # Do not change legacy AI IDs. Add a full case-sensitive identity discriminator.
    return photo_id(asset) + ':' + _digest(source_identity_path(asset, asset.primary_path))


def _sources(asset):
    rows = []
    for path in asset.rating_target_paths:
        path = Path(path)
        row = {'path': str(path), 'identity_path': source_identity_path(asset, path)}
        try:
            stat = path.stat()
            row['feedback_observed_stat'] = [stat.st_size, stat.st_mtime_ns]
        except OSError as exc:
            row['feedback_observed_stat'] = None
            row['availability'] = type(exc).__name__
        rows.append(row)
    return rows


def snapshot_asset(asset):
    names = ('local_recommendation', 'auto_rejected', 'screening_reason', 'focus_score',
             'subject_features', 'subject_checked', 'clarity_version', 'clarity_evidence',
             'focus_scope', 'ai_focus_result', 'ai_focus_dirty', 'ai_focus_stale',
             'ai_focus_attempt', 'person_review_pending', 'analysis_mode')
    result = {'photo_id': photo_id(asset), 'asset_key': asset_key(asset),
              'sources': _sources(asset), 'recorded': {name: _plain(getattr(asset, name, None))
                                                    for name in names},
              'identity_gaps': [], 'historical_receipt_reconstructed': False}
    if not getattr(asset, 'clarity_version', None):
        result['identity_gaps'].append('原算法版本未记录')
    if not getattr(asset, 'focus_scope', None):
        result['identity_gaps'].append('原范围版本未记录')
    evidence = getattr(asset, 'clarity_evidence', None)
    if not isinstance(evidence, dict) or not evidence.get('run_id'):
        result['identity_gaps'].append('原测量运行标识未记录；反馈快照版本不是测量收据')
    result['asset_version'] = _digest(result)
    return result


def _now():
    return datetime.now(timezone.utc).isoformat()


def _validate_region(region):
    if region is None:
        return None
    if not isinstance(region, dict):
        raise ValueError('关注范围必须是对象')
    result = _plain(region)
    # UI uses normalized xyxy; retain other recorded coordinate contracts as-is.
    box = result.get('box', result.get('rect'))
    if box is not None:
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)):
            raise ValueError('关注范围须为有效归一化矩形，不能越界')
    result['status'] = 'draft'
    result['measurement_supported'] = result.get('kind') == 'face'
    return result


def _shared_snapshot(snapshot):
    if snapshot is None:
        return None
    recorded = snapshot['recorded']
    evidence = recorded.get('clarity_evidence') or {}
    allowed_states = {'keep', 'reject', 'review', 'clear', 'uncertain', 'blur',
                      'severe_blur', 'adequate', 'insufficient', 'failed', 'skipped'}
    technical = {}
    for key in ('local_recommendation', 'ai_focus_attempt'):
        if recorded.get(key) in allowed_states:
            technical[key] = recorded[key]
    for key in ('auto_rejected', 'ai_focus_dirty', 'ai_focus_stale', 'person_review_pending'):
        if type(recorded.get(key)) is bool:
            technical[key] = recorded[key]
    for key in ('recommendation', 'reliability', 'state'):
        if isinstance(evidence, dict) and evidence.get(key) in allowed_states:
            technical[key] = evidence[key]
    return {'photo_id': snapshot['photo_id'], 'asset_version': snapshot['asset_version'],
            'recorded': technical,
            'source_count': len(snapshot['sources']),
            'identity_gap_count': len(snapshot['identity_gaps']),
            'historical_receipt_reconstructed': False}


class ScanReviewStore:
    def __init__(self, workspace, *, writer=None):
        self.workspace = Path(workspace).resolve()
        self.path = self.workspace / 'scan-review.json'
        self.writer = atomic_json if writer is None else writer

    def read(self):
        if not self.path.exists():
            return {'version': 1, 'revision': 0, 'photos': {}}
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or data.get('version') != 1
                or type(data.get('revision')) is not int or not isinstance(data.get('photos'), dict)):
            raise ValueError('反馈记录版本或结构不支持；原文件未改写')
        return data

    def photo(self, asset):
        return self.read()['photos'].get(asset_key(asset))

    def _mutate(self, expected_revision, change, *, writer=None, lock_held=False):
        with nullcontext() if lock_held else evidence_operation(self.workspace):
            data = self.read()
            if expected_revision is not None and expected_revision != data['revision']:
                raise ReviewConflict('反馈记录已更新，请刷新后重试')
            result = change(data)
            data['revision'] += 1
            # Validate before writer; a failed writer never changes caller's data.
            json.dumps(data, ensure_ascii=False, allow_nan=False)
            (self.writer if writer is None else writer)(self.path, data)
            return copy.deepcopy(data), copy.deepcopy(result)

    @staticmethod
    def _row(data, asset):
        key = asset_key(asset)
        return data['photos'].setdefault(key, {'asset_key': key, 'photo_id': photo_id(asset),
                                              'feedback': [], 'rechecks': [], 'scope_revision': 0})

    def record_feedback(self, asset, kind='question', text='', region=None, value=None,
                        feedback_id=None, withdraw=False, expected_revision=None):
        if kind not in {'question', 'region', 'final'}:
            raise ValueError('未知反馈类型')
        if not isinstance(text, str):
            raise ValueError('反馈文字无效')
        region = _validate_region(region)
        def change(data):
            row = self._row(data, asset)
            old = next((f for f in row['feedback'] if f['id'] == feedback_id), None)
            if feedback_id and old is None:
                raise ReviewConflict('找不到要修改的反馈')
            if old and old['kind'] != kind:
                raise ValueError('疑问、范围和最终决定不能互相转换')
            entry = {'id': feedback_id or uuid.uuid4().hex, 'kind': kind, 'text': text,
                     'region': region, 'value': _plain(value), 'withdrawn': bool(withdraw),
                     'updated_at': _now(), 'snapshot': snapshot_asset(asset), 'history': []}
            if old:
                prior = copy.deepcopy(old)
                entry['history'] = prior.pop('history') + [prior]
                row['feedback'][row['feedback'].index(old)] = entry
            else:
                row['feedback'].append(entry)
            if kind == 'region':
                row['scope_revision'] += 1
            return entry
        return self._mutate(expected_revision, change)[0]

    def begin_recheck(self, asset, region=None, expected_revision=None, operation_id=None,
                      crops_version=None):
        region = _validate_region(region)
        def change(data):
            row = self._row(data, asset)
            previous = next((op for op in row['rechecks'] if op['id'] == operation_id), None)
            if previous:
                if (previous['before']['asset_version'] != snapshot_asset(asset)['asset_version']
                        or previous['region'] != region
                        or previous.get('crops_version') != crops_version):
                    raise ReviewConflict('操作标识已用于不同版本或范围')
                return previous
            if any(op['status'] == 'pending' for op in row['rechecks']):
                raise ReviewConflict('已有未完成的重新检查，请先恢复或标记失败')
            op = {'id': operation_id or uuid.uuid4().hex, 'status': 'pending',
                  'started_at': _now(), 'before': snapshot_asset(asset), 'after': None,
                  'region': region, 'scope_revision': row['scope_revision'],
                  'granularity': 'whole_photo', 'error': None, 'crops_version': crops_version}
            row['rechecks'].append(op)
            return op
        return self._mutate(expected_revision, change)[1]

    def finish_recheck(self, asset, operation_id, expected_asset_version, error=None,
                       expected_revision=None, *, writer=None, lock_held=False):
        def change(data):
            row = self._row(data, asset)
            op = next((item for item in row['rechecks'] if item['id'] == operation_id), None)
            if not op or op['before']['asset_version'] != expected_asset_version:
                raise ReviewConflict('重新检查版本不匹配，旧响应未应用')
            if op['status'] != 'pending':
                raise ReviewConflict('重新检查已结束，重复响应未应用')
            if row['scope_revision'] != op['scope_revision']:
                raise ReviewConflict('关注范围已更新，旧响应未应用')
            after = snapshot_asset(asset)
            if error is None and after['sources'] != op['before']['sources']:
                raise ReviewConflict('来源已改变，旧响应未应用')
            op.update(status='failed' if error is not None else 'completed',
                      after=None if error is not None else after,
                      error=str(error) if error is not None else None, finished_at=_now())
            return op
        return self._mutate(expected_revision, change, writer=writer, lock_held=lock_held)[1]

    def verify_recheck(self, asset, operation_id, *, crops_version=None):
        """Validate the STILL-OLD production asset before a result is published.

        Caller must hold its production publication lock through final commit.
        This is read-only; finish_recheck checks the draft and source again.
        """
        row = self.photo(asset)
        op = next((item for item in (row or {}).get('rechecks', [])
                   if item['id'] == operation_id), None)
        if not op or op['status'] != 'pending':
            raise ReviewConflict('重新检查不是待发布状态')
        if (row['scope_revision'] != op['scope_revision']
                or snapshot_asset(asset)['asset_version'] != op['before']['asset_version']
                or (crops_version is not None and crops_version != op.get('crops_version'))):
            raise ReviewConflict('生产记录或范围版本已改变，旧响应未应用')
        return copy.deepcopy(op)

    def note_recheck_error(self, asset, operation_id, error, *, writer=None, lock_held=False):
        """Keep the pending job retryable without publishing failed measurements."""
        def change(data):
            row = self._row(data, asset)
            op = next((item for item in row['rechecks'] if item['id'] == operation_id), None)
            if not op or op['status'] != 'pending':
                raise ReviewConflict('检查已结束或不存在，不能添加失败重试记录')
            attempt = {'at': _now(), 'error': str(error), 'published': False}
            op.setdefault('attempt_history', []).append(attempt)
            op['last_error'] = str(error)
            return op
        return self._mutate(None, change, writer=writer, lock_held=lock_held)[1]

    def interrupt_recheck(self, asset, operation_id, reason='上次检查中断；原结果保留',
                          *, writer=None, lock_held=False):
        """Explicit recovery, never a write caused merely by opening a session."""
        row = self.photo(asset)
        op = next((x for x in (row or {}).get('rechecks', []) if x['id'] == operation_id), None)
        if not op:
            raise ReviewConflict('找不到未完成的检查')
        # Interrupted/superseded operations must be closable even after a new draft.
        def change(data):
            current = next(x for x in self._row(data, asset)['rechecks'] if x['id'] == operation_id)
            if current['status'] != 'pending':
                raise ReviewConflict('检查已结束')
            current.update(status='interrupted', error=str(reason), finished_at=_now())
            return current
        return self._mutate(None, change, writer=writer, lock_held=lock_held)[1]

    def export(self, path, *, shared=True, assets=None):
        data = self.read()
        if assets is not None:
            assets = list(assets)
            data['current_results'] = [snapshot_asset(asset) for asset in assets]
        if shared:
            # Strict allowlist: no paths, free text, raw evidence or dict keys from
            # evidence can leak private location strings. Private export is opt-in.
            data = {'version': data['version'], 'revision': data['revision'], 'redacted': True,
                    'photos': [{'photo_id': row['photo_id'],
                                'feedback': [{'id': f['id'], 'kind': f['kind'],
                                              'withdrawn': f['withdrawn'],
                                              'history_count': len(f['history']),
                                              'original_result': _shared_snapshot(f['snapshot'])}
                                             for f in row['feedback']],
                                'rechecks': [{'id': op['id'], 'status': op['status'],
                                              'granularity': op['granularity'],
                                              'before': _shared_snapshot(op['before']),
                                              'after': _shared_snapshot(op['after'])}
                                             for op in row['rechecks']]}
                               for row in data['photos'].values()]}
            if assets is not None:
                data['current_results'] = [_shared_snapshot(snapshot_asset(asset)) for asset in assets]
        target = Path(path).resolve()
        if target == self.path:
            raise ValueError('导出不能覆盖活跃反馈记录')
        if target.suffix.lower() != '.json':
            raise ValueError('诊断导出必须使用 .json 文件，不能覆盖照片')
        if target.parent == self.workspace:
            raise ValueError('诊断导出请放在工作区外，不能覆盖活跃项目记录')
        atomic_json(target, data)
        return data
