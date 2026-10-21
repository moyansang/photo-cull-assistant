import json
from pathlib import Path
import uuid

import pytest

from test_scan_review_store import asset
from ai_cull_assistant import publication_transaction as publication
from ai_cull_assistant.ai_project import atomic_json
from ai_cull_assistant.scan_review_cancel import cancel_recheck
from ai_cull_assistant.scan_review_store import ScanReviewStore, ReviewConflict, asset_key
from ai_cull_assistant.workspace_layout import workspace_path


def setup(tmp_path, asset, legacy=False):
    workspace = tmp_path / 'workspace'
    store = ScanReviewStore(workspace)
    op = store.begin_recheck(asset)
    identifier = uuid.uuid4().hex
    if legacy:
        (workspace / '.processing').mkdir()
    root = workspace_path(workspace, '.processing') / identifier
    root.mkdir(parents=True)
    data = {'version': 2, 'id': identifier, 'workspace': str(workspace),
            'input_dir': str(tmp_path), 'scan_review': {'asset_key': asset_key(asset),
            'operation_id': op['id'], 'expected_asset_version': op['before']['asset_version']}}
    atomic_json(root / 'job.json', data)
    active = root.parent / 'active.json'
    atomic_json(active, {'version': 2, 'job_id': identifier, 'input_dir': str(tmp_path)})
    return workspace, store, op, root, active


@pytest.mark.parametrize('legacy', [False, True])
def test_cancel_preserves_checkpoint_and_all_source_bytes(tmp_path, asset, legacy):
    workspace, store, op, root, active = setup(tmp_path, asset, legacy=legacy)
    before = (root / 'job.json').read_bytes()
    source = asset.primary_path.read_bytes()
    assert cancel_recheck(workspace, asset, tmp_path)
    assert not active.exists()
    assert (root / 'job.json').read_bytes() == before
    assert asset.primary_path.read_bytes() == source
    assert store.photo(asset)['rechecks'][0]['status'] == 'interrupted'
    assert cancel_recheck(workspace, asset, tmp_path) is False
    assert store.begin_recheck(asset)['status'] == 'pending'


def test_busy_does_not_mutate(tmp_path, asset):
    workspace, store, op, root, active = setup(tmp_path, asset)
    before = store.path.read_bytes()
    with publication.operation(workspace):
        with pytest.raises(publication.PublicationRecoveryError):
            cancel_recheck(workspace, asset, tmp_path)
    assert store.path.read_bytes() == before and active.exists()


@pytest.mark.parametrize('change', ['other_asset', 'other_source', 'not_review', 'wrong_version'])
def test_other_or_invalid_job_is_not_cancelled(tmp_path, asset, change):
    workspace, store, op, root, active = setup(tmp_path, asset)
    data = json.loads((root / 'job.json').read_text('utf-8'))
    if change == 'other_asset':
        data['scan_review']['asset_key'] = 'another'
    elif change == 'other_source':
        data['input_dir'] = str(tmp_path / 'elsewhere')
    elif change == 'not_review':
        data.pop('scan_review')
    else:
        data['scan_review']['expected_asset_version'] = 'other'
    atomic_json(root / 'job.json', data)
    before = store.path.read_bytes()
    with pytest.raises(ReviewConflict):
        cancel_recheck(workspace, asset, tmp_path)
    assert store.path.read_bytes() == before and active.exists()


def test_failed_record_write_retains_active_and_old_record(tmp_path, asset, monkeypatch):
    workspace, store, op, root, active = setup(tmp_path, asset)
    before = store.path.read_bytes()
    def fail(*a, **kw):
        raise OSError('disk full')
    monkeypatch.setattr('ai_cull_assistant.scan_review_store.atomic_json', fail)
    with pytest.raises(OSError, match='disk full'):
        cancel_recheck(workspace, asset, tmp_path)
    assert active.exists() and store.path.read_bytes() == before


def test_unlink_interruption_is_retryable(tmp_path, asset, monkeypatch):
    workspace, store, op, root, active = setup(tmp_path, asset)
    unlink = Path.unlink
    def fail(path, *a, **kw):
        if path == active:
            raise OSError('file locked')
        return unlink(path, *a, **kw)
    with monkeypatch.context() as m:
        m.setattr(Path, 'unlink', fail)
        with pytest.raises(OSError, match='file locked'):
            cancel_recheck(workspace, asset, tmp_path)
    assert active.exists()
    assert store.photo(asset)['rechecks'][0]['status'] == 'interrupted'
    assert cancel_recheck(workspace, asset, tmp_path)
    assert not active.exists()


def test_orphan_pending_and_unknown_publication(tmp_path, asset):
    workspace, store, op, root, active = setup(tmp_path, asset)
    atomic_json(root / publication.JOURNAL, {'version': 999})
    before = store.path.read_bytes()
    with pytest.raises(publication.PublicationRecoveryError):
        cancel_recheck(workspace, asset, tmp_path)
    assert active.exists() and store.path.read_bytes() == before
    (root / publication.JOURNAL).unlink()
    active.unlink()
    assert cancel_recheck(workspace, asset, tmp_path)
    assert store.photo(asset)['rechecks'][0]['status'] == 'interrupted'


def test_rollback_publication_before_cancellation(tmp_path, asset):
    workspace, store, op, root, active = setup(tmp_path, asset)
    output = root / 'output'
    output.mkdir()
    state = publication.begin(root, workspace, root.name,
                              [(output / 'contact_sheets', workspace / 'contact_sheets')],
                              passive=('scan-review.json',))
    for entry in state['entries']:
        _, target, backup = publication._paths(root, workspace, root.name, entry)
        if target.exists():
            target.replace(backup)
    assert not store.path.exists()
    assert cancel_recheck(workspace, asset, tmp_path)
    assert store.photo(asset)['rechecks'][0]['before'] == op['before']
    assert store.photo(asset)['rechecks'][0]['status'] == 'interrupted'
    assert (root / 'job.json').exists()
