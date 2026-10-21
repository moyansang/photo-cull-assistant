"""Explicit technical recheck adapter; existing scoring and grouping are unchanged."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from .crop_settings import CropSettings
from .scan_review_store import ScanReviewStore, ReviewConflict, asset_key


def file_revision(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def prepare_scope(result, asset, crops, draft):
    """Record the old result first. Do not save new scopes or mutate live assets."""
    if not any(item is asset for item in result.assets):
        raise ReviewConflict('照片已变化，请重新打开结果页')
    if draft.get('kind') != 'face':
        raise ValueError('当前入口仅支持明确的人脸范围重查；其他关注部位保留为待支持草稿')
    if draft.get('coordinate_space') != 'normalized_display' or draft.get('preview_version') != 'v05':
        raise ValueError('关注范围坐标或方向不明确，未应用')
    box = draft.get('box')
    if (not isinstance(box, (list, tuple)) or len(box) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
            or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)):
        raise ValueError('关注范围无效或越界')
    # A fallback preview cannot authorize a new measurement scope.
    source = draft.get('source_path')
    if not source or Path(source).resolve() not in {p.resolve() for p in asset.rating_target_paths}:
        raise ValueError('关注范围不是可验证的原来源成员；请打开原生图重新框选')
    if not Path(source).is_file():
        raise ValueError('原图不可访问；原范围与判断保留')
    from .image_decode import asset_source_signature
    current_signature = json.loads(json.dumps(asset_source_signature(asset)))
    if draft.get('source_signature') != current_signature:
        raise ReviewConflict('框选后来源已变化或未记录来源版本；请重新打开原生图再画框')
    if getattr(asset, 'analysis_mode', 'portrait') != 'portrait':
        raise ValueError('风景模式保持整图分析，不应用人脸范围')
    updated = deepcopy(crops)
    entry = updated.photos.setdefault(updated.key(asset), {})
    entry.update(manual_face=[box[0], box[1], box[2]-box[0], box[3]-box[1]], preview_version='v05')
    # This explicitly replaces this photo's selected subject; do not silently append.
    for field in ('selected_faces', 'face_crops', 'hidden', 'subjects_disabled',
                  'primary_face_index', 'primary_face_id', 'primary_face_basis'):
        entry.pop(field, None)
    from .primary_quality import CONFIRMATION_KEY, confirm_selection
    entry[CONFIRMATION_KEY] = confirm_selection(asset, updated)
    if updated == crops and not getattr(asset, 'ai_focus_dirty', False):
        raise ValueError('范围与已确认设置没有变化；未重复启动检查')
    workspace = Path(result.workspace_dir)
    session_revision = file_revision(workspace / 'scan-session.json')
    if session_revision is None:
        raise ReviewConflict('先保存当前扫描结果后再重新检查')
    settings_revision = file_revision(workspace / 'workspace-settings.json')
    # Bind to one canonical read-only view of the persisted result. UI-only
    # availability flags must not make fresh scopes permanently unpublishable.
    from .session_store import load_session
    saved = load_session(workspace, result.input_dir, preserve_unavailable=True)
    canonical = next((a for a in saved.assets if asset_key(a) == asset_key(asset)), None) if saved else None
    if canonical is None:
        raise ReviewConflict('原扫描记录无法定位此照片')
    op = ScanReviewStore(workspace).begin_recheck(canonical, draft, crops_version=settings_revision)
    context = {'asset_key': asset_key(asset), 'operation_id': op['id'],
               'expected_asset_version': op['before']['asset_version'],
               'session_revision': session_revision, 'settings_revision': settings_revision,
               'groups_revision': file_revision(workspace / 'groups.json'),
               'preferences': json.loads((workspace / 'workspace-settings.json').read_text('utf-8'))
                   if (workspace / 'workspace-settings.json').exists() else {}}
    return updated, context


def validate_review_publication(job):
    """Under publication + feedback locks, reject late results before live writes."""
    context = job._data['scan_review']
    for name, key in (('scan-session.json', 'session_revision'),
                      ('workspace-settings.json', 'settings_revision'), ('groups.json', 'groups_revision')):
        if file_revision(job.workspace / name) != context[key]:
            raise ReviewConflict('工作区结果、范围或分组已变化；旧重算未发布')
    from .session_store import load_session
    saved = load_session(job.workspace, job.input_dir, preserve_unavailable=True)
    old = next((a for a in saved.assets if asset_key(a) == context['asset_key']), None) if saved else None
    if old is None:
        raise ReviewConflict('原照片记录已变化；旧重算未发布')
    store = ScanReviewStore(job.workspace)
    operation = store.verify_recheck(old, context['operation_id'])
    from .image_decode import asset_source_signature
    if json.loads(json.dumps(asset_source_signature(old))) != operation['region'].get('source_signature'):
        raise ReviewConflict('框选来源版本已变化；旧重算未发布')
    job._review_old_inventory = json.loads((job.workspace / 'scan-session.json').read_text('utf-8'))['source_stats']
    new = next(a for a in job.assets if asset_key(a) == context['asset_key'])
    if new.screening_reason == 'decode_unavailable':
        raise ReviewConflict('重新检查读取失败；原结果未替换，请恢复来源后继续任务')
    payloads = []
    store.finish_recheck(new, context['operation_id'], context['expected_asset_version'],
                         writer=lambda path, data: payloads.append(data), lock_held=True)
    job._review_publication = payloads[0]


def publish_review_result(job, result, writer):
    """Called inside existing rollback transaction and feedback lock."""
    context = job._data['scan_review']
    settings_path = job.workspace / 'workspace-settings.json'
    # Transaction has moved the old file to its backup; use the approved snapshot.
    preferences = deepcopy(context.get('preferences', {}))
    preferences.update(version=1, face_crop=job._data['created_crops'])
    writer(settings_path, preferences)
    writer(job.workspace / 'scan-review.json', job._review_publication)
