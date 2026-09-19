import json
from pathlib import Path
import pytest
from ai_cull_assistant import project_storage as storage
from ai_cull_assistant.settings import read_values,save_values

def test_sources_with_same_name_get_distinct_stable_workspaces(tmp_path):
    settings=tmp_path/'settings'
    a=tmp_path/'one'/'photos';b=tmp_path/'two'/'photos'
    first=storage.workspace_for(settings,a);second=storage.workspace_for(settings,b)
    assert first!=second and first.parent==tmp_path/'workspaces'
    assert storage.workspace_for(settings,a)==first
    with pytest.raises(ValueError):storage.workspace_for(settings,b,preferred=first)
    storage.save_workspace_preferences(first,{'photos':{'first':{}}},{'grouping':'严格','no_auto_updates':True})
    assert storage.load_workspace_preferences(settings,first,a)['options']=={'grouping':'严格'}
    assert storage.load_workspace_preferences(settings,second,b)['face_crop']=={}

def test_workspace_migration_rebases_only_owned_paths_and_preserves_source(tmp_path):
    old=tmp_path/'repository';old.mkdir()
    photo=old/'original.jpg';photo.write_bytes(b'original')
    preview=old/'previews'/'p.jpg';preview.parent.mkdir();preview.write_bytes(b'preview')
    snapshot=old/'ai_tasks'/'task'/'sheet.jpg';snapshot.parent.mkdir(parents=True);snapshot.write_bytes(b'sheet')
    original={'workspace_dir':str(old),'input_dir':str(old),
              'primary_path':str(photo),'preview_path':str(preview),
              'image_paths':[str(snapshot)],'fingerprint':'keep','image_hashes':['keep']}
    (old/'scan-session.json').write_text(json.dumps(original))
    (old/'ai-api-profiles.json').write_text('{"do_not_copy":true}')
    new=tmp_path/'workspaces'/'project'
    storage.migrate_workspace(old,new)
    data=json.loads((new/'scan-session.json').read_text())
    assert data['workspace_dir']==str(new)
    assert data['input_dir']==str(old) and data['primary_path']==str(photo)
    assert data['preview_path']==str(new/'previews'/'p.jpg')
    assert data['image_paths']==[str(new/'ai_tasks'/'task'/'sheet.jpg')]
    assert data['fingerprint']=='keep' and data['image_hashes']==['keep']
    assert json.loads((old/'scan-session.json').read_text())==original
    assert not (new/'original.jpg').exists() and not (new/'ai-api-profiles.json').exists()
    assert storage.migrate_workspace(old,new)==new

def test_runtime_migration_preserves_profile_ids_and_user_overrides(tmp_path,monkeypatch):
    old=tmp_path/'program';old.mkdir();data=tmp_path/'data'
    photos=tmp_path/'photos';photos.mkdir()
    save_values(old,{'input':str(photos),'workspace':str(old),'face_crop':{'scale_factor':1.2},'selected_api_profile_id':'same-id'})
    (old/'ai-api-profiles.json').write_text('{"version":1,"profiles":[{"id":"same-id"}]}')
    (old/'previews').mkdir();(old/'previews'/'p.jpg').write_bytes(b'preview')
    monkeypatch.setattr(storage,'application_dir',lambda:old)
    monkeypatch.setattr(storage,'runtime_data_root',lambda:data)
    settings=storage.prepare_runtime_settings()
    values=read_values(settings);workspace=Path(values['workspace'])
    assert settings==data/'settings' and workspace.parent==data/'workspaces'
    assert values['selected_api_profile_id']=='same-id'
    assert (settings/'ai-api-profiles.json').read_bytes()==(old/'ai-api-profiles.json').read_bytes()
    assert storage.load_workspace_preferences(settings,workspace,photos)['face_crop']=={'scale_factor':1.2}
    save_values(settings,{'selected_api_profile_id':'new-id'})
    assert storage.prepare_runtime_settings()==settings
    assert read_values(settings)['selected_api_profile_id']=='new-id'

def test_migration_never_overwrites_existing_destination(tmp_path):
    old=tmp_path/'old';old.mkdir();new=tmp_path/'new';new.mkdir()
    sentinel=new/'scan-session.json';sentinel.write_bytes(b'untouched')
    with pytest.raises(ValueError):storage.migrate_workspace(old,new)
    assert sentinel.read_bytes()==b'untouched'


