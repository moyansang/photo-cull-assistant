"""Local-only automatic selection regression coverage; no detector downloads or AI."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from ai_cull_assistant import analysis_modes, face_focus, primary_quality as quality
from ai_cull_assistant.ai_project import ReviewProject, photo_id
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.lightroom_results import effective_decision, local_recommendation
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.screening import ScreeningResult, screen_assets
from test_primary_quality import FACE, OTHER, BODY, face, confirm, body


@pytest.fixture
def automatic(tmp_path):
    path = tmp_path / 'portrait.jpg'
    Image.new('RGB', (600, 600), 'gray').save(path)
    asset = PhotoAsset('portrait', path, path, None, path, datetime.now(), '.jpg',
                       preview_path=path)
    return asset, CropSettings()


def detector_evidence(settings, state='clear', **proof_changes):
    proof = dict(version='single-detected-face-v1', candidate_count=1,
                 detection_threshold=settings.detection_confidence, box=FACE,
                 landmarks_available=True)
    proof.update(proof_changes)
    return dict(subjects=[face(state, body=False)], automatic_subject=proof)


def scan(monkeypatch, asset, settings, evidence=None, *, body_check=False, during=None):
    evidence = detector_evidence(settings) if evidence is None else deepcopy(evidence)
    def measure(*args, **kwargs):
        if during:
            during()
        return ScreeningResult(False, 'controlled_local_measurement', True,
                               focus_evidence=evidence, source_size=(600,600))
    monkeypatch.setattr(analysis_modes, '_assess_asset_mode', measure)
    result = analysis_modes.assess_asset_mode(asset, crop_settings=settings, body_check=body_check)
    asset.clarity_evidence = result.focus_evidence
    return result


@pytest.mark.parametrize('state,expected', [('clear','keep'), ('severe_blur','reject'), ('uncertain','review')])
def test_unique_supported_face_local_decisions_without_human_confirmation(automatic,monkeypatch,state,expected):
    asset,settings=automatic
    result=scan(monkeypatch,asset,settings,detector_evidence(settings,state))
    evidence=result.focus_evidence
    selection=quality.current_selection(asset,settings)
    assert selection['origin']=='automatic'
    assert selection['human_confirmed'] is False
    assert quality.confirmed_selection(asset,settings) is None
    assert settings.photos=={}
    assert evidence['primary_quality']['confirmation_digest'] is None
    assert evidence['primary_quality']['selection_digest']==selection['digest']
    assert evidence['primary_quality']['recommendation']==expected
    assert result.rejected is (expected=='reject')
    assert [part['part'] for part in evidence['primary_quality']['subjects'][0]['parts']]==['face']
    assert 'body' not in evidence['subjects'][0]


@pytest.mark.parametrize('proof_change', [dict(candidate_count=2),dict(candidate_count=None),
    dict(candidate_count=True),dict(candidate_count='1'),dict(candidate_count=0),
    dict(landmarks_available=False),dict(detection_threshold=.123),dict(version='legacy')])
def test_ambiguous_unknown_or_unproven_detector_never_auto_selects(automatic,monkeypatch,proof_change):
    asset,settings=automatic
    result=scan(monkeypatch,asset,settings,detector_evidence(settings,'severe_blur',**proof_change))
    assert result.focus_evidence['recommendation']=='review'
    assert quality.current_selection(asset,settings) is None
    assert result.rejected is False


@pytest.mark.parametrize('change', ['missing_proof','extra_face','box_mismatch','selection_pending'])
def test_background_people_and_unbound_faces_remain_review(automatic,monkeypatch,change):
    asset,settings=automatic
    evidence=detector_evidence(settings,'severe_blur')
    if change=='missing_proof': evidence.pop('automatic_subject')
    elif change=='extra_face': evidence['subjects'].append(face('severe_blur',OTHER,2,False))
    elif change=='box_mismatch': evidence['automatic_subject']['box']=OTHER
    else: evidence['selection_pending']=True
    assert scan(monkeypatch,asset,settings,evidence).focus_evidence['recommendation']=='review'


@pytest.mark.parametrize('change', ['landmarks','native_size','nan','inf','missing_metrics','unsupported_state'])
def test_corrupt_or_unsupported_measurements_never_reject(automatic,monkeypatch,change):
    asset,settings=automatic
    evidence=detector_evidence(settings,'severe_blur'); item=evidence['subjects'][0]
    if change=='landmarks': item['landmarks_used']=False
    elif change=='native_size': item['native_face_width']=30
    elif change in ('nan','inf'): item['regions']['face_core']['fine']['contrast']=float(change)
    elif change=='missing_metrics': item.pop('regions')
    else: item['face_measurement_state']='clear'
    assert scan(monkeypatch,asset,settings,evidence).focus_evidence['recommendation']=='review'


@pytest.mark.parametrize('change', ['source','roi','global_crop','photo_crop','threshold','enabled_parts'])
def test_measurement_binding_invalidates_automatic_selection(automatic,monkeypatch,change):
    asset,settings=automatic
    scan(monkeypatch,asset,settings)
    assert quality.current_selection(asset,settings)
    if change=='source': asset.primary_path.write_bytes(b'changed source')
    elif change=='roi': settings.photos[settings.key(asset)]={'selected_faces':[OTHER]}
    elif change=='global_crop': settings=replace(settings,scale_factor=settings.scale_factor+.1)
    elif change=='photo_crop': settings.photos[settings.key(asset)]={'face_crops':{'new':{'scale_factor':1.2}}}
    elif change=='threshold': settings=replace(settings,detection_confidence=.5)
    else: asset.clarity_evidence['measurement_inputs']['enabled_parts']=['face','body']
    assert quality.current_selection(asset,settings) is None
    assert quality.evaluate_local_evidence(asset,settings,asset.clarity_evidence)['recommendation']=='review'


@pytest.mark.parametrize('change', ['source','roi','crop'])
def test_inputs_changed_during_measurement_are_not_stamped(automatic,monkeypatch,change):
    asset,settings=automatic
    def mutate():
        if change=='source': asset.primary_path.write_bytes(b'changed while measuring')
        elif change=='roi': settings.photos[settings.key(asset)]={'selected_faces':[OTHER]}
        else: settings.photos[settings.key(asset)]={'scale_factor':1.8}
    result=scan(monkeypatch,asset,settings,during=mutate)
    assert 'automatic_selection' not in result.focus_evidence
    assert 'measurement_selection_digest' not in result.focus_evidence
    assert result.focus_evidence['recommendation']=='review'


@pytest.mark.parametrize('entry', [{'selected_faces':[FACE]}, {'manual_face':FACE},
    {'selected_faces':[]}, {'selected_bodies':[]}, {'hidden':True}, {'subjects_disabled':True}])
def test_manual_or_disabled_selection_cannot_silently_become_automatic(automatic,monkeypatch,entry):
    asset,settings=automatic
    settings.photos[settings.key(asset)]=deepcopy(entry)
    result=scan(monkeypatch,asset,settings)
    assert result.focus_evidence['recommendation']=='review'
    assert quality.current_selection(asset,settings) is None


def test_confirmed_manual_selection_and_edited_confirmation_stay_distinct(automatic,monkeypatch):
    asset,settings=automatic
    settings.photos[settings.key(asset)]={'selected_faces':[FACE]}
    confirm(asset,settings)
    result=scan(monkeypatch,asset,settings)
    assert result.focus_evidence['recommendation']=='keep'
    assert quality.current_selection(asset,settings)['human_confirmed'] is True
    assert 'automatic_selection' not in result.focus_evidence
    settings.photos[settings.key(asset)]['selected_faces']=[OTHER]
    assert scan(monkeypatch,asset,settings).focus_evidence['recommendation']=='review'
    assert quality.current_selection(asset,settings) is None


def test_requested_body_missing_is_review_but_disabled_body_is_not_invented(automatic,monkeypatch):
    asset,settings=automatic
    assert scan(monkeypatch,asset,settings).focus_evidence['recommendation']=='keep'
    result=scan(monkeypatch,asset,settings,body_check=True)
    policy=result.focus_evidence['primary_quality']
    assert policy['recommendation']=='review'
    assert policy['enabled_parts']==['face','body']
    assert policy['subjects'][0]['parts'][1]['state']=='unassessed'


def test_explicit_body_roi_requests_body_even_with_checkbox_off(automatic,monkeypatch):
    asset,settings=automatic
    settings.photos[settings.key(asset)]={'selected_faces':[FACE],'selected_bodies':[BODY]}
    confirm(asset,settings)
    evidence=detector_evidence(settings)
    evidence['body_results']=[body(['torso','left_upper_arm'])]
    result=scan(monkeypatch,asset,settings,evidence,body_check=False)
    assert result.focus_evidence['enabled_parts']==['face','body']
    assert result.focus_evidence['recommendation']=='reject'
    assert len(result.focus_evidence['primary_quality']['subjects'])==1


@pytest.mark.parametrize('state,expected', [('clear','keep'),('severe_blur','reject'),('uncertain','review')])
def test_screen_refresh_json_reload_and_effective_decision(automatic,monkeypatch,tmp_path,state,expected):
    asset,settings=automatic
    evidence=detector_evidence(settings,state)
    monkeypatch.setattr(analysis_modes,'_assess_asset_mode',lambda *a,**k:
        ScreeningResult(False,'controlled',True,focus_evidence=deepcopy(evidence),source_size=(600,600)))
    screen_assets([asset],crop_settings=settings,analysis_mode='portrait',body_check=False)
    project=ReviewProject(tmp_path/'workspace')
    project.refresh([asset],settings)
    row=project.data['photos'][photo_id(asset)]
    for value in (asset,row,json.loads(json.dumps(row))):
        assert local_recommendation(value)==expected
        decision=effective_decision(value)
        assert decision['recommendation']==expected
        assert decision['rating'] is None
        assert decision['pick_status']=={'keep':1,'reject':-1,'review':None}[expected]
    reloaded=ReviewProject(tmp_path/'workspace')
    assert local_recommendation(reloaded.data['photos'][photo_id(asset)])==expected
    row['final']={'confirmed':True,'rating':5,'pick_status':1}
    assert effective_decision(row)['source']=='manual'
    assert effective_decision(row)['rating']==5


def test_old_json_workspace_is_review_until_fresh_rescan(automatic,monkeypatch,tmp_path):
    asset,settings=automatic
    asset.clarity_evidence=json.loads(json.dumps({'state':'clear','recommendation':'keep','subjects':[face()]}))
    asset.screening_reason='subject_not_obviously_blurred'
    project=ReviewProject(tmp_path/'workspace')
    project.refresh([asset],settings)
    assert local_recommendation(project.data['photos'][photo_id(asset)])=='review'
    scan(monkeypatch,asset,settings)
    project.refresh([asset],settings)
    assert local_recommendation(project.data['photos'][photo_id(asset)])=='keep'


@pytest.mark.parametrize('count,landmarks,expected', [(1,True,'keep'),(2,True,'review'),(None,True,'review'),(1,False,'review')])
def test_actual_face_focus_propagates_detector_candidate_proof(automatic,monkeypatch,count,landmarks,expected):
    asset,settings=automatic
    points=((.18,.2),(.35,.2),(.27,.3),(.2,.4),(.33,.4)) if landmarks else None
    detected=SimpleNamespace(face=tuple(FACE),landmarks=points,candidate_count=count)
    monkeypatch.setattr(face_focus,'detail_features',lambda *a,**k:detected)
    item=face(body=False)
    item.update(face_box=(60,60,210,240),landmarks_used=landmarks)
    monkeypatch.setattr(face_focus,'_measure_participants',lambda *a:[deepcopy(item)])
    # Missing landmarks may start a body fallback; keep this detector fixture local.
    from ai_cull_assistant import subject
    monkeypatch.setattr(subject,'body_candidates',lambda *a:[])
    result=analysis_modes.assess_asset_mode(asset,crop_settings=settings,body_check=False)
    proof=result.focus_evidence['automatic_subject']
    assert proof['candidate_count']==count
    assert proof['landmarks_available'] is landmarks
    assert result.focus_evidence['recommendation']==expected


@pytest.mark.parametrize('change', ['missing_inputs','missing_stamp','wrong_stamp','human_flag','candidate_count'])
def test_serialized_automatic_token_cannot_be_forged_or_detached(automatic,monkeypatch,change):
    asset,settings=automatic
    scan(monkeypatch,asset,settings)
    asset.clarity_evidence=json.loads(json.dumps(asset.clarity_evidence))
    assert quality.current_selection(asset,settings)['human_confirmed'] is False
    evidence=asset.clarity_evidence
    if change=='missing_inputs': evidence.pop('measurement_inputs')
    elif change=='missing_stamp': evidence.pop('measurement_selection_digest')
    elif change=='wrong_stamp': evidence['measurement_selection_digest']='stale'
    elif change=='human_flag': evidence['automatic_selection']['human_confirmed']=True
    else: evidence['automatic_subject']['candidate_count']=2
    assert quality.current_selection(asset,settings) is None
    assert quality.evaluate_local_evidence(asset,settings,evidence)['recommendation']=='review'
    assert quality.confirmed_selection(asset,settings) is None


def test_source_edit_invalidates_downstream_without_reusing_old_keep(automatic,monkeypatch,tmp_path):
    asset,settings=automatic
    scan(monkeypatch,asset,settings)
    project=ReviewProject(tmp_path/'workspace')
    project.refresh([asset],settings)
    assert local_recommendation(asset)=='keep'
    asset.primary_path.write_bytes(b'new source; old measurements are obsolete')
    assert local_recommendation(asset)=='review'
    project.refresh([asset],settings)
    row=project.data['photos'][photo_id(asset)]
    assert local_recommendation(row)=='review'
    assert effective_decision(row)['pick_status'] is None
