"""Deterministic scope contracts, not natural-photo accuracy measurements."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace as NS

import numpy as np
import pytest
from PIL import Image

from ai_cull_assistant import primary_quality as q, subject, face_focus, focus_protocol
from ai_cull_assistant.body_subject_dialog import BodySubjectDialog
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.yunet import FaceDetection
from test_primary_quality import face, body, FACE, BODY, OTHER


def asset_at(tmp_path):
    p=tmp_path/'two people.png'
    Image.new('RGB',(600,600),'gray').save(p)
    return PhotoAsset(p.stem,p,p,None,p,datetime.now(),'.png',preview_path=p)


def settings_for(asset,entry):
    settings=CropSettings(photos={CropSettings().key(asset):deepcopy(entry)})
    settings.photos[settings.key(asset)][q.CONFIRMATION_KEY]=q.confirm_selection(asset,settings)
    return settings


def candidate(x):
    return FaceDetection((x,200,120,160),((x+30,240),(x+90,240),(x+60,275),(x+35,310),(x+85,310)),.95)


@pytest.mark.parametrize('reverse',[False,True])
def test_manual_rectangle_with_two_faces_never_picks_by_order_or_center(tmp_path,monkeypatch,reverse):
    a=asset_at(tmp_path);s=CropSettings()
    rows=[candidate(140),candidate(340)]
    if reverse: rows.reverse()
    monkeypatch.setattr(subject,'detect',lambda *args:rows)
    subject._manual_details.clear()
    for _ in range(2):  # actual re-detection and cached ambiguity agree
        result=subject._manual_detail(a,s,None,(.1,.1,.8,.8))
        assert result.face==(.1,.1,.8,.8)
        assert result.landmarks is None
        assert result.head_source=='manual_ambiguous'


def test_single_contained_face_remains_measurable(tmp_path,monkeypatch):
    a=asset_at(tmp_path)
    monkeypatch.setattr(subject,'detect',lambda *args:[candidate(140)])
    subject._manual_details.clear()
    result=subject._manual_detail(a,CropSettings(),None,(.1,.1,.8,.8))
    assert result.landmarks and result.head_source=='manual'


def test_selection_records_deduplicate_preserving_first_order_and_skip_hidden():
    entry={'selected_faces':[OTHER,{'box':FACE},OTHER,{'box':BODY,'hidden':True}],
           'selected_bodies':[{'box':BODY,'subjects_disabled':True}]}
    assert q.selected_boxes(entry,'face')==[tuple(OTHER),tuple(FACE)]
    assert q.explicit_body_parts(entry)==['face']
    assert q.selection_regions(entry)==sorted(q.selection_regions(entry),key=lambda row:row['id'])
    entry['selected_faces'].append([True,.1,.2,.2])
    with pytest.raises(ValueError):q.selected_boxes(entry,'face')


def test_manual_scope_contract_preserves_confirmation_and_expires_old_measurement(tmp_path):
    a=asset_at(tmp_path);s=settings_for(a,{'selected_faces':[FACE],'selected_bodies':[]})
    confirmed=q.confirmed_selection(a,s)
    inputs=q.measurement_inputs(a,s,['face'])
    assert inputs['manual_face_scope_contract']==q.MANUAL_FACE_SCOPE_VERSION
    assert q.manual_body_measurement_current(a,s,{'measurement_inputs':inputs})
    old=deepcopy(inputs);old.pop('manual_face_scope_contract')
    assert not q.manual_body_measurement_current(a,s,{'measurement_inputs':old})
    assert not q.manual_body_measurement_current(a,s,{})
    assert q.confirmed_selection(a,s)==confirmed
    # Human confirmation still allows a fresh request before local measurement.
    a.clarity_evidence={}
    scope=focus_protocol.make_scope(a,s)
    assert scope['manual_face_scope_contract']==q.MANUAL_FACE_SCOPE_VERSION
    assert scope['enabled_parts']==['face']
    old_scope=deepcopy(scope);old_scope.pop('manual_face_scope_contract')
    old_scope['digest']=focus_protocol._digest({k:v for k,v in old_scope.items() if k!='digest'})
    with pytest.raises(focus_protocol.FocusProtocolError):focus_protocol._validate_scope(old_scope)


def test_foreign_body_and_legacy_attached_body_cannot_reject_selected_person(tmp_path):
    a=asset_at(tmp_path);s=settings_for(a,{'selected_faces':[FACE],'selected_bodies':[BODY]})
    selected=body([]);selected.update(associated_face=1,association='pose_head_overlap')
    foreign=body(['torso','left_upper_arm']);foreign['box']=[.6,.05,.35,.9]
    f=face();f['body']=foreign
    evidence={'subjects':[f],'body_results':[selected,foreign],
              'measurement_inputs':q.measurement_inputs(a,s,['face','body']),
              'measurement_confirmation_digest':q.confirmed_selection(a,s)['digest']}
    assert q.evaluate_local_evidence(a,s,evidence)['recommendation']=='keep'
    evidence['body_results']=[foreign]
    result=q.evaluate_local_evidence(a,s,evidence)
    assert result['recommendation']=='review'
    assert all(part['state']!='severe_blur' for item in result['subjects'] for part in item['parts'])


def test_ambiguous_manual_scope_does_not_call_ai_or_image_cache(tmp_path,monkeypatch):
    from ai_cull_assistant import ai_focus, focus_image_cache
    a=asset_at(tmp_path);s=settings_for(a,{'selected_faces':[[.1,.1,.8,.8]],'selected_bodies':[]})
    a.clarity_evidence={};scope=focus_protocol.make_scope(a,s)
    monkeypatch.setattr(subject,'_manual_detail',lambda *args:NS(head_source='manual_ambiguous'))
    def forbidden(*args,**kwargs):raise AssertionError('Ambiguous person must not reach paid review or image cache')
    monkeypatch.setattr(ai_focus,'call_model',forbidden)
    monkeypatch.setattr(focus_image_cache,'cached_focus_images',forbidden)
    region=q.confirmed_selection(a,s)['regions'][0]
    value=ai_focus._review_primary_target(a,s,{},tmp_path/'cache',scope,region)
    assert all(p['severity']=='uncertain' and not p['evidence_sufficient'] for p in value['parts'])


def test_body_scope_toggle_is_view_only_and_default_face_only():
    calls=[];entry={'selected_faces':[FACE]};original=deepcopy(entry)
    enabled=[];var=NS(value=False);var.get=lambda:var.value
    fake=NS(_has_faces=True,_face_first=True,_body_scope_var=var,_drag=None,
            selected=[],entry=entry,canvas=NS(delete=lambda *a:None),
            _scope_buttons=[NS(configure=lambda **kw:enabled.append(kw['state']))],
            render=lambda:None,on_apply=calls.append)
    BodySubjectDialog.apply(fake)
    assert not calls
    var.value=True;BodySubjectDialog.toggle_body_scope(fake)
    assert not fake._face_first and enabled[-1]=='normal'
    var.value=False;BodySubjectDialog.toggle_body_scope(fake)
    assert fake._face_first and enabled[-1]=='disabled'
    assert entry==original and not calls and fake.selected==[]


def test_face_present_body_prepare_does_not_select_background_candidates():
    from concurrent.futures import Future
    image=Image.new('RGB',(100,100));f=Future();f.set_result((image,[BODY]))
    fake=NS(_closed=False,_future=f,_has_faces=True,entry={'selected_faces':[FACE]},
            selected=[],render=lambda:None)
    BodySubjectDialog._finish_prepare(fake)
    assert fake.selected==[] and fake.candidates==[BODY]
    image.close()

@pytest.mark.parametrize('state,expected',[('clear','keep'),('severe_blur','reject'),('uncertain','review')])
def test_selected_person_only_through_measurement_policy_and_export(tmp_path,monkeypatch,state,expected):
    from ai_cull_assistant import analysis_modes, lightroom_results
    a=asset_at(tmp_path);s=settings_for(a,{'selected_faces':[FACE],'selected_bodies':[]})
    base=subject.SubjectFeatures('','',None,tuple(OTHER),tuple(OTHER),candidate_count=2)
    monkeypatch.setattr(subject,'asset_features',lambda *args,**kwargs:base)
    monkeypatch.setattr(subject,'_manual_detail',lambda asset,settings,base,box:
        subject.SubjectFeatures('','',None,tuple(box),tuple(box),((.15,.2),(.30,.2),(.23,.3),(.18,.4),(.28,.4)), 'manual'))
    seen=[]
    def measure(image,inputs,**kwargs):
        seen.extend(box for box,_ in inputs)
        return [dict(face(state,box=list(box),participant=i,body=False),
                     face_box=[box[0]*image.width,box[1]*image.height,box[2]*image.width,box[3]*image.height])
                for i,(box,_) in enumerate(inputs,1)]
    monkeypatch.setattr(face_focus,'_measure_participants',measure)
    # Pretend old saved evidence concerns a blurred, unrelated background face.
    a.clarity_evidence={'subjects':[face('severe_blur',box=OTHER)],'recommendation':'reject'}
    result=analysis_modes.assess_asset_mode(a,crop_settings=s,body_check=True)
    assert seen==[tuple(FACE)]
    assert result.focus_evidence['primary_quality']['recommendation']==expected
    a.clarity_evidence=result.focus_evidence;a.focus_scope=focus_protocol.make_scope(a,s)
    assert lightroom_results.local_recommendation(a)==expected
    # Editing the face cannot retain old person identity or verdict.
    s.photos[s.key(a)]['selected_faces']=[OTHER]
    assert q.current_selection(a,s) is None
    assert q.evaluate_local_evidence(a,s,a.clarity_evidence)['recommendation']=='review'


def test_opt_in_body_editor_application_changes_draft_only_and_empty_is_explicit(tmp_path,monkeypatch):
    from ai_cull_assistant.crop_dialog import CropDialog
    from ai_cull_assistant import body_subject_dialog
    a=asset_at(tmp_path);a.subject_features=subject.SubjectFeatures('','',None,tuple(FACE))
    entry={'selected_faces':[FACE]};saved=deepcopy(entry)
    page=NS(assets=[a],index=0,_body_dialog=None,edits={'photo':entry},
            store_current=lambda:None,render=lambda:None,
            global_settings=lambda:NS(key=lambda asset:'photo'),current_entry=lambda:entry)
    calls={}
    def dialog(parent,asset,draft,on_apply):
        calls['apply']=on_apply
        return NS(winfo_exists=lambda:False)
    monkeypatch.setattr(body_subject_dialog,'BodySubjectDialog',dialog)
    CropDialog.edit_body_subjects(page)
    assert entry==saved and 'selected_bodies' not in entry  # opening/cancelling no mutation
    calls['apply']([])
    assert entry['selected_bodies']==[] and saved=={'selected_faces':[FACE]}
    calls['apply'](None)
    assert 'selected_bodies' not in entry and entry['selected_faces']==[FACE]

@pytest.mark.parametrize('record',[{'box':FACE,'hidden':True},{'box':FACE,'subjects_disabled':True},{'box':[True,.1,.2,.2]}])
def test_removed_or_invalid_face_does_not_revive_auto_body_without_explicit_scope(record):
    entry={'selected_faces':[record]}
    assert q.manual_faces_disable_body_fallback(entry)
    if record['box']==FACE:
        assert not q.manual_faces_disable_body_fallback(dict(entry,roi_selection_schema=2))
    else:
        assert q.manual_faces_disable_body_fallback(dict(entry,roi_selection_schema=2))


def test_auto_face_is_not_promoted_when_toggling_optional_body_scope():
    var=NS(value=True);var.get=lambda:var.value;var.set=lambda value:setattr(var,'value',value)
    fake=NS(_has_faces=True,_has_manual_faces=False,_face_first=True,
            _body_scope_var=var,_drag=None,canvas=NS(delete=lambda *a:None),
            _scope_buttons=[],render=lambda:None)
    BodySubjectDialog.toggle_body_scope(fake)
    assert fake._face_first and var.value is False


def test_canonical_editor_face_reads_preserve_order_without_mutating_draft():
    from ai_cull_assistant.crop_dialog import _selected_face_boxes
    entry={'selected_faces':[OTHER,{'box':FACE},OTHER,{'box':BODY,'hidden':True}]}
    old=deepcopy(entry)
    assert _selected_face_boxes(entry)==[tuple(OTHER),tuple(FACE)]
    assert entry==old and _selected_face_boxes({}) is None


def test_migrated_scope_is_scheduled_for_rescan_without_changing_confirmation(tmp_path):
    a=asset_at(tmp_path);s=settings_for(a,{'selected_faces':[FACE],'selected_bodies':[]})
    confirmation=q.confirmed_selection(a,s)
    a.clarity_evidence={'measurement_inputs':q.measurement_inputs(a,s,['face'])}
    assert not q.manual_scope_remeasurement_required(a,s)
    a.clarity_evidence['measurement_inputs'].pop('manual_face_scope_contract')
    assert q.manual_scope_remeasurement_required(a,s)
    assert q.confirmed_selection(a,s)==confirmation
    a.analysis_mode='landscape'
    assert not q.manual_scope_remeasurement_required(a,s)

@pytest.mark.parametrize('entry',[{'manual_face':{'box':None}}, {'manual_face':{'box':FACE,'hidden':True}}])
def test_legacy_removed_face_does_not_revive_body(entry):
    assert q.manual_faces_disable_body_fallback(entry)


def test_unchanged_save_and_direct_rescan_remeasure_migrated_scope(tmp_path):
    from ai_cull_assistant.app import App
    from ai_cull_assistant.processing_job import start_job
    from ai_cull_assistant.workflow import ScanResult
    a=asset_at(tmp_path);s=settings_for(a,{'selected_faces':[FACE],'selected_bodies':[]})
    a.clarity_evidence={'measurement_inputs':q.measurement_inputs(a,s,['face'])}
    a.clarity_evidence['measurement_inputs'].pop('manual_face_scope_contract')
    work=tmp_path/'work';work.mkdir()
    scan=ScanResult([a],work/'previews',work/'sheets',work,work/'groups.json',input_dir=tmp_path)
    options={'technical_screening':True,'analysis_mode':'portrait'}
    page=NS(_sync_selected_workspace=lambda:True,_active_input=str(tmp_path),_active_workspace=str(work),
            scan_result=scan,crop_settings=s,_workspace_options=lambda:options,
            detection_confidence_var=NS(set=lambda value:None),_set_sheets_ready=lambda value:None,
            _log=lambda value:None,_save_session=lambda:None)
    jobs=[]
    def start(mode,regroup=False):
        assert mode=='rescan' and regroup
        jobs.append(start_job(tmp_path,work,options,page.crop_settings,result=scan,mode=mode,regroup=regroup))
    page._start_processing=start
    App._save_crop_settings(page,deepcopy(s))
    assert a.ai_focus_dirty and jobs[0]._data['work_indices']==[0]
    a.ai_focus_dirty=False
    direct=start_job(tmp_path,tmp_path/'direct-rescan-work',options,s,result=scan,mode='rescan')
    assert a.ai_focus_dirty and direct._data['work_indices']==[0]
