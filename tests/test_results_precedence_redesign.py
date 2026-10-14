"""Authorized 2026-10 redesign policy and dependency regressions."""
from datetime import datetime
import copy
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from ai_cull_assistant.ai_project import ReviewProject, photo_id, technical_fingerprint
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.lightroom_results import effective_decision, current_focus, current_selection
from ai_cull_assistant.models import PhotoAsset


def make_project(tmp_path):
    assets=[]
    for i in range(3):
        path=tmp_path/f'{i}.jpg';Image.new('RGB',(120,160),'white').save(path)
        assets.append(PhotoAsset(str(i),path,path,None,path,datetime.now(),'.jpg',
            group_id=1 if i<2 else 2,preview_path=path,screening_reason='subject_not_obviously_blurred'))
    project=ReviewProject(tmp_path/'workspace');crops=CropSettings();project.refresh(assets,crops)
    return project,assets,crops


def seed(project,asset,crops):
    row=project.data['photos'][photo_id(asset)]
    row['ai']={'rating':4,'suggest_reject':False,'reason':'composition','review_items':[],
               'fingerprint':row['fingerprint']}
    asset.ai_focus_result={'status':'clear','reason':'native detail','source':'api',
                          'technical_fingerprint':technical_fingerprint(asset,crops)}


def test_effective_manual_survives_local_rejection_ai_blur_and_pending():
    row={'final':{'confirmed':True,'rating':5,'pick_status':1},'fingerprint':'g','stale':True,
         'technical_fingerprint':'p','focus_fingerprint':'p','focus_review':True,
         'technical_rejected':True,'ai_focus_result':{'status':'blur','reason':'blur'}}
    result=effective_decision(row)
    assert (result['source'],result['rating'],result['pick_status'])==('manual',5,1)


@pytest.mark.parametrize('state,expected,pick',[('clear','keep',1),('severe_blur','reject',-1),('uncertain','review',None)])
def test_local_never_manufactures_stars(state,expected,pick):
    value=effective_decision({'clarity_evidence':{'state':state}})
    assert (value['recommendation'],value['pick_status'],value['rating'])==(expected,pick,None)


def test_ai_above_local_and_expired_ai_is_not_a_fallback():
    row={'fingerprint':'g','technical_fingerprint':'p','technical_rejected':True,
         'ai':{'fingerprint':'g','rating':5,'suggest_reject':False,'reason':'usable'},
         'clarity_evidence':{'state':'severe_blur'}}
    assert effective_decision(row)['source']=='ai'
    assert effective_decision(row)['pick_status']==1
    row['stale']=True
    assert effective_decision(row)['source']=='local'
    assert effective_decision(row)['rating'] is None


def test_regroup_expires_group_selection_only_and_preserves_manual(tmp_path):
    project,assets,crops=make_project(tmp_path)
    for asset in assets:seed(project,asset,crops)
    project.refresh(assets,crops)
    for row in project.data['photos'].values():
        if row.get('ai'):row['ai']['fingerprint']=row['fingerprint'];row['stale']=False
    project.confirm(photo_id(assets[0]),5,1)
    before=copy.deepcopy(project.data['photos'])
    assets[1].group_id=3;project.refresh(assets,crops)
    for asset in assets[:2]:
        row=project.data['photos'][photo_id(asset)]
        assert row['stale'] and current_selection(row) is None
        assert current_focus(row)['status']=='clear'
        assert row['ai']==before[photo_id(asset)]['ai']
    assert not project.data['photos'][photo_id(assets[2])]['stale']
    assert project.effective_decision(photo_id(assets[0]))['rating']==5


def test_subject_change_invalidates_photo_focus_and_entire_group_not_other_group(tmp_path):
    project,assets,crops=make_project(tmp_path)
    for asset in assets:seed(project,asset,crops)
    project.refresh(assets,crops)
    for row in project.data['photos'].values():
        if row.get('ai'):row['ai']['fingerprint']=row['fingerprint'];row['stale']=False
    crops.photos[crops.key(assets[0])]={'manual_face':[.1,.1,.3,.4]}
    project.refresh(assets,crops)
    first,second,third=(project.data['photos'][photo_id(asset)] for asset in assets)
    assert current_focus(first) is None and first['focus_stale']
    assert first['ai_focus_result']['reason']=='native detail'
    assert current_focus(second) is not None and current_focus(third) is not None
    assert first['stale'] and second['stale'] and not third['stale']
    reloaded=ReviewProject(project.workspace);reloaded.refresh(assets,crops)
    assert current_focus(reloaded.data['photos'][photo_id(assets[0])]) is None


