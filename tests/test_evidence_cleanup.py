import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import zipfile

import pytest
from PIL import Image

from ai_cull_assistant import evidence_cleanup as cleanup
from ai_cull_assistant.focus_audit import record_inputs, record_response


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def workspace(tmp_path):
    photos = tmp_path/'photos'; photos.mkdir()
    photo = photos/'A.jpg'; Image.new('RGB', (40, 60), 'white').save(photo)
    root = tmp_path/'work'; root.mkdir()
    st = photo.stat()
    write(root/'scan-session.json', dict(version=1, workspace_dir=str(root), input_dir=str(photos),
          source_stats={str(photo):[st.st_size,st.st_mtime_ns]}))
    write(root/'ai_project.json', dict(version=1, photos={'A':{'ai':{'rating':4},'final':{'confirmed':True},'history':[1]}}, tasks=[]))
    for name in ['groups.json','workspace-settings.json','screening_results.json']:
        write(root/name, {'preserve':name})
    record_inputs(root/'cache'/'analysis', 'request', [photo], 'saved prompt', {}, 'v2')
    record_response(root/'cache'/'analysis', 'request', {'raw_response':'saved answer'})
    return root,photos,photo,tmp_path/'trash'


def snapshots(root):
    return {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_inspect_is_read_only_and_move_preserves_all_results(workspace):
    root,photos,photo,trash=workspace;before=snapshots(root)
    plan=cleanup.inspect_evidence(root,photos)
    assert snapshots(root)==before and len(plan.files)==1
    assert not plan.missing_sources and not plan.changed_sources
    result=cleanup.quarantine_evidence(plan,trash)
    assert result['moved']==1 and not result['failed']
    for name,payload in before.items():
        if name.parts[:2]!=('focus-evidence','blobs'):assert (root/name).read_bytes()==payload
    moved=next(trash.rglob('*.zip'));assert moved.read_bytes()==before[Path('focus-evidence/blobs')/plan.files[0].name]
    assert '已清理 1' in cleanup.evidence_status(root)
    again=cleanup.inspect_evidence(root,photos)
    assert not again.files and cleanup.quarantine_evidence(again,trash)['moved']==0


@pytest.mark.parametrize('kind',['missing','changed','whole_directory_missing'])
def test_missing_or_changed_sources_are_explicit_warnings_not_record_edits(workspace,kind):
    root,photos,photo,trash=workspace
    if kind=='missing':photo.unlink()
    elif kind=='changed':photo.write_bytes(b'changed')
    else:photos.rename(photos.with_name('offline'))
    before=snapshots(root);plan=cleanup.inspect_evidence(root,photos)
    assert (plan.changed_sources if kind=='changed' else plan.missing_sources)==1
    assert snapshots(root)==before
    assert cleanup.quarantine_evidence(plan,trash)['moved']==1
    assert (root/'ai_project.json').read_bytes()==before[Path('ai_project.json')]
    assert (root/'scan-session.json').read_bytes()==before[Path('scan-session.json')]


@pytest.mark.parametrize('kind',['processing','legacy_processing','running_batch','running_web','unanswered'])
def test_running_or_unresolved_requests_block(workspace,kind):
    root,photos,photo,trash=workspace
    if kind in ['processing','legacy_processing']:
        write(root/('cache/processing/active.json' if kind=='processing' else '.processing/active.json'),{})
    elif kind=='unanswered':
        write(root/'focus-evidence/requests/request.json',{'images':[]})
    else:
        write(root/'ai_project.json',{'tasks':[{'batches':[{'status':'running'}] if kind=='running_batch' else [],'web_submissions':[{'status':'running'}] if kind=='running_web' else []}]})
    before=snapshots(root)
    with pytest.raises(ValueError):cleanup.inspect_evidence(root,photos)
    assert snapshots(root)==before and not trash.exists()


def test_pending_rating_task_can_survive_cleanup(workspace):
    root,photos,photo,trash=workspace
    write(root/'ai_project.json',{'tasks':[{'batches':[{'status':'failed'}]}]})
    plan=cleanup.inspect_evidence(root,photos)
    assert cleanup.quarantine_evidence(plan,trash)['moved']==1


@pytest.mark.parametrize('kind',['mixed','not_image','invalid_zip','nested','wrong_hash'])
def test_unknown_or_mixed_packages_are_preserved(workspace,kind):
    root,photos,photo,trash=workspace;p=root/'focus-evidence/blobs'/('f'*64+'.zip')
    if kind=='invalid_zip':p.write_bytes(b'bad')
    else:
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('folder/image.jpg' if kind=='nested' else 'image.jpg',photo.read_bytes() if kind!='not_image' else b'bad')
            if kind=='mixed':z.writestr('metadata.json','{"keep":true}')
    original=p.read_bytes();plan=cleanup.inspect_evidence(root,photos)
    assert plan.preserved==1 and len(plan.files)==1
    assert cleanup.quarantine_evidence(plan,trash)['moved']==1 and p.read_bytes()==original


@pytest.mark.parametrize('change',['source','session','new_blob','active_job'])
def test_changes_after_confirmation_abort_before_any_move(workspace,change):
    root,photos,photo,trash=workspace;plan=cleanup.inspect_evidence(root,photos)
    if change=='source':photo.unlink()
    elif change=='session':
        d=json.loads((root/'scan-session.json').read_text());d['new']=True;write(root/'scan-session.json',d)
    elif change=='active_job':write(root/'cache/processing/active.json',{})
    else:(root/'focus-evidence/blobs/keep.bin').write_bytes(b'keep')
    with pytest.raises(ValueError):cleanup.quarantine_evidence(plan,trash)
    assert len(list((root/'focus-evidence/blobs').glob('*.zip')))==1


def test_partial_file_in_use_preserves_failed_file_and_receipt(workspace,monkeypatch):
    root,photos,photo,trash=workspace
    second=photos/'B.jpg';Image.new('RGB',(40,60),'blue').save(second)
    record_inputs(root/'cache/analysis','second',[second],'p',{},'v2');record_response(root/'cache/analysis','second',{})
    plan=cleanup.inspect_evidence(root,photos);original=Path.rename
    blocked=plan.files[0].name
    def rename(path,target):
        if path.name==blocked:raise PermissionError('file in use')
        return original(path,target)
    monkeypatch.setattr(Path,'rename',rename)
    result=cleanup.quarantine_evidence(plan,trash)
    assert result['moved']==1 and len(result['failed'])==1
    assert (root/'focus-evidence/blobs'/blocked).exists()
    assert list(trash.rglob('manifest.json')) and '已清理 1' in cleanup.evidence_status(root)


def test_interruption_keeps_recovery_map(workspace,monkeypatch):
    root,photos,photo,trash=workspace;plan=cleanup.inspect_evidence(root,photos)
    original=Path.rename
    def interrupted(path,target):
        original(path,target);raise KeyboardInterrupt()
    monkeypatch.setattr(Path,'rename',interrupted)
    with pytest.raises(KeyboardInterrupt):cleanup.quarantine_evidence(plan,trash)
    assert list(trash.rglob('manifest.json')) and len(list(trash.rglob('*.zip')))==1
    assert '已清理 1' in cleanup.evidence_status(root)


def link_dir(link,target):
    try:link.symlink_to(target,target_is_directory=True)
    except OSError:
        if os.name!='nt':raise
        subprocess.run(['cmd','/c','mklink','/J',str(link),str(target)],check=True,capture_output=True)


@pytest.mark.parametrize('kind',['workspace','blobs','destination'])
def test_links_and_junctions_cannot_escape(workspace,tmp_path,kind):
    root,photos,photo,trash=workspace
    if kind=='workspace':
        alias=tmp_path/'alias';link_dir(alias,root)
        with pytest.raises(ValueError,match='链接|联接'):cleanup.inspect_evidence(alias,photos)
    elif kind=='blobs':
        blobs=root/'focus-evidence/blobs';moved=tmp_path/'elsewhere';blobs.rename(moved);link_dir(blobs,moved)
        with pytest.raises(ValueError,match='链接|联接'):cleanup.inspect_evidence(root,photos)
    else:
        actual=tmp_path/'actual';actual.mkdir();link_dir(trash,actual)
        plan=cleanup.inspect_evidence(root,photos)
        with pytest.raises(ValueError,match='链接|联接'):cleanup.quarantine_evidence(plan,trash)
        assert not list(actual.iterdir())


def test_concurrent_focus_operation_blocks_cleanup(workspace):
    root,photos,photo,trash=workspace;plan=cleanup.inspect_evidence(root,photos)
    with cleanup.evidence_operation(root):
        with pytest.raises(ValueError,match='正在'):cleanup.quarantine_evidence(plan,trash)
    assert len(list((root/'focus-evidence/blobs').glob('*.zip')))==1


def test_other_process_cannot_take_evidence_lock(workspace):
    root,photos,photo,trash=workspace
    command = 'from ai_cull_assistant.evidence_cleanup import evidence_operation\nwith evidence_operation('+repr(str(root))+'):\n pass\n'
    with cleanup.evidence_operation(root):
        assert subprocess.run([sys.executable,'-B','-c',command],capture_output=True).returncode!=0
    assert subprocess.run([sys.executable,'-B','-c',command],capture_output=True).returncode==0


@pytest.mark.skipif(os.name!='nt',reason='Windows file-sharing semantics')
def test_windows_open_file_is_preserved(workspace):
    import ctypes
    from ctypes import wintypes
    root,photos,photo,trash=workspace;plan=cleanup.inspect_evidence(root,photos)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    path=root/'focus-evidence/blobs'/plan.files[0].name
    handle=kernel.CreateFileW(str(path),0x80000000,3,None,3,128,None)
    assert handle!=wintypes.HANDLE(-1).value
    try:
        result=cleanup.quarantine_evidence(plan,trash)
        assert result['moved']==0 and len(result['failed'])==1 and path.exists()
    finally:kernel.CloseHandle(handle)


def completed(tmp_path):
    from ai_cull_assistant.workflow import run_scan
    from ai_cull_assistant.session_store import save_session
    from ai_cull_assistant.ai_project import ReviewProject
    from ai_cull_assistant.crop_settings import CropSettings
    photos=tmp_path/'photos';photos.mkdir();photo=photos/'A.jpg';Image.new('RGB',(120,180),'white').save(photo)
    root=tmp_path/'work';result=run_scan(photos,root,technical_screening=False);save_session(result)
    project=ReviewProject(root);crops=CropSettings();task=project.create_task(result.assets,crops,{})
    batch=task['batches'][0]
    answer=dict(task_id=task['id'],batch_id=batch['id'],photos=[dict(photo_id=pid,rating=4,suggest_reject=False,reason='clear',review_items=[]) for pid in batch['photo_ids']])
    assert not project.ingest(task,batch,json.dumps(answer))
    project.export_final(ai_ratings=True)
    record_inputs(root/'cache/analysis','request',[photo],'p',{},'v2');record_response(root/'cache/analysis','request',{'raw_response':'clear'})
    return root,photos,result,project,crops


@pytest.mark.parametrize('archived',[False,True])
def test_archive_reopen_scores_and_missing_batch_images_rebuild_without_scan(tmp_path,archived):
    from ai_cull_assistant.workspace_archive import compact_workspace,restore_workspace
    from ai_cull_assistant.session_store import load_session
    from ai_cull_assistant.ai_project import ReviewProject
    root,photos,result,project,crops=completed(tmp_path)
    original=project.data['photos'];taskid=project.current_task()['id']
    if archived:assert compact_workspace(root)['compacted']
    before=snapshots(root);plan=cleanup.inspect_evidence(root,photos)
    assert snapshots(root)==before
    assert cleanup.quarantine_evidence(plan,tmp_path/'trash')['moved']==1
    for name,data in before.items():
        if name.parts[:2]!=('focus-evidence','blobs'):assert (root/name).read_bytes()==data
    restore_workspace(root);scan=load_session(root,photos);reopened=ReviewProject(root);reopened.refresh(scan.assets,crops)
    assert reopened.data['photos']==original
    task=reopened.current_task();batch=task['batches'][0];assert task['id']==taskid
    for name in batch['image_paths']:Path(name).unlink()
    reopened.validate_batch_sources(scan.assets,crops,batch)
    assert all(p.exists() for p in reopened.batch_images(task,batch))
    assert batch['status']=='complete' and reopened.data['photos']==original


def pump(app):
    deadline=time.monotonic()+15
    while getattr(app,'_cleaning_evidence',False) and time.monotonic()<deadline:
        app.update();time.sleep(.01)
    assert not getattr(app,'_cleaning_evidence',False)


def test_home_button_cancel_confirm_repeat_and_missing_source_preserve_records(tmp_path,monkeypatch):
    from ai_cull_assistant.app import App
    from ai_cull_assistant.settings import save_values
    from ai_cull_assistant import evidence_cleanup_ui as ui
    root,photos,result,project,crops=completed(tmp_path)
    settings=tmp_path/'settings';save_values(settings,dict(input=str(photos),workspace=str(root),options={'no_auto_updates':True}))
    app=App(settings_dir=settings);app.withdraw();app.update()
    photo=photos/'A.jpg';payload=photo.read_bytes();info=photo.stat();photo.unlink()
    before=snapshots(root);prompts=[];choice=[False]
    monkeypatch.setattr(ui.messagebox,'askyesno',lambda title,text,**kw:prompts.append(text) or choice[0])
    monkeypatch.setattr(ui.messagebox,'showinfo',lambda *a,**kw:None)
    monkeypatch.setattr(ui.messagebox,'showwarning',lambda *a,**kw:None)
    monkeypatch.setattr(ui.messagebox,'showerror',lambda *a,**kw:pytest.fail(str(a)))
    monkeypatch.setattr(cleanup,'default_quarantine_root',lambda p:tmp_path/'trash')
    try:
        assert app.evidence_cleanup_button.cget('text')=='清理证据图片'
        original_busy=app._review_busy;app._review_busy=lambda:True
        app._refresh_evidence_button()
        assert str(app.evidence_cleanup_button.cget('state'))=='disabled'
        app._review_busy=original_busy;app._refresh_evidence_button()
        app.evidence_cleanup_button.invoke();pump(app)
        assert len(prompts)==1 and '原片缺失 1' in prompts[0] and '不可恢复' in prompts[0] and '不进入回收站' in prompts[0]
        assert snapshots(root)==before and not (tmp_path/'trash').exists()
        choice[0]=True
        app.evidence_cleanup_button.invoke();pump(app)
        assert len(prompts)==2
        assert not (tmp_path/'trash').exists()
        for name,data in before.items():
            if name.parts[:2]!=('focus-evidence','blobs'):assert (root/name).read_bytes()==data
        assert not list((root/'focus-evidence/blobs').glob('*.zip'))
        app.evidence_cleanup_button.invoke();pump(app)
        assert len(prompts)==2 and '已清理 1' in app.evidence_status_var.get()
    finally:
        # Restore only the synthetic input before exercising the normal window shutdown.
        photo.write_bytes(payload);os.utime(photo,ns=(info.st_atime_ns,info.st_mtime_ns))
        app._close()


def test_focus_guard_blocks_during_quarantine_and_allows_later_regeneration(workspace):
    root,photos,photo,trash=workspace
    @cleanup.guard_focus_review
    def fake_review(asset,settings,profile,cache):
        record_inputs(cache,'new-request',[photo],'p',{},'v2');record_response(cache,'new-request',{})
    plan=cleanup.inspect_evidence(root,photos);cleanup.quarantine_evidence(plan,trash)
    with cleanup.evidence_operation(root):
        with pytest.raises(ValueError):fake_review(None,None,{},root/'cache/analysis')
    fake_review(None,None,{},root/'cache/analysis')
    assert len(cleanup.inspect_evidence(root,photos).files)==1
    assert cleanup.evidence_status(root)==''
