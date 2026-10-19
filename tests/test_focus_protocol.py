from copy import deepcopy
from datetime import datetime
import json

import pytest
from PIL import Image

from ai_cull_assistant import ai_focus, focus_protocol as protocol
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.primary_quality import confirm_selection, CONFIRMATION_KEY


@pytest.fixture
def confirmed(tmp_path):
    path=tmp_path/'controlled.png'
    Image.new('RGB',(600,600),'gray').save(path)
    asset=PhotoAsset(path.stem,path,path,None,path,datetime.now(),'.png',preview_path=path)
    settings=CropSettings(photos={CropSettings().key(asset):{'selected_faces':[[.1,.1,.4,.4]]}})
    settings.photos[settings.key(asset)][CONFIRMATION_KEY]=confirm_selection(asset,settings)
    return asset,settings


def context(confirmed, available=('face','body')):
    asset,settings=confirmed
    scope=protocol.make_scope(asset,settings)
    return scope,protocol.make_context(scope,scope['subjects'][0]['id'],evidence_parts=available)


def answer(ctx, severity='clear'):
    value=protocol.response_template(ctx)
    for part in value['parts']:
        part.update(visibility='visible',severity=severity,evidence_sufficient=True,
                    reason='Controlled visible edge observation',location=part['part'])
    return value


def test_clear_and_severe_have_explicit_supported_parts(confirmed):
    scope,ctx=context(confirmed)
    clear=protocol.parse_response(json.dumps(answer(ctx)),ctx)
    assert protocol.assemble_result(scope,[clear])['recommendation']=='keep'
    severe=answer(ctx); severe['parts'][0]['severity']='severe'
    severe['parts'][1].update(severity='uncertain',evidence_sufficient=False,visibility='unknown')
    result=protocol.assemble_result(scope,[protocol.parse_response(json.dumps(severe),ctx)])
    assert result['status']=='blur' and result['severity']=='severe'
    assert result['participants'][0]['parts'][1]['severity']=='uncertain'
    assert protocol.validate_result(result)==result


@pytest.mark.parametrize('field', ['version','photo_identity','scope_digest','confirmation_digest','subject_id','context_digest'])
def test_response_identity_mismatch_rejected(confirmed,field):
    _,ctx=context(confirmed)
    value=answer(ctx); value[field]='wrong'
    with pytest.raises(protocol.FocusProtocolError):
        protocol.parse_response(json.dumps(value),ctx)


@pytest.mark.parametrize('change', ['missing','part','bool','extra','nan','duplicate'])
def test_strict_schema_never_accepts_unbound_severe_claim(confirmed,change):
    _,ctx=context(confirmed)
    value=answer(ctx,'severe')
    if change=='missing': value['parts'][0].pop('evidence_sufficient')
    elif change=='part': value['parts'][0]['part']='unselected_bystander'
    elif change=='bool': value['parts'][0]['evidence_sufficient']='true'
    elif change=='extra': value['parts'][0]['new']=True
    elif change=='nan': value['parts'][0]['evidence_sufficient']=float('nan')
    else: value['parts'].append(deepcopy(value['parts'][0]))
    with pytest.raises(protocol.FocusProtocolError):
        protocol.parse_response(json.dumps(value),ctx)


def test_face_images_cannot_support_body_clear_or_severe(confirmed):
    _,ctx=context(confirmed,('face',))
    for severity in ['clear','severe']:
        with pytest.raises(protocol.FocusProtocolError):
            protocol.parse_response(json.dumps(answer(ctx,severity)),ctx)


def test_stored_tampering_and_roi_version_invalidated(confirmed):
    asset,settings=confirmed
    scope,ctx=context(confirmed)
    result=protocol.assemble_result(scope,[protocol.parse_response(json.dumps(answer(ctx)),ctx)])
    assert protocol.current_result(result,asset,settings)
    forged=deepcopy(result); forged.update(status='blur',severity='severe',recommendation='reject')
    assert protocol.current_result(forged,asset,settings) is None
    settings.photos[settings.key(asset)]['selected_faces'][0][0]=.2
    assert protocol.current_result(result,asset,settings) is None


def test_old_blur_not_promoted_and_unconfirmed_never_calls_model(confirmed,tmp_path,monkeypatch):
    asset,settings=confirmed
    assert protocol.current_result({'status':'blur','reason':'ordinary blur'},asset,settings) is None
    settings.photos[settings.key(asset)].pop(CONFIRMATION_KEY)
    monkeypatch.setattr(ai_focus,'call_model',lambda *a:pytest.fail('unconfirmed API call'))
    with pytest.raises(protocol.FocusProtocolError):
        ai_focus.review_focus(asset,settings,{},tmp_path/'cache')


def test_native_protocol_cache_and_legacy_answer_stay_unknown(confirmed,tmp_path,monkeypatch):
    asset,settings=confirmed
    calls=[]
    monkeypatch.setattr(ai_focus,'detail_features',lambda *a:None)
    def model(profile,prompt,images):
        calls.append(prompt)
        return {'text':json.dumps({'photo_identity':asset.stem,'status':'blur','reason':'old ordinary blur'})}
    monkeypatch.setattr(ai_focus,'call_model',model)
    result=ai_focus.review_focus(asset,settings,{'key':'do-not-cache'},tmp_path/'cache')
    again=ai_focus.review_focus(asset,settings,{'key':'do-not-cache'},tmp_path/'cache')
    assert result==again and result['status']=='uncertain'
    assert len(calls)==1
    assert list((tmp_path/'cache'/protocol.PROMPT_VERSION).glob('*.json'))
    assert 'do-not-cache' not in ''.join(p.read_text('utf-8') for p in tmp_path.rglob('*.json'))


