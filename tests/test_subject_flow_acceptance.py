"""Headless acceptance of subject editor/save/local-rescan wiring.

Pose outputs are controlled; segmentation, body metrics, project persistence,
UI callbacks and the real processing pipeline execute. This is not a detector
accuracy benchmark and does not instantiate Tk or call an AI endpoint.
"""
from copy import deepcopy
from hashlib import sha256
import json
from threading import Event
from types import SimpleNamespace as NS

import numpy as np
import pytest
from PIL import Image

from ai_cull_assistant import analysis_modes, body_focus
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.screening import ScreeningResult


def face_result(boxes, states=None):
    states=states or ['clear']*len(boxes)
    values=[dict(participant=i,normalized_box=box,state=state,landmarks_used=True,reasons=[])
            for i,(box,state) in enumerate(zip(boxes,states),1)]
    return ScreeningResult(False,'subject_not_obviously_blurred',True,focus_evidence={'state':'clear','participants':values})


def body(box,head,state='clear'):
    return dict(box=box,head_box=head,state=state,source='manual',confidence=.9,
                evidence_kind='body',eye_evidence=False,reasons=[])


def test_face_and_body_same_person_merge_without_hiding_body_issue():
    face=(.25,.1,.15,.2)
    clearbody=body((.1,.05,.5,.9),(.23,.08,.19,.24))
    result=analysis_modes._portrait_result(face_result([face]),bodies=[clearbody])
    evidence=result.focus_evidence
    assert len(evidence['subjects'])==1
    assert evidence['body_results'][0]['associated_face']==1
    assert evidence['subjects'][0]['body_evidence'][0]['eye_evidence'] is False
    assert evidence['recommendation']=='keep'
    blurbody=dict(clearbody,state='severe_blur')
    result=analysis_modes._portrait_result(face_result([face]),bodies=[blurbody])
    assert len(result.focus_evidence['subjects'])==1
    assert result.focus_evidence['subjects'][0]['face_state']=='clear'
    assert result.focus_evidence['body_results'][0]['state']=='severe_blur'
    assert result.focus_evidence['recommendation']=='review' and not result.rejected


def test_ambiguous_overlap_is_review_instead_of_guessing_person_identity():
    faces=[(.2,.1,.15,.2),(.27,.1,.15,.2)]
    result=analysis_modes._portrait_result(face_result(faces),bodies=[body((.1,0,.7,1),(.17,.08,.29,.25))])
    evidence=result.focus_evidence
    assert evidence['body_results'][0]['associated_face'] is None
    assert evidence['body_results'][0]['association']=='ambiguous'
    assert evidence['body_results'][0]['reliability']=='insufficient'
    assert evidence['recommendation']=='review'


def test_face_and_different_body_stay_distinct_subjects():
    result=analysis_modes._portrait_result(face_result([(.1,.1,.15,.2)]),
                bodies=[body((.6,.05,.35,.9),(.67,.1,.15,.2),'severe_blur')])
    assert len(result.focus_evidence['subjects'])==2
    assert result.focus_evidence['body_results'][0]['association']=='separate_body'
    assert result.focus_evidence['recommendation']=='review'


def _mock_models(monkeypatch):
    def detect(image):
        height,width=image.shape[:2]
        return np.array([[.3*width,.03*height,.7*width,.2*height,
                          .5*width,.65*height,.5*width,.02*height,
                          .5*width,.2*height,.5*width,.03*height,.95]],dtype=np.float32)
    def estimate(image,person):
        height,width=image.shape[:2]
        points=np.zeros((33,5),dtype=np.float32)
        for index,xy in {11:(.25,.25),12:(.75,.25),13:(.15,.45),14:(.85,.45),
                         15:(.12,.63),16:(.88,.63),23:(.35,.65),24:(.65,.65),
                         25:(.32,.82),26:(.68,.82),27:(.30,.96),28:(.70,.96)}.items():
            points[index,:2]=(xy[0]*width,xy[1]*height);points[index,3:]=.99
        return dict(landmarks=points,mask=np.full((height,width),255,np.uint8),confidence=.99)
    monkeypatch.setattr(body_focus,'_get_models',lambda:(NS(infer=detect),NS(infer=estimate)))


