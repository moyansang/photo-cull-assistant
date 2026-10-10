from dataclasses import asdict
from pathlib import Path
import json

from PIL import Image
import pytest

from ai_cull_assistant import evidence_cleanup as cleanup
from ai_cull_assistant.focus_audit import record_inputs, record_response
from ai_cull_assistant.session_store import load_session, save_session, source_changes
from test_source_reconciliation import initial


def setup(tmp_path, monkeypatch, *, shared=False, legacy=False):
    photos, root, result = initial(tmp_path)
    monkeypatch.setattr(cleanup, 'default_quarantine_root', lambda _: tmp_path/'trash')
    for index, asset in enumerate(result.assets):
        image = tmp_path/f'evidence{index}.png'
        Image.new('RGB', (80, 100), 'red' if shared or index == 0 else ['blue', 'green'][index-1]).save(image)
        kwargs = {} if legacy else {'source_paths': asset.rating_target_paths}
        audit = record_inputs(root/'cache/analysis', f'request{index}', [image], 'prompt', {}, 'v2', **kwargs)
        record_response(root/'cache/analysis', f'request{index}', {'raw_response': 'clear'})
        asset.ai_focus_result = {'audit': audit, 'status':'clear', 'source':'api'}
    save_session(result, fresh=True)
    return photos, root, result


@pytest.mark.parametrize('legacy', [False, True])
def test_only_removed_exclusive_evidence_moves(tmp_path, monkeypatch, legacy):
    photos, root, result = setup(tmp_path, monkeypatch, legacy=legacy)
    before = {p.name:p.read_bytes() for p in (root/'focus-evidence/requests').iterdir()}
    survivor = asdict(result.assets[1])
    result.assets[0].primary_path.unlink()
    restored = load_session(root, photos)
    assert len(restored.assets)==2 and asdict(restored.assets[0])==survivor
    assert len(list((root/'focus-evidence/blobs').glob('*.zip')))==2
    assert len(list((tmp_path/'trash').rglob('*.zip')))==1
    assert before=={p.name:p.read_bytes() for p in (root/'focus-evidence/requests').iterdir()}
    assert '已清理 1' in cleanup.evidence_status(root)
    load_session(root, photos)
    assert len(list((tmp_path/'trash').rglob('*.zip')))==1


@pytest.mark.parametrize('legacy', [False, True])
def test_shared_blob_survives_until_last_owner_deleted(tmp_path, monkeypatch, legacy):
    photos, root, result = setup(tmp_path, monkeypatch, shared=True, legacy=legacy)
    for asset in result.assets[:-1]:
        asset.primary_path.unlink(); load_session(root, photos)
        assert len(list((root/'focus-evidence/blobs').glob('*.zip')))==1
        assert not (tmp_path/'trash').exists()
    result.assets[-1].primary_path.unlink(); load_session(root, photos)
    # Old request ownership is still understood after its scan row was pruned.
    assert not list((root/'focus-evidence/blobs').glob('*.zip'))
    assert len(list((tmp_path/'trash').rglob('*.zip')))==1


@pytest.mark.parametrize('kind', ['offline', 'modified', 'running', 'unknown', 'unanswered', 'escape'])
def test_unsafe_states_preserve_records_and_evidence(tmp_path, monkeypatch, kind):
    photos, root, result = setup(tmp_path, monkeypatch)
    if kind=='offline':photos.rename(tmp_path/'offline')
    elif kind=='modified':result.assets[1].primary_path.write_bytes(b'changed')
    elif kind=='running':
        (root/'cache/processing').mkdir(exist_ok=True)
        (root/'cache/processing/active.json').write_text('{}')
    elif kind=='unknown':(root/'focus-evidence/requests/unknown.bin').write_bytes(b'keep')
    elif kind=='unanswered':(root/'focus-evidence/requests/pending.json').write_text('{"images": []}')
    else:
        d=json.loads((root/'scan-session.json').read_text())
        d['assets'][0]['ai_focus_result']['audit']='../outside.json'
        (root/'scan-session.json').write_text(json.dumps(d))
    if kind!='offline':result.assets[0].primary_path.unlink()
    before=(root/'scan-session.json').read_bytes()
    with pytest.raises((ValueError, KeyError)):load_session(root, photos)
    assert (root/'scan-session.json').read_bytes()==before
    assert len(list((root/'focus-evidence/blobs').glob('*.zip')))==3
    assert not (tmp_path/'trash').exists()


def test_added_photo_only_prompts_and_unknown_shared_request_protects(tmp_path, monkeypatch):
    photos, root, result = setup(tmp_path, monkeypatch)
    blobs={p.name:p.read_bytes() for p in (root/'focus-evidence/blobs').iterdir()}
    Image.new('RGB',(90,100),'pink').save(photos/'NEW.jpg')
    assert len(source_changes(root,photos)[0])==1
    load_session(root,photos)
    assert blobs=={p.name:p.read_bytes() for p in (root/'focus-evidence/blobs').iterdir()}
    request=root/'focus-evidence/requests/request0.json'
    unknown=json.loads(request.read_text());unknown.pop('source_paths')
    (request.parent/'unknown.json').write_text(json.dumps(unknown))
    result.assets[0].primary_path.unlink();load_session(root,photos)
    assert blobs=={p.name:p.read_bytes() for p in (root/'focus-evidence/blobs').iterdir()}


def test_live_project_reference_protects_legacy_shared_image(tmp_path, monkeypatch):
    photos, root, result = setup(tmp_path, monkeypatch, legacy=True)
    audit=result.assets[0].ai_focus_result['audit']
    (root/'ai_project.json').write_text(json.dumps({'photos':{'live':{'path':str(result.assets[1].primary_path),'ai_focus_result':{'audit':audit}}},'tasks':[]}))
    result.assets[0].primary_path.unlink(); load_session(root,photos)
    assert len(list((root/'focus-evidence/blobs').glob('*.zip')))==3


def test_failed_move_keeps_records_for_retry(tmp_path, monkeypatch):
    photos, root, result = setup(tmp_path, monkeypatch)
    result.assets[0].primary_path.unlink()
    before=(root/'scan-session.json').read_bytes()
    original=Path.rename
    def blocked(path, target):
        if path.suffix=='.zip':raise PermissionError('in use')
        return original(path,target)
    monkeypatch.setattr(Path,'rename',blocked)
    with pytest.raises(ValueError,match='部分证据'):load_session(root,photos)
    assert (root/'scan-session.json').read_bytes()==before
    monkeypatch.setattr(Path,'rename',original)
    assert len(load_session(root,photos).assets)==2
    assert len(list((root/'focus-evidence/blobs').glob('*.zip')))==2


def test_evidence_lock_preserves_deletion_state(tmp_path, monkeypatch):
    photos, root, result = setup(tmp_path, monkeypatch)
    result.assets[0].primary_path.unlink();before=(root/'scan-session.json').read_bytes()
    with cleanup.evidence_operation(root):
        with pytest.raises(ValueError):load_session(root,photos)
    assert (root/'scan-session.json').read_bytes()==before


def test_rename_follows_existing_delete_and_add_rules(tmp_path, monkeypatch):
    photos, root, result = setup(tmp_path, monkeypatch)
    result.assets[0].primary_path.rename(photos/'RENAMED.jpg')
    added,removed=source_changes(root,photos)
    assert len(added)==len(removed)==1
    assert len(load_session(root,photos).assets)==2
    assert len(source_changes(root,photos)[0])==1
    assert len(list((tmp_path/'trash').rglob('*.zip')))==1
