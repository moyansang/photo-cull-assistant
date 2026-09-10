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
    assets[0].auto_rejected=True
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