def test_mode_invalidates_focus_even_when_source_and_boxes_unchanged(tmp_path):
    project,assets,crops=make_project(tmp_path)
    seed(project,assets[0],crops);project.refresh(assets,crops)
    assets[0].analysis_mode='landscape';project.refresh(assets,crops)
    row=project.data['photos'][photo_id(assets[0])]
    assert row['focus_stale'] and current_focus(row) is None


def test_selection_does_not_require_paid_focus_and_web_does_not_bundle_it(tmp_path,monkeypatch):
    project,assets,crops=make_project(tmp_path)
    for asset in assets:
        asset.clarity_version='clarity-v2';asset.clarity_evidence={'state':'uncertain'}
        asset.screening_reason='face_focus_uncertain'
    monkeypatch.setattr('ai_cull_assistant.ai_focus.prepare_focus_images',lambda *a:pytest.fail('selection must not trigger focus evidence'))
    monkeypatch.setattr('ai_cull_assistant.ai_api.call_model',lambda *a:pytest.fail('no automatic paid calls'))
    task=project.create_task(assets,crops,{})
    assert sum(len(batch['photo_ids']) for batch in task['batches'])==3
    assert '技术证据' in task['batches'][0]['prompt']
    assert '完整画面' in task['batches'][0]['prompt']
    submission=project.create_web_submission(task,[b['id'] for b in task['batches']])
    assert submission['focus_photo_ids']==[] and submission['focus_image_map']=={}


def test_export_matches_effective_result_and_never_changes_originals_or_xmp(tmp_path):
    project,assets,crops=make_project(tmp_path)
    seed(project,assets[0],crops);project.refresh(assets,crops)
    project.confirm(photo_id(assets[1]),5,0)
    assets[2].clarity_evidence={'state':'uncertain'}
    sidecar=assets[0].primary_path.with_suffix('.xmp');sidecar.write_text('existing stars')
    before={p:p.read_bytes() for p in [a.primary_path for a in assets]+[sidecar]}
    rows=json.loads(project.export_final().read_text())['photos']
    for asset,row in zip(assets,rows):
        decision=project.effective_decision(photo_id(asset))
        assert row.get('rating')==decision['rating']
        assert row.get('pick_status')==decision['pick_status']
        assert row['decision_source']==decision['source']
    assert 'rating' not in rows[2] and 'pick_status' not in rows[2]
    assert before=={p:p.read_bytes() for p in before}


def test_replacement_retains_manual_history_and_old_task_files(tmp_path):
    project,assets,crops=make_project(tmp_path)
    old=project.create_task(assets,crops,{})
    pid=photo_id(assets[0]);project.confirm(pid,5,1)
    seed(project,assets[0],crops)
    old_files=[path for batch in old['batches'] for path in batch['image_paths']]
    project.create_task(assets,crops,{},replace_current=True)
    assert project.data['photos'][pid]['final']['rating']==5
    assert len(project.data['tasks'])==2 and old['superseded']
    from pathlib import Path
    assert all(Path(path).is_file() for path in old_files)


def test_stale_ai_flag_cannot_reimport_preserved_result(tmp_path):
    project,assets,crops=make_project(tmp_path);seed(project,assets[0],crops)
    project.refresh(assets,crops)
    assets[0].ai_focus_stale=True
    project.refresh(assets,crops)
    assert current_focus(project.data['photos'][photo_id(assets[0])]) is None


def test_multi_subject_blur_is_review_not_whole_photo_reject():
    row={'ai_focus_result':{'status':'blur','reason':'second member',
         'participants':[{'status':'clear'},{'status':'blur'}]}}
    assert effective_decision(row)['recommendation']=='review'