def test_manual_decision_overrides_severe_but_selection_star_is_retained(confirmed):
    from ai_cull_assistant.lightroom_results import effective_decision
    scope,ctx=context(confirmed)
    result=protocol.assemble_result(scope,[protocol.parse_response(json.dumps(answer(ctx,'severe')),ctx)])
    photo=dict(analysis_mode='portrait',focus_scope=scope,ai_focus_result=result,
               ai={'fingerprint':'selection','rating':5,'reason':'pose'},fingerprint='selection')
    # No fingerprint is fabricated for technical validity.
    photo['technical_fingerprint']='technical'
    photo['focus_fingerprint']='technical'
    decision=effective_decision(photo)
    assert decision['recommendation']=='reject' and decision['rating']==5
    photo['final']={'confirmed':True,'rating':4,'pick_status':1}
    decision=effective_decision(photo)
    assert decision['recommendation']=='keep' and decision['source']=='manual'


def test_landscape_scope_does_not_require_portrait_confirmation(confirmed):
    asset,settings=confirmed
    asset.analysis_mode='landscape'
    settings.photos.clear()
    scope=protocol.make_scope(asset,settings)
    assert scope['confirmation_digest'] is None
    assert scope['subjects']==[{'id':'scene:frame','kind':'scene'}]


def test_old_checkpoint_without_new_scope_field_loads(confirmed):
    from ai_cull_assistant.processing_job import _asset_from_dict, _asset_to_dict
    asset,_=confirmed
    data=_asset_to_dict(asset); data.pop('focus_scope')
    restored=_asset_from_dict(data)
    assert restored.focus_scope is None


def test_merged_face_body_request_contains_both_native_inputs(confirmed,tmp_path,monkeypatch):
    from ai_cull_assistant import primary_quality
    from quality_cases import confirmed_settings
    asset,settings=confirmed
    confirmed_settings(asset,settings,bodies=[[.05,.05,.8,.9]])
    confirmation=primary_quality.confirmed_selection(asset,settings)
    face_id=next(r['id'] for r in confirmation['regions'] if r['kind']=='face')
    body_id=next(r['id'] for r in confirmation['regions'] if r['kind']=='body')
    # Controlled current measurement relationship. This tests image binding,
    # not the quality accuracy of a synthetic person detector.
    asset.clarity_evidence=dict(measurement_confirmation_digest=confirmation['digest'],
        measurement_inputs=primary_quality.measurement_inputs(asset,settings,['face','body']),
        primary_quality=dict(version=primary_quality.POLICY_VERSION,confirmation_digest=confirmation['digest'],
            selection_origin='human',selection_digest=confirmation['digest'],enabled_parts=['face','body'],
            subjects=[dict(id=face_id,region_ids=[face_id,body_id],parts=[],original_evidence={},
                           original_body_evidence=[dict(box=[.05,.05,.8,.9],regions=[])])]))
    calls=[]
    monkeypatch.setattr(ai_focus,'detail_features',lambda *a:None)
    def model(_profile,prompt,images):
        value=json.loads(prompt.split('返回格式：')[1])
        calls.append([path.name for path in images])
        for part in value['parts']:
            part.update(visibility='visible',severity='clear',evidence_sufficient=True,
                        reason='Visible native edge evidence',location=part['part'])
        return {'text':json.dumps(value)}
    monkeypatch.setattr(ai_focus,'call_model',model)
    result=ai_focus.review_focus(asset,settings,{},tmp_path/'cache')
    assert result['status']=='clear' and len(result['participants'])==1
    assert len(calls)==1 and any(name.startswith('body_native_associated_') for name in calls[0])


def test_stale_policy_cannot_grant_body_invisibility(confirmed,tmp_path,monkeypatch):
    asset,settings=confirmed
    scope=protocol.make_scope(asset,settings)
    asset.clarity_evidence=dict(measurement_confirmation_digest='old',primary_quality=dict(
        version='confirmed-primary-quality-v1',confirmation_digest='old',subjects=[dict(
            id=scope['subjects'][0]['id'],parts=[dict(part='body',state='not_visible')],
            original_evidence=dict(body=dict(state='clear',reasons=['body_not_visible_headshot']))) ]))
    monkeypatch.setattr(ai_focus,'detail_features',lambda *a:None)
    def model(_profile,prompt,images):
        ranges=json.loads(prompt.split('请求范围：')[1].split('\n返回格式：')[0])
        assert ranges['not_visible_parts']==[]
        value=json.loads(prompt.split('返回格式：')[1])
        return {'text':json.dumps(value)}
    monkeypatch.setattr(ai_focus,'call_model',model)
    assert ai_focus.review_focus(asset,settings,{},tmp_path/'cache')['status']=='uncertain'
