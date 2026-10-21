"""Explicit cancellation of one unpublished scan-review job, preserving diagnostics."""
from pathlib import Path
import json
import uuid

from . import publication_transaction
from .evidence_cleanup import evidence_operation, _plain
from .scan_review_store import ScanReviewStore, ReviewConflict, asset_key
from .workspace_layout import workspace_path


def cancel_recheck(workspace, asset, input_dir):
    """Return True when cancellation was applied; False when nothing is pending.

    Never deletes checkpoints or source files. If interruption was recorded but
    pointer unlink failed, repeating the action safely completes that unlink.
    An existing publish journal is handled only by the production recovery code.
    """
    workspace = _plain(Path(workspace).resolve())
    input_dir = Path(input_dir).resolve()
    with publication_transaction.operation(workspace):
        with evidence_operation(workspace):
            store = ScanReviewStore(workspace)
            processing_root = _plain(workspace_path(workspace, '.processing'))
            active_path = _plain(processing_root / 'active.json')
            active = None
            context = None
            if active_path.exists():
                active = json.loads(active_path.read_text('utf-8'))
                if (not isinstance(active, dict) or active.get('version') not in (1, 2)
                        or Path(active.get('input_dir', '')).resolve() != input_dir):
                    raise ReviewConflict('活跃任务不属于当前来源，未取消')
                identifier = active.get('job_id')
                try:
                    if not isinstance(identifier, str) or uuid.UUID(identifier).hex != identifier:
                        raise ValueError('invalid ID')
                except ValueError as exc:
                    raise ReviewConflict('活跃任务身份无效，保留现场') from exc
                root = _plain(processing_root / identifier)
                data_path = _plain(root / 'job.json')
                data = json.loads(data_path.read_text('utf-8'))
                if (not isinstance(data, dict) or data.get('version') not in (1, 2)
                        or data.get('id') != identifier
                        or Path(data.get('workspace', '')).resolve() != workspace
                        or Path(data.get('input_dir', '')).resolve() != input_dir):
                    raise ReviewConflict('任务检查点身份不符，未取消')
                context = data.get('scan_review')
                if (not isinstance(context, dict) or context.get('asset_key') != asset_key(asset)
                        or not isinstance(context.get('operation_id'), str)
                        or not isinstance(context.get('expected_asset_version'), str)):
                    raise ReviewConflict('活跃任务不是当前照片的范围重查，未取消')
                # Recovery may restore scan-review.json from its durable backup;
                # read the operation only afterwards, under both locks.
                recovered = publication_transaction.recover(root, workspace, identifier)
                if recovered == 'committed':
                    raise ReviewConflict('此重查已经发布，不能作为未发布任务取消')
            row = store.photo(asset)
            operations = (row or {}).get('rechecks', [])
            if context is not None:
                operation = next((op for op in operations if op['id'] == context['operation_id']), None)
                if (operation is None
                        or operation['before']['asset_version'] != context['expected_asset_version']
                        or operation['status'] not in {'pending', 'interrupted'}):
                    raise ReviewConflict('任务与反馈版本不符或已经发布，未取消')
            else:
                pending = [op for op in operations if op['status'] == 'pending']
                if not pending:
                    return False
                if len(pending) != 1:
                    raise ReviewConflict('存在多个未完成重查，保留现场')
                operation = pending[0]
            if operation['status'] == 'pending':
                store.interrupt_recheck(asset, operation['id'],
                                        '用户取消未发布重查；原结果与检查点保留', lock_held=True)
            if active is not None:
                # Publication lock excludes legitimate writers; still refuse an
                # unexpected pointer replacement rather than delete another job.
                if json.loads(active_path.read_text('utf-8')) != active:
                    raise ReviewConflict('活跃任务指针已变化，未解除')
                active_path.unlink()
            return True