def test_migrated_completed_analysis_and_ai_task_reopen_without_rescan(tmp_path):
    from PIL import Image
    from ai_cull_assistant.workflow import run_scan
    from ai_cull_assistant.session_store import save_session,load_session
    from ai_cull_assistant.ai_project import ReviewProject
    from ai_cull_assistant.crop_settings import CropSettings
    photos=tmp_path/'photos';photos.mkdir()
    Image.new('RGB',(80,120),'white').save(photos/'A.jpg')
    old=tmp_path/'old'
    result=run_scan(photos,old,technical_screening=False)
    save_session(result)
    project=ReviewProject(old);crops=CropSettings()
    task=project.create_task(result.assets,crops,{})
    batch=task['batches'][0]
    answer={'task_id':task['id'],'batch_id':batch['id'],'photos':[
        {'photo_id':pid,'rating':4,'suggest_reject':False,'reason':'清晰','review_items':[]} for pid in batch['photo_ids']]}
    assert project.ingest(task,batch,json.dumps(answer))==[]
    new=tmp_path/'new';storage.migrate_workspace(old,new)
    loaded=load_session(new,photos)
    assert loaded.workspace_dir==new and loaded.preview_dir==new/'previews'
    reopened=ReviewProject(new);reopened.refresh(loaded.assets,crops)
    assert reopened.current_task()['id']==task['id']
    assert reopened.current_task()['batches'][0]['status']=='complete'
    assert all(p.is_file() for p in reopened.batch_images(reopened.current_task(),reopened.current_task()['batches'][0]))
    assert all(p['ai']['rating']==4 and not p['stale'] for p in reopened.data['photos'].values())

@pytest.mark.parametrize('archived', [False, True])
def test_discover_copied_workspace_on_new_computer(tmp_path, archived):
    import zipfile
    photos=tmp_path/'photos';photos.mkdir()
    settings=tmp_path/'new-computer'/'settings'
    copied=settings.parent/'workspaces'/'copied-name'
    copied.mkdir(parents=True)
    session=dict(version=1,input_dir=str(photos),workspace_dir=str(copied),source_stats={})
    if archived:
        # Discovery must not extract an archive merely to identify its source.
        with zipfile.ZipFile(copied/'.workspace-archive.zip','w') as bundle:
            bundle.writestr('scan-session.json', json.dumps(session))
    else:
        (copied/'scan-session.json').write_text(json.dumps(session))
    assert storage.discover_workspaces(settings,photos)==[copied.resolve()]
    if archived:
        assert not (copied/'scan-session.json').exists()
    else:
        assert storage.workspace_for(settings,photos)==copied.resolve()
        identity=json.loads((copied/'workspace-identity.json').read_text())['workspace_id']
        assert storage.workspace_for(settings,photos)==copied.resolve()
        assert json.loads((copied/'workspace-identity.json').read_text())['workspace_id']==identity


def test_stale_machine_registry_finds_existing_migrated_workspace(tmp_path):
    photos=tmp_path/'photos';photos.mkdir()
    settings=tmp_path/'settings'
    copied=tmp_path/'workspaces'/'moved';copied.mkdir(parents=True)
    (copied/'workspace-identity.json').write_text(json.dumps(dict(workspace_id='stable',input_dir=str(photos))))
    missing=tmp_path/'old-pc'/'missing'
    save_values(settings,dict(workspaces={storage._key(photos):str(missing)}))
    assert storage.workspace_for(settings,photos,preferred=missing)==copied.resolve()
    assert not missing.exists()
    assert json.loads((copied/'workspace-identity.json').read_text())['workspace_id']=='stable'


def test_ambiguous_workspace_copies_require_explicit_choice(tmp_path):
    photos=tmp_path/'photos';photos.mkdir()
    for name in ('a','b'):
        copied=tmp_path/'workspaces'/name;copied.mkdir(parents=True)
        (copied/'workspace-identity.json').write_text(json.dumps(dict(input_dir=str(photos))))
    with pytest.raises(ValueError,match='多个'):
        storage.workspace_for(tmp_path/'settings',photos)
    assert storage.workspace_for(tmp_path/'settings',photos,preferred=tmp_path/'workspaces'/'b').name=='b'


def test_select_source_finds_copied_archived_workspace_without_old_registry(tmp_path):
    import shutil
    from test_workspace_archive import _completed_workspace
    from ai_cull_assistant.workspace_archive import compact_workspace
    from ai_cull_assistant.session_store import load_session
    from ai_cull_assistant.ai_project import ReviewProject
    photos, old, result, project, task, batch, export, crops = _completed_workspace(tmp_path)
    assert compact_workspace(old)['compacted']
    settings=tmp_path/'new-machine'/'settings'
    copied=settings.parent/'workspaces'/'my-copied-project'
    shutil.copytree(old,copied)
    selected=storage.workspace_for(settings,photos)
    assert selected==copied.resolve()
    loaded=load_session(selected,photos)
    assert loaded.workspace_dir==selected
    reopened=ReviewProject(selected)
    reopened.refresh(loaded.assets,crops)
    assert reopened.current_task()['id']==task['id']
    assert reopened.current_task()['batches'][0]['status']=='complete'
    assert all(p['ai']['rating']==4 for p in reopened.data['photos'].values())