def test_manual_body_editor_to_settings_save_rescan_and_results(tmp_path,monkeypatch):
    from ai_cull_assistant import subject, body_subject_dialog, ai_focus
    from ai_cull_assistant.app import App
    from ai_cull_assistant.crop_dialog import CropDialog
    from ai_cull_assistant.body_subject_dialog import BodySubjectDialog
    from ai_cull_assistant.processing_job import start_job
    from ai_cull_assistant.session_store import load_session, save_session
    from ai_cull_assistant.participant_review import body_participant_labels
    monkeypatch.setattr(subject,'detect',lambda *a,**k:[])
    monkeypatch.setattr(subject,'_head_candidates',lambda *a:[])
    monkeypatch.setattr(ai_focus,'review_focus',lambda *a,**k:(_ for _ in ()).throw(AssertionError('No paid review during local rescan')))
    _mock_models(monkeypatch)
    source=tmp_path/'photos';source.mkdir()
    yy,xx=np.indices((768,1024))
    pixels=np.repeat((((xx//8+yy//8)%2)*255).astype(np.uint8)[:,:,None],3,axis=2)
    pixels[:,512:]=128
    Image.fromarray(pixels).save(source/'edited.png')
    Image.new('RGB',(1024,768),'gray').save(source/'untouched.png')
    original_hashes={path.name:sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}
    workspace=tmp_path/'work'
    first_options={'technical_screening':False,'analysis_mode':'portrait'}
    initial=start_job(source,workspace,first_options,CropSettings(),mode='scan').run(first_options,CropSettings(),Event(),None)
    edited,untouched=initial.assets
    edited.ai_focus_result={'status':'clear','reason':'old subject','source':'api'}
    untouched.ai_focus_result={'status':'clear','reason':'unchanged','source':'api'}
    untouched_evidence=deepcopy(untouched.clarity_evidence)
    original_settings=CropSettings()
    page=NS(assets=initial.assets,index=0,edits={},_body_dialog=None,store_current=lambda:None,render=lambda:None,
            _confidence_value=lambda:.8)
    page.global_settings=lambda:CropDialog.global_settings(page)
    page.current_entry=lambda:page.edits.setdefault(page.global_settings().key(edited),{})
    captured={}
    def show_editor(parent,asset,entry,on_apply):
        captured['apply']=on_apply
        return NS(winfo_exists=lambda:False)
    monkeypatch.setattr(body_subject_dialog,'BodySubjectDialog',show_editor)
    CropDialog.edit_body_subjects(page)
    canvas=NS(delete=lambda *a:None)
    pointer=NS(_image_rect=(0,0,1000,1000),_drag=None,selected=[],candidates=[],canvas=canvas,render=lambda:None)
    pointer._normalized=lambda x,y:BodySubjectDialog._normalized(pointer,x,y)
    for first,last in (((70,70),(430,930)),((570,70),(930,930))):
        BodySubjectDialog.pointer_down(pointer,NS(x=first[0],y=first[1]))
        BodySubjectDialog.pointer_up(pointer,NS(x=last[0],y=last[1]))
    BodySubjectDialog.apply(NS(selected=pointer.selected,on_apply=captured['apply'],destroy=lambda:None))
    settings=page.global_settings()
    assert len(settings.photos[settings.key(edited)]['selected_bodies'])==2
    options={'technical_screening':True,'analysis_mode':'portrait'}
    app=NS(_sync_selected_workspace=lambda:True,_active_input=str(source),_active_workspace=str(workspace),
           scan_result=initial,crop_settings=original_settings,_workspace_options=lambda:options,
           detection_confidence_var=NS(set=lambda value:None),_set_sheets_ready=lambda value:None,_log=lambda value:None)
    app._save_session=lambda:save_session(app.scan_result)
    work={}
    def run(mode, regroup=False):
        assert mode=='rescan' and regroup
        job=start_job(source,workspace,options,app.crop_settings,result=app.scan_result,mode=mode,regroup=regroup)
        work['indices']=job._data['work_indices']
        app.scan_result=job.run(options,app.crop_settings,Event(),None)
    app._start_processing=run
    App._save_crop_settings(app,settings)
    assert work['indices']==[0]
    changed,still=app.scan_result.assets
    assert changed.local_recommendation=='review' and not changed.auto_rejected
    rows=changed.clarity_evidence['subjects']
    assert len(rows)==2 and [row['state'] for row in rows]==['clear','uncertain']
    assert all(row['evidence_kind']=='body' and not row['eye_evidence'] for row in rows)
    assert changed.ai_focus_stale and changed.ai_focus_result['reason']=='old subject'
    assert not changed.ai_focus_dirty
    assert still.clarity_evidence==untouched_evidence and still.ai_focus_result['reason']=='unchanged'
    assert not still.ai_focus_stale
    labels=body_participant_labels(changed,settings.photos[settings.key(changed)],settings.photos[settings.key(changed)])
    assert '身体细节清晰' in labels[0] and '身体待复核' in labels[1]
    restored=load_session(workspace,source)
    assert restored.assets[0].clarity_evidence['subjects']==json.loads(json.dumps(changed.clarity_evidence['subjects']))
    assert original_hashes=={path.name:sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}


@pytest.mark.parametrize('face_kind',['selected_faces','manual_face'])
def test_public_screening_links_manual_face_to_manually_selected_body(tmp_path,monkeypatch,face_kind):
    from datetime import datetime
    from ai_cull_assistant import face_focus
    from ai_cull_assistant.models import PhotoAsset
    from ai_cull_assistant.subject import SubjectFeatures
    from ai_cull_assistant.screening import screen_assets
    _mock_models(monkeypatch)
    yy,xx=np.indices((900,800))
    pixels=np.repeat((((xx//8+yy//8)%2)*255).astype(np.uint8)[:,:,None],3,axis=2)
    path=tmp_path/'one_person.png';Image.fromarray(pixels).save(path)
    asset=PhotoAsset(path.stem,path,path,None,path,datetime.now(),'.png',preview_path=path)
    body_box=(.1,.05,.8,.9)
    face_box=(.34,.077,.32,.153)
    settings=CropSettings(photos={CropSettings().key(asset):{face_kind:([face_box] if face_kind=='selected_faces' else face_box),'selected_bodies':[body_box]}})
    points=((.40,.11),(.60,.11),(.50,.15),(.43,.20),(.57,.20))
    monkeypatch.setattr(face_focus,'detail_features_list',lambda *a,**k:[SubjectFeatures('','',None,face_box,landmarks=points)])
    monkeypatch.setattr(face_focus,'detail_features',lambda *a,**k:SubjectFeatures('','',None,face_box,landmarks=points))
    monkeypatch.setattr(face_focus,'detail_metrics',lambda *a,**k:{'state':'clear','landmarks_used':True,'reasons':['strong_landmark_aligned_detail']})
    screen_assets([asset],analysis_mode='portrait',crop_settings=settings)
    evidence=asset.clarity_evidence
    assert len(evidence['subjects'])==1
    assert evidence['subjects'][0]['evidence_kind']=='face'
    assert evidence['body_results'][0]['associated_face']==1
    assert evidence['body_results'][0]['association']=='pose_head_overlap'
    assert evidence['body_results'][0]['eye_evidence'] is False
    assert asset.local_recommendation=='review'
    assert evidence['primary_quality']['issues'][0]['reason']=='primary_selection_unconfirmed'


def test_landscape_subject_page_guard_precedes_old_cached_page(monkeypatch):
    from ai_cull_assistant import app as app_module
    notices=[]
    monkeypatch.setattr(app_module.messagebox,'showinfo',lambda *a,**k:notices.append(a))
    fake=NS(_commit_detection_confidence=lambda:None,updates=NS(busy=False),
            _sync_selected_workspace=lambda:True,scan_result=NS(assets=[NS(analysis_mode='landscape')]),
            _show_workspace_page=lambda name:(_ for _ in ()).throw(AssertionError('Must not show cached portrait page')))
    app_module.App._open_crop_settings(fake)
    assert notices and notices[0][0]=='风景模式'


def test_two_bodies_matching_one_face_do_not_silently_merge_people():
    face=(.25,.1,.15,.2)
    candidates=[body((.10,.05,.5,.9),(.23,.08,.19,.24)),
                body((.18,.04,.5,.9),(.24,.09,.19,.24))]
    evidence=analysis_modes._portrait_result(face_result([face]),bodies=candidates).focus_evidence
    assert all(item['associated_face'] is None for item in evidence['body_results'])
    assert all(item['association']=='reverse_conflict' for item in evidence['body_results'])
    assert all(item['state']=='uncertain' for item in evidence['body_results'])
    assert evidence['recommendation']=='review'
    assert len(evidence['subjects'])==3  # unresolved evidence slots, not asserted person count


def test_cached_pixel_face_boxes_still_associate_after_upgrade():
    face=ScreeningResult(False,'subject_not_obviously_blurred',True,source_size=(1000,1000),focus_evidence={
        'state':'clear','participants':[{'state':'clear','landmarks_used':True,'face_box':(250,100,150,200)},
                                       {'state':'clear','landmarks_used':True,'face_box':(700,100,150,200)}]})
    evidence=analysis_modes._portrait_result(face,bodies=[body((.1,.05,.5,.9),(.23,.08,.19,.24))]).focus_evidence
    assert len(evidence['subjects'])==2
    assert evidence['body_results'][0]['associated_face']==1
