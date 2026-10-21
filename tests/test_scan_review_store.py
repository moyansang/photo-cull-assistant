import copy
from datetime import datetime
import json
from pathlib import Path

import pytest

from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.scan_review_store import (ScanReviewStore, ReviewConflict,
                                                asset_key, snapshot_asset)
from ai_cull_assistant.evidence_cleanup import evidence_operation


@pytest.fixture
def asset(tmp_path):
    path = tmp_path / '原片 空格.JPG'
    path.write_bytes(b'synthetic source, not a photo')
    return PhotoAsset('原片', path, path, None, path, datetime(2026, 1, 1), '.jpg',
                      clarity_evidence={'recommendation': 'review', 'reason': '未记录'},
                      local_recommendation='review')


def test_read_legacy_does_not_write(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    assert store.read()['revision'] == 0
    assert store.photo(asset) is None
    assert not store.workspace.exists()
    snap = snapshot_asset(asset)
    assert snap['historical_receipt_reconstructed'] is False
    assert len(snap['identity_gaps']) == 3
    assert snap['recorded']['focus_scope'] is None


def test_feedback_is_separate_and_durable(tmp_path, asset):
    before = copy.deepcopy(asset)
    store = ScanReviewStore(tmp_path / '工作 区')
    data = store.record_feedback(asset, text='手在动，但保留动作；噪声也多')
    entry = data['photos'][asset_key(asset)]['feedback'][0]
    store.record_feedback(asset, 'region', region={'box': [0.1, 0.2, 0.3, 0.4], 'kind': 'hand'})
    store.record_feedback(asset, 'final', value={'pick_status': 'keep', 'rating': 3})
    row = ScanReviewStore(store.workspace).photo(asset)
    assert row['feedback'][1]['region']['measurement_supported'] is False
    assert row['feedback'][0]['text'] == entry['text']
    assert asset == before
    store.record_feedback(asset, text='改为只记录艺术性模糊', feedback_id=entry['id'])
    data = store.record_feedback(asset, feedback_id=entry['id'], withdraw=True)
    changed = data['photos'][asset_key(asset)]['feedback'][0]
    assert changed['withdrawn'] and len(changed['history']) == 2
    assert changed['history'][0]['text'] == entry['text']
    assert asset == before


def test_identity_not_stem_or_casefold(tmp_path, asset):
    other = copy.deepcopy(asset)
    # Real different files with the same name must not share an identity.
    directory = tmp_path / '另一目录'
    directory.mkdir()
    other.primary_path = directory / asset.primary_path.name
    other.primary_path.write_bytes(b'different synthetic source')
    assert other.stem == asset.stem
    assert not other.primary_path.samefile(asset.primary_path)
    assert asset_key(other) != asset_key(asset)

    # Probe the actual filesystem, not sys.platform: case variants can be an
    # existing file alias (Windows defaults) or independently creatable files.
    variant = tmp_path / '原片 空格.jpg'
    case_asset = copy.deepcopy(asset)
    case_asset.primary_path = variant
    if variant.exists() and variant.samefile(asset.primary_path):
        assert asset_key(case_asset) == asset_key(asset)
    else:
        variant.write_bytes(b'independent case-sensitive synthetic source')
        assert not variant.samefile(asset.primary_path)
        assert asset_key(case_asset) != asset_key(asset)

    # A spelling alias that resolves to the same real file is never split.
    alias = copy.deepcopy(asset)
    alias.primary_path = directory / '..' / asset.primary_path.name
    assert alias.primary_path.samefile(asset.primary_path)
    assert asset_key(alias) == asset_key(asset)


def test_cas_and_failed_save_preserve_file(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    store.record_feedback(asset, text='one', expected_revision=0)
    before = store.path.read_bytes()
    with pytest.raises(ReviewConflict):
        store.record_feedback(asset, text='stale', expected_revision=0)
    def fail(*args):
        raise OSError('disk full')
    broken = ScanReviewStore(store.workspace, writer=fail)
    with pytest.raises(OSError, match='disk full'):
        broken.record_feedback(asset, text='not persisted', expected_revision=1)
    assert store.path.read_bytes() == before
    assert store.read()['revision'] == 1


def test_concurrent_writer_busy(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    with evidence_operation(store.workspace):
        with pytest.raises(ValueError, match='正在被任务使用'):
            store.record_feedback(asset, text='must not overwrite')
    assert not store.path.exists()


def test_recheck_records_before_after_and_rejects_repeated_response(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    op = store.begin_recheck(asset, operation_id='explicit-operation')
    assert store.begin_recheck(asset, operation_id=op['id']) == op
    original = op['before']['asset_version']
    asset.clarity_evidence = {'recommendation': 'keep'}
    done = store.finish_recheck(asset, op['id'], original)
    assert done['before']['recorded']['clarity_evidence']['recommendation'] == 'review'
    assert done['after']['recorded']['clarity_evidence']['recommendation'] == 'keep'
    with pytest.raises(ReviewConflict, match='重复响应'):
        store.finish_recheck(asset, op['id'], original)
    assert len(store.photo(asset)['rechecks']) == 1


def test_superseded_draft_rejects_late_response_then_explicit_recovery(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    op = store.begin_recheck(asset)
    store.record_feedback(asset, 'region', region={'kind': 'body', 'box': [0, 0, 1, 1]})
    before = store.path.read_bytes()
    with pytest.raises(ReviewConflict, match='范围已更新'):
        store.finish_recheck(asset, op['id'], op['before']['asset_version'])
    assert store.path.read_bytes() == before
    assert ScanReviewStore(store.workspace).photo(asset)['rechecks'][0]['status'] == 'pending'
    recovered = store.interrupt_recheck(asset, op['id'])
    assert recovered['status'] == 'interrupted' and recovered['after'] is None
    assert store.begin_recheck(asset)['status'] == 'pending'


def test_source_change_and_failed_recheck(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    op = store.begin_recheck(asset)
    asset.primary_path.write_bytes(b'changed')
    with pytest.raises(ReviewConflict, match='来源已改变'):
        store.finish_recheck(asset, op['id'], op['before']['asset_version'])
    failed = store.finish_recheck(asset, op['id'], op['before']['asset_version'], error='原图不可读')
    assert failed['status'] == 'failed' and failed['after'] is None
    assert failed['before'] == op['before']


@pytest.mark.parametrize('box', [[0, 0, 2, 1], [0.5, 0, 0.2, 1], [0, 0, float('nan'), 1]])
def test_invalid_region_not_saved(tmp_path, asset, box):
    store = ScanReviewStore(tmp_path / 'workspace')
    with pytest.raises(ValueError):
        store.record_feedback(asset, 'region', region={'box': box})
    assert not store.path.exists()


def test_export_public_allowlist_private_roundtrip(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    store.record_feedback(asset, text=f'私有路径 {asset.primary_path}')
    public = tmp_path / '共享.json'
    private = tmp_path / '私有.json'
    store.export(public)
    store.export(private, shared=False)
    assert str(tmp_path) not in public.read_text('utf-8')
    assert '私有路径' not in public.read_text('utf-8')
    assert json.loads(private.read_text('utf-8')) == store.read()
    with pytest.raises(ValueError, match='不能覆盖'):
        store.export(store.path)


def test_corrupt_or_future_record_is_not_replaced(tmp_path, asset):
    store = ScanReviewStore(tmp_path)
    store.path.write_text('{"version": 99}', encoding='utf-8')
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='不支持'):
        store.record_feedback(asset, text='no')
    assert store.path.read_bytes() == before


def test_verify_original_then_transaction_writer(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    op = store.begin_recheck(asset, crops_version='old-crops')
    assert store.verify_recheck(asset, op['id'], crops_version='old-crops') == op
    with pytest.raises(ReviewConflict):
        store.verify_recheck(asset, op['id'], crops_version='new-crops')
    asset.clarity_evidence = {'recommendation': 'keep'}
    with pytest.raises(ReviewConflict):
        store.verify_recheck(asset, op['id'])
    staged = []
    done = store.finish_recheck(asset, op['id'], op['before']['asset_version'],
                               writer=lambda path, data: staged.append((path, data)))
    assert done['status'] == 'completed'
    assert staged[0][0] == store.path
    assert store.photo(asset)['rechecks'][0]['status'] == 'pending'
    # The publication transaction owns commit/rollback, not this helper.
    assert staged[0][1]['photos'][asset_key(asset)]['rechecks'][0]['status'] == 'completed'


def test_reused_operation_cannot_silently_change_scope(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    store.begin_recheck(asset, operation_id='one')
    with pytest.raises(ReviewConflict, match='不同版本'):
        store.begin_recheck(asset, operation_id='one', region={'kind': 'face', 'box': [0, 0, 1, 1]})


def test_export_never_overwrites_source_or_workspace(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    store.record_feedback(asset)
    original = asset.primary_path.read_bytes()
    with pytest.raises(ValueError, match='不能覆盖照片'):
        store.export(asset.primary_path)
    assert asset.primary_path.read_bytes() == original
    with pytest.raises(ValueError, match='活跃项目'):
        store.export(store.workspace / 'ai_project.json')
    out = tmp_path / 'results.json'
    result = store.export(out, assets=iter([asset]))
    assert len(result['current_results']) == 1


def test_transaction_lock_can_enclose_finish(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    op = store.begin_recheck(asset)
    with evidence_operation(store.workspace):
        store.verify_recheck(asset, op['id'])
        result = store.finish_recheck(asset, op['id'], op['before']['asset_version'], lock_held=True)
    assert result['status'] == 'completed'



def test_retryable_failure_keeps_before_and_later_completion(tmp_path, asset):
    store = ScanReviewStore(tmp_path / 'workspace')
    op = store.begin_recheck(asset)
    failed = store.note_recheck_error(asset, op['id'], '读取失败')
    assert failed['status'] == 'pending'
    assert failed['last_error'] == '读取失败'
    assert failed['before'] == op['before']
    assert failed['after'] is None
    assert failed['attempt_history'][0]['published'] is False
    store.verify_recheck(asset, op['id'])
    done = store.finish_recheck(asset, op['id'], op['before']['asset_version'])
    assert done['status'] == 'completed'
    assert done['attempt_history'] == failed['attempt_history']


def test_os_lock_blocks_separate_process(tmp_path):
    import os
    import subprocess
    import sys
    store = ScanReviewStore(tmp_path / 'workspace')
    script = '''
import sys
from ai_cull_assistant.evidence_cleanup import evidence_operation
try:
    with evidence_operation(sys.argv[1]):
        raise RuntimeError('incorrectly acquired lock')
except ValueError:
    print('blocked')
'''
    with evidence_operation(store.workspace):
        proc = subprocess.run([sys.executable, '-X', 'utf8', '-Werror', '-c', script,
                               str(store.workspace)], capture_output=True, text=True,
                              encoding='utf-8', env=os.environ.copy(), check=True)
    assert proc.stdout.strip() == 'blocked'


@pytest.mark.parametrize('kind,supported', [('face', True), ('body', False), ('hand', False), ('other', False)])
def test_only_face_draft_can_claim_this_recheck_route(tmp_path, asset, kind, supported):
    store = ScanReviewStore(tmp_path / 'workspace')
    store.record_feedback(asset, 'region', region={'kind': kind, 'box': [0, 0, 1, 1]})
    region = store.photo(asset)['feedback'][0]['region']
    assert region['measurement_supported'] is supported
    assert region['status'] == 'draft'
