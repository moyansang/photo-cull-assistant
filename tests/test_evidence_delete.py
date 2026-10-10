import json
from pathlib import Path

import pytest
from PIL import Image

from ai_cull_assistant import evidence_cleanup as cleanup
from ai_cull_assistant.focus_audit import record_inputs, record_response
from test_evidence_cleanup import workspace, snapshots, write, completed


@pytest.mark.parametrize('missing', [False, True])
def test_delete_preserves_every_nonimage_record_and_keeps_receipt(workspace, missing):
    root, photos, photo, trash=workspace
    if missing:photo.unlink()
    before=snapshots(root);plan=cleanup.inspect_evidence(root,photos)
    result=cleanup.delete_evidence(plan)
    assert result==dict(deleted=1, bytes=plan.bytes, failed=[])
    assert not trash.exists() and not list((root/'focus-evidence/blobs').iterdir())
    for name,payload in before.items():
        if name.parts[:2]!=('focus-evidence','blobs'):assert (root/name).read_bytes()==payload
    history=json.loads((root/'focus-evidence/evidence-cleanup.json').read_text())
    assert history['operations'][-1]['action']=='permanent_delete'
    assert history['operations'][-1]['result']==result
    assert '已清理 1' in cleanup.evidence_status(root)
    assert cleanup.delete_evidence(cleanup.inspect_evidence(root,photos))['deleted']==0


@pytest.mark.parametrize('change', ['source','request','active','new_blob','session'])
def test_confirmation_revalidation_aborts_before_delete(workspace, change):
    root, photos, photo, trash=workspace;plan=cleanup.inspect_evidence(root,photos)
    if change=='source':photo.unlink()
    elif change=='request':record_response(root/'cache/analysis','request',{'new':True})
    elif change=='active':write(root/'cache/processing/active.json',{})
    elif change=='new_blob':(root/'focus-evidence/blobs/unknown.bin').write_bytes(b'preserve')
    else:write(root/'scan-session.json',{})
    with pytest.raises(ValueError):cleanup.delete_evidence(plan)
    assert len(list((root/'focus-evidence/blobs').glob('*.zip')))==1


def test_shared_image_deleted_once_for_confirmed_workspace_scope(workspace):
    root, photos, photo, trash=workspace
    record_inputs(root/'cache/analysis','second',[photo],'second prompt',{},'v2')
    record_response(root/'cache/analysis','second',{'answer':'keep'})
    before={p.name:p.read_bytes() for p in (root/'focus-evidence/requests').iterdir()}
    result=cleanup.delete_evidence(cleanup.inspect_evidence(root,photos))
    assert result['deleted']==1
    assert before=={p.name:p.read_bytes() for p in (root/'focus-evidence/requests').iterdir()}


def test_partial_delete_reports_failure_and_retains_blocked_file(workspace, monkeypatch):
    root, photos, photo, trash=workspace
    second=photos/'B.png';Image.new('RGB',(40,60),'blue').save(second)
    record_inputs(root/'cache/analysis','second',[second],'p',{},'v2');record_response(root/'cache/analysis','second',{})
    plan=cleanup.inspect_evidence(root,photos);blocked=plan.files[0].name
    original=Path.unlink
    def unlink(path,*args,**kwargs):
        if path.name==blocked:raise PermissionError('file in use')
        assert path.is_relative_to(root)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',unlink)
    result=cleanup.delete_evidence(plan)
    assert result['deleted']==1 and len(result['failed'])==1
    assert (root/'focus-evidence/blobs'/blocked).exists()


def test_mixed_and_unknown_packages_remain(workspace):
    import zipfile
    root, photos, photo, trash=workspace
    mixed=root/'focus-evidence/blobs'/('a'*64+'.zip')
    with zipfile.ZipFile(mixed,'w') as z:
        z.writestr('image.jpg',photo.read_bytes());z.writestr('metadata.json','{"keep":1}')
    before=mixed.read_bytes()
    assert cleanup.delete_evidence(cleanup.inspect_evidence(root,photos))['deleted']==1
    assert mixed.read_bytes()==before


def test_evidence_lock_blocks_permanent_delete(workspace):
    root, photos, photo, trash=workspace;plan=cleanup.inspect_evidence(root,photos)
    with cleanup.evidence_operation(root):
        with pytest.raises(ValueError):cleanup.delete_evidence(plan)
    assert len(list((root/'focus-evidence/blobs').glob('*.zip')))==1


def test_interruption_has_durable_permanent_delete_intent(workspace,monkeypatch):
    root, photos, photo, trash=workspace;plan=cleanup.inspect_evidence(root,photos)
    original=Path.unlink
    def interrupted(path,*args,**kwargs):
        original(path,*args,**kwargs);raise KeyboardInterrupt()
    monkeypatch.setattr(Path,'unlink',interrupted)
    with pytest.raises(KeyboardInterrupt):cleanup.delete_evidence(plan)
    assert '已清理 1' in cleanup.evidence_status(root)
    event=json.loads((root/'focus-evidence/evidence-cleanup.json').read_text())['operations'][-1]
    assert event['action']=='permanent_delete' and 'destination' not in event


def test_archived_workspace_is_not_restored_or_modified(tmp_path):
    from ai_cull_assistant.workspace_archive import compact_workspace
    root,photos,*_=completed(tmp_path)
    assert compact_workspace(root)['compacted']
    before=(root/'.workspace-archive.zip').read_bytes()
    assert cleanup.delete_evidence(cleanup.inspect_evidence(root,photos))['deleted']==1
    assert (root/'.workspace-archive.zip').read_bytes()==before
    assert not (root/'scan-session.json').exists()
