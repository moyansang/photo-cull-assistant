from datetime import datetime
import json
from pathlib import Path
import pytest
from PIL import Image
from ai_cull_assistant.ai_project import ReviewProject,photo_id,parse_answer
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.crop_settings import CropSettings


def setup_project(tmp_path):
    p=tmp_path/'photos';p.mkdir()
    assets=[]
    for name in ['A','B']:
        path=p/(name+'.jpg');Image.new('RGB',(100,150),'white').save(path)
        assets.append(PhotoAsset(name,path,path,None,path,datetime.now(),'.jpg',group_id=1,preview_path=path))
    project=ReviewProject(tmp_path/'workspace')
    task=project.create_task(assets,CropSettings(),{'intensity':'均衡保留'})
    return project,assets,task,task['batches'][0]


def answer(task,batch,rating=4):
    return json.dumps(dict(task_id=task['id'],batch_id=batch['id'],photos=[dict(photo_id=pid,rating=rating,suggest_reject=False,reason='表情自然',review_items=[]) for pid in batch['photo_ids']]))


def test_snapshot_prompt_and_strict_answers(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    assert str(tmp_path) not in batch['prompt']
    assert all(photo_id(a) in batch['prompt'] for a in assets)
    assert project.ingest(task,batch,answer(task,batch))==[]
    bad=json.loads(answer(task,batch));bad['photos'][0]['rating']=True
    bad['photos'].append(dict(bad['photos'][1],rating=5))
    assert project.ingest(task,batch,json.dumps(bad))
    assert batch['status']=='partial'
    bad['task_id']='wrong'
    assert project.ingest(task,batch,json.dumps(bad))
    assert batch['status']=='invalid' and len(batch['raw_responses'])==3


def test_human_decisions_preserved_and_export_only_confirmed(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    project.ingest(task,batch,answer(task,batch))
    with pytest.raises(ValueError):project.export_final()
    project.confirm(photo_id(assets[0]),5,None)
    project.ingest(task,batch,answer(task,batch,1))
    assert project.data['photos'][photo_id(assets[0])]['final']['rating']==5
    output=json.loads(project.export_final().read_text('utf-8'))
    assert len(output['photos'])==1 and output['photos'][0]['rating']==5
    assert 'pick_status' not in output['photos'][0]
    assert not list((tmp_path/'photos').glob('*.xmp'))


def test_changed_group_invalidates_results_but_keeps_human_values(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    project.confirm(photo_id(assets[0]),4,-1)
    assets[0].group_id=2;project.refresh(assets,CropSettings())
    assert project.data['photos'][photo_id(assets[0])]['stale']
    with pytest.raises(ValueError):project.export_final()
    assert project.ingest(task,batch,answer(task,batch))
    project.confirm(photo_id(assets[0]),4,0)
    assert json.loads(project.export_final().read_text('utf-8'))['photos'][0]['pick_status']==0


def test_restart_and_legacy(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    batch['status']='running';project.save()
    loaded=ReviewProject(project.workspace)
    assert loaded.current_task()['batches'][0]['status']=='failed'
    loaded.refresh(assets,CropSettings())
    assert loaded.ingest_legacy(task,batch,'A,4\nB,3')==[]
    assert loaded.ingest_legacy(task,batch,'A,5\nunknown,4')
    assert '简化结果' in loaded.data['photos'][photo_id(assets[0])]['ai']['review_items'][0]


def test_snapshot_tampering_is_rejected(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    Path(batch['image_paths'][0]).write_bytes(b'changed')
    with pytest.raises(ValueError,match='快照'):project.batch_images(task,batch)


def test_receipt_requires_current_export_and_matching_values(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    project.confirm(photo_id(assets[0]),4,1)
    output=json.loads(project.export_final().read_text('utf-8'))
    report=dict(status='applied',export_id=output['export_id'],changes=[dict(path=output['photos'][0]['path'],requested_rating=4,requested_pick_status=1)])
    assert '已应用 1/1' in project.import_receipt(json.dumps(report))
    report['export_id']='older-export'
    with pytest.raises(ValueError):project.import_receipt(json.dumps(report))

def test_group_membership_changes_invalidate_other_group_photos(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    pid=photo_id(assets[0]);project.confirm(pid,5,None)
    assets[1].group_id=2
    project.refresh(assets,CropSettings())
    assert project.data['photos'][pid]['stale']
    with pytest.raises(ValueError):project.batch_images(task,batch)


def test_old_receipt_cannot_claim_changed_decisions_applied(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    pid=photo_id(assets[0]);project.confirm(pid,4,1)
    output=json.loads(project.export_final().read_text('utf-8'))
    project.confirm(pid,5,1)
    receipt=dict(status='applied',export_id=output['export_id'],changes=[])
    with pytest.raises(ValueError,match='重新导出'):project.import_receipt(json.dumps(receipt))


def test_explicit_split_retry_leaves_old_task_and_human_results(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    project.confirm(photo_id(assets[0]),5,None)
    small=project.create_task(assets,CropSettings(),{'_split_limit':1},photo_ids=batch['photo_ids'])
    assert len(small['batches'])==2 and len(task['batches'])==1
    assert project.data['photos'][photo_id(assets[0])]['final']['rating']==5
    assert '同组可能未完整提供' in small['batches'][0]['prompt']


def test_tasks_exclude_technical_rejects_from_implicit_explicit_and_refine_scopes(tmp_path):
    project,assets,_,_=setup_project(tmp_path)
    rejected=assets[1]
    rejected.auto_rejected=True
    rejected.screening_reason='severe_subject_blur'
    rejected_id=photo_id(rejected)

    for kind,photo_ids in [('initial',None),('initial',[photo_id(assets[0]),rejected_id]),('refine',[photo_id(assets[0]),rejected_id])]:
        task=project.create_task(assets,CropSettings(),{},kind=kind,photo_ids=photo_ids)
        assert [pid for batch in task['batches'] for pid in batch['photo_ids']]==[photo_id(assets[0])]
        assert all(rejected_id not in batch['prompt'] for batch in task['batches'])

    with pytest.raises(ValueError,match='没有可评审'):
        project.create_task(assets,CropSettings(),{},photo_ids=[rejected_id])


def test_existing_batch_cannot_be_used_after_photo_becomes_technical_reject(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    assets[1].auto_rejected=True
    assets[1].screening_reason='camera_shake'

    with pytest.raises(ValueError,match='技术筛选'):
        project.batch_images(task,batch)
    with pytest.raises(ValueError,match='技术筛选'):
        project.prompt(task,batch)
    with pytest.raises(ValueError,match='技术筛选'):
        project.create_web_submission(task,[batch['id']])


def test_replacing_current_task_discards_history_and_resets_scoped_results(tmp_path):
    project,assets,old_task,old_batch=setup_project(tmp_path)
    project.ingest(old_task,old_batch,answer(old_task,old_batch))
    project.confirm(photo_id(assets[0]),5,1)
    old_task_root=project.workspace/'ai_tasks'/old_task['id']
    assert old_task_root.is_dir()

    new_task=project.create_task(assets,CropSettings(),{'intensity':'更严格'},replace_current=True)

    assert project.data['tasks']==[new_task]
    assert project.current_task() is new_task
    assert not old_task_root.exists()
    for asset in assets:
        photo=project.data['photos'][photo_id(asset)]
        assert 'ai' not in photo
        assert photo['final']==dict(rating=None,pick_status=None,confirmed=False)
        assert photo['history']==[] and not photo['stale']


def test_replacement_preparation_failure_keeps_old_task_and_results(tmp_path,monkeypatch):
    project,assets,old_task,old_batch=setup_project(tmp_path)
    project.ingest(old_task,old_batch,answer(old_task,old_batch))
    project.confirm(photo_id(assets[0]),5,1)
    before=json.loads(json.dumps(project.data))

    def fail_after_creating_folder(_assets,folder,**_kwargs):
        Path(folder).mkdir(parents=True)
        raise OSError('cannot render')

    monkeypatch.setattr('ai_cull_assistant.ai_project.generate_contact_sheets',fail_after_creating_folder)
    with pytest.raises(OSError,match='cannot render'):
        project.create_task(assets,CropSettings(),{},replace_current=True)

    assert project.data==before
    assert {path.name for path in (project.workspace/'ai_tasks').iterdir()}=={old_task['id']}


def test_replacement_save_failure_rolls_back_old_task_and_results(tmp_path,monkeypatch):
    project,assets,old_task,old_batch=setup_project(tmp_path)
    project.ingest(old_task,old_batch,answer(old_task,old_batch))
    project.confirm(photo_id(assets[0]),5,1)
    before=json.loads(json.dumps(project.data))
    real_save=project.save
    save_calls=0

    def fail_commit():
        nonlocal save_calls
        save_calls+=1
        if save_calls==2:raise OSError('disk full')
        real_save()

    monkeypatch.setattr(project,'save',fail_commit)
    with pytest.raises(OSError,match='disk full'):
        project.create_task(assets,CropSettings(),{},replace_current=True)

    assert project.data==before
    assert {path.name for path in (project.workspace/'ai_tasks').iterdir()}=={old_task['id']}


def test_replacement_never_deletes_folder_named_by_unsafe_persisted_task_id(tmp_path):
    project,assets,_,_=setup_project(tmp_path)
    keep=project.workspace/'keep'
    keep.mkdir()
    (keep/'evidence.txt').write_text('keep',encoding='utf-8')
    project.data['tasks'].append({'id':'../keep','kind':'initial','batches':[]})

    project.create_task(assets,CropSettings(),{},replace_current=True)

    assert (keep/'evidence.txt').read_text('utf-8')=='keep'


def test_all_technical_rejects_can_replace_old_task_with_empty_fresh_task(tmp_path):
    project,assets,old_task,old_batch=setup_project(tmp_path)
    project.ingest(old_task,old_batch,answer(old_task,old_batch))
    project.confirm(photo_id(assets[0]),5,1)
    for asset in assets:
        asset.auto_rejected=True
        asset.screening_reason='severe_subject_blur'

    task=project.create_task(assets,CropSettings(),{},replace_current=True)

    assert project.data['tasks']==[task] and task['batches']==[]
    assert all('ai' not in project.data['photos'][photo_id(asset)] for asset in assets)
    assert all(not project.data['photos'][photo_id(asset)]['final']['confirmed'] for asset in assets)
    rows=json.loads(project.export_final(ai_ratings=True).read_text('utf-8'))['photos']
    assert len(rows)==2 and all(row['pick_status']==-1 and 'rating' not in row for row in rows)


def setup_merged_web_submission(tmp_path):
    project,assets,_,_=setup_project(tmp_path)
    task=project.create_task(assets,CropSettings(),{'intensity':'均衡保留','_split_limit':1})
    submission=project.create_web_submission(task,[batch['id'] for batch in task['batches']])
    return project,assets,task,submission


def web_answer(task,submission,rating=4):
    return json.dumps(dict(task_id=task['id'],batch_id=submission['id'],photos=[
        dict(photo_id=pid,rating=rating,suggest_reject=False,reason='合并比较后保留',review_items=[])
        for pid in submission['photo_ids']
    ]))


def test_web_submission_combines_batches_and_persists_immutable_snapshots(tmp_path):
    project,assets,task,submission=setup_merged_web_submission(tmp_path)
    assert submission['id']=='W001'
    assert submission['batch_ids']==['B001','B002']
    assert submission['photo_ids']==[photo_id(asset) for asset in assets]
    assert all(pid in submission['prompt'] for pid in submission['photo_ids'])
    assert f'"batch_id":"{submission["id"]}"' in submission['prompt']
    images=project.web_images(task,submission)
    assert len(images)==2 and len({image.name for image in images})==2
    assert all(image.parent.name=='images' and image.parent.parent.name=='W001' for image in images)
    assert (images[0].parent.parent/'prompt.txt').read_text('utf-8')==submission['prompt']

    loaded=ReviewProject(project.workspace)
    restored=loaded.current_task()['web_submissions'][0]
    assert restored['id']=='W001'
    assert [path.name for path in loaded.web_images(loaded.current_task(),restored)]==[path.name for path in images]


def test_web_submission_applies_all_batches_and_preserves_human_decisions(tmp_path):
    project,assets,task,submission=setup_merged_web_submission(tmp_path)
    decided=photo_id(assets[0])
    project.confirm(decided,5,1)
    assert project.ingest_web(task,submission,web_answer(task,submission,3))==[]
    assert submission['status']=='complete'
    assert all(batch['status']=='complete' for batch in task['batches'])
    for pid in submission['photo_ids']:
        result=project.data['photos'][pid]['ai']
        assert result['web_submission_id']=='W001'
        assert result['batch_id']==submission['photo_batches'][pid]
    assert project.data['photos'][decided]['final']==dict(
        rating=5,pick_status=1,confirmed=True,fingerprint=project.data['photos'][decided]['fingerprint'])


@pytest.mark.parametrize('damage', ['missing','duplicate'])
def test_web_submission_rejects_incomplete_answer_without_partial_results(tmp_path,damage):
    project,assets,task,submission=setup_merged_web_submission(tmp_path)
    payload=json.loads(web_answer(task,submission))
    if damage=='missing':
        payload['photos'].pop()
    else:
        payload['photos'].append(dict(payload['photos'][0]))
    issues=project.ingest_web(task,submission,json.dumps(payload))
    assert issues and submission['status']=='invalid'
    assert all('ai' not in project.data['photos'][pid] for pid in submission['photo_ids'])
    assert all(batch['status']=='pending' for batch in task['batches'])


def test_web_submission_rejects_duplicates_and_stale_photos(tmp_path):
    project,assets,task,submission=setup_merged_web_submission(tmp_path)
    with pytest.raises(ValueError,match='重复选择'):
        project.create_web_submission(task,['B001','B001'])
    assets[0].group_id=2
    project.refresh(assets,CropSettings())
    issues=project.ingest_web(task,submission,web_answer(task,submission))
    assert issues and '改变' in issues[0]
    assert all('ai' not in project.data['photos'][pid] for pid in submission['photo_ids'])
    assert all(batch['status']=='pending' for batch in task['batches'])


def test_web_submission_preserves_refine_intent_and_recovers_failed_save(tmp_path,monkeypatch):
    project,assets,task,_=setup_project(tmp_path)
    task=project.create_task(assets,CropSettings(),{'_split_limit':1},kind='refine')
    real_save=project.save
    monkeypatch.setattr(project,'save',lambda: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError,match='disk full'):
        project.create_web_submission(task,['B001','B002'])
    assert not (project.workspace/'ai_tasks'/task['id']/'web'/'W001').exists()
    monkeypatch.setattr(project,'save',real_save)
    submission=project.create_web_submission(task,['B001','B002'])
    assert submission['id']=='W001'
    assert '跨组精选，减少重复并统一优先级' in submission['prompt']


def test_export_ai_ratings_for_lightroom_review(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    project.ingest(task,batch,answer(task,batch))
    first,second=batch['photo_ids']
    project.data['photos'][first]['ai']['suggest_reject']=True
    payload=json.loads(project.export_final(ai_ratings=True).read_text('utf-8'))
    assert len(payload['photos'])==2
    assert all(row['rating']==4 for row in payload['photos'])
    assert payload['photos'][0]['pick_status']==-1
    assert 'pick_status' not in payload['photos'][1]
    assert not project.data['photos'][first]['final']['confirmed']
    project.confirm(first,5,1)
    project.data['photos'][second]['stale']=True
    payload=json.loads(project.export_final(ai_ratings=True).read_text('utf-8'))
    assert len(payload['photos'])==1
    assert payload['photos'][0]['rating']==5 and payload['photos'][0]['pick_status']==1


def test_lr_export_includes_technical_reject_without_ai(tmp_path):
    project,assets,task,batch=setup_project(tmp_path)
    assets[1].auto_rejected=True
    assets[1].screening_reason='severe_subject_blur'
    task=project.create_task(assets,project._crops,{},replace_current=True)
    batch=task['batches'][0]
    project.ingest(task,batch,answer(task,batch))
    rejected_id=photo_id(assets[1])
    rejected=project.data['photos'][rejected_id]
    rejected.pop('ai',None)
    rejected['final']=dict(rating=5,pick_status=1,confirmed=True,fingerprint='older')
    rejected['stale']=True
    rows=json.loads(project.export_final(ai_ratings=True).read_text('utf-8'))['photos']
    assert len(rows)==2
    assert rows[0]['rating']==4
    assert rows[1]['pick_status']==-1 and 'rating' not in rows[1]
    project.confirm(rejected_id,None,1)
    rows=json.loads(project.export_final(ai_ratings=True).read_text('utf-8'))['photos']
    assert rows[1]['pick_status']==1
