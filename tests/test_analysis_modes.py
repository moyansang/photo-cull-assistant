from datetime import datetime
from types import SimpleNamespace
import json

import numpy as np
import pytest
from PIL import Image

from ai_cull_assistant import analysis_modes as modes, body_focus, subject
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.screening import ScreeningResult, screen_assets


def asset_at(tmp_path):
    path = tmp_path/'image.png'
    Image.new('RGB',(512,512),'gray').save(path)
    return PhotoAsset(path.stem,path,path,None,path,datetime.now(),'.png',preview_path=path)


def test_manual_modes_reject_auto_and_per_photo_classification():
    assert modes.normalize_mode(None) == 'portrait'
    assert modes.normalize_mode('landscape') == 'landscape'
    with pytest.raises(ValueError):
        modes.normalize_mode('auto')


def test_landscape_sky_water_and_blurred_background_never_auto_reject():
    blank = Image.new('RGB',(512,512),(120,150,200))
    evidence = modes.landscape_evidence(blank)
    assert evidence['recommendation']=='review'
    assert evidence['visible_boxes']==[] and evidence['subjects']==[]
    assert evidence['reliability']=='insufficient'
    yy,xx = np.indices((512,512))
    pixels = np.full((512,512,3),128,np.uint8)
    board = ((xx//4+yy//4)%2*255).astype(np.uint8)
    pixels[256:,:] = board[256:,:,None]
    result = modes.landscape_evidence(Image.fromarray(pixels))
    assert result['recommendation']=='keep'
    assert result['visible_boxes']==[]
    json.dumps(result)


def test_no_face_no_body_is_review_not_reject(tmp_path, monkeypatch):
    a = asset_at(tmp_path)
    monkeypatch.setattr('ai_cull_assistant.face_focus.assess_asset_focus',lambda *a,**k:ScreeningResult(False,'no_reliable_face',False))
    monkeypatch.setattr(subject,'body_candidates',lambda *a:[])
    result=modes.assess_asset_mode(a)
    assert not result.rejected
    assert result.focus_evidence['recommendation']=='review'
    assert result.focus_evidence['reliability']=='insufficient'


def test_body_only_clarity_cannot_become_eye_evidence(tmp_path, monkeypatch):
    a = asset_at(tmp_path)
    box = (.2,.1,.5,.8)
    body = dict(box=box,source='mediapipe_pose',confidence=.9,state='clear',reasons=['multiple_major_body_regions_clear'],eye_evidence=False)
    monkeypatch.setattr('ai_cull_assistant.face_focus.assess_asset_focus',lambda *a,**k:ScreeningResult(False,'no_reliable_face',False))
    monkeypatch.setattr(subject,'body_candidates',lambda *a:[body])
    monkeypatch.setattr(body_focus,'detect_body_candidates',lambda *a:[body])
    result=modes.assess_asset_mode(a)
    assert result.focus_evidence['recommendation']=='keep'
    assert result.focus_evidence['eyes_unassessed'] is True
    assert result.focus_evidence['eye_evidence'] is False
    assert not result.face_found
    assert result.focus_evidence['visible_boxes']==[{'kind':'body','box':list(box)}]


def test_multiple_people_do_not_average_away_one_blur(tmp_path, monkeypatch):
    a = asset_at(tmp_path)
    evidence = {'state':'severe_blur','participants':[{'state':'clear','landmarks_used':True},{'state':'severe_blur','landmarks_used':True}]}
    monkeypatch.setattr('ai_cull_assistant.face_focus.assess_asset_focus',lambda *a,**k:ScreeningResult(True,'obvious_subject_blur',True,focus_evidence=evidence))
    result=modes.assess_asset_mode(a)
    assert not result.rejected
    assert result.focus_evidence['recommendation']=='review'
    assert result.focus_evidence['reliability']=='adequate'
    assert [p['state'] for p in result.focus_evidence['subjects']]==['clear','severe_blur']


def test_uncertain_eyes_remain_uncertain_even_when_body_is_clear(tmp_path, monkeypatch):
    a = asset_at(tmp_path)
    evidence = {'state':'uncertain','landmarks_used':True,'reasons':['borderline_focus_evidence']}
    monkeypatch.setattr('ai_cull_assistant.face_focus.assess_asset_focus',lambda *a,**k:ScreeningResult(False,'face_focus_uncertain',True,focus_evidence=evidence))
    monkeypatch.setattr(subject,'body_candidates',lambda *a:pytest.fail('reliable face takes priority'))
    result=modes.assess_asset_mode(a)
    assert result.focus_evidence['recommendation']=='review'
    assert result.focus_evidence['reliability']=='adequate'


def test_manual_multiple_body_selection_preserves_each_result(tmp_path, monkeypatch):
    a = asset_at(tmp_path)
    boxes = [[.1,.1,.3,.8],[.6,.1,.3,.8]]
    settings=CropSettings(photos={CropSettings().key(a):{'selected_bodies':boxes}})
    monkeypatch.setattr('ai_cull_assistant.face_focus.assess_asset_focus',lambda *a,**k:pytest.fail('body-only selection must not choose background face'))
    monkeypatch.setattr(body_focus,'assess_body_region',lambda image,box:dict(box=box,source='manual',confidence=None,state='clear' if box[0]<.5 else 'severe_blur',reasons=[]))
    result=modes.assess_asset_mode(a,crop_settings=settings)
    assert not result.rejected
    assert result.focus_evidence['recommendation']=='review'
    assert len(result.focus_evidence['subjects'])==2
    assert not result.focus_evidence['eye_evidence']


def test_empty_selection_does_not_restore_ignored_candidates(tmp_path):
    a=asset_at(tmp_path)
    settings=CropSettings(photos={CropSettings().key(a):{'selected_bodies':[]}})
    assert subject.body_candidates(a,settings)==[]
    assert modes.assess_asset_mode(a,crop_settings=settings).focus_evidence['recommendation']=='review'


def test_automatic_multiple_bodies_ask_for_subject_selection(tmp_path,monkeypatch):
    a=asset_at(tmp_path)
    bodies=[dict(box=box,state='clear',source='mediapipe_pose',confidence=.9,reasons=[]) for box in ((.1,.1,.3,.8),(.6,.1,.3,.8))]
    monkeypatch.setattr('ai_cull_assistant.face_focus.assess_asset_focus',lambda *a,**k:ScreeningResult(False,'no_reliable_face',False))
    monkeypatch.setattr(subject,'body_candidates',lambda *a:bodies)
    monkeypatch.setattr(body_focus,'detect_body_candidates',lambda *a:bodies)
    result=modes.assess_asset_mode(a)
    assert result.focus_evidence['selection_pending']
    assert result.focus_evidence['recommendation']=='review'
    assert a.person_review_pending


def test_group_recommendations_allow_ties_without_stars_or_rejecting_others(tmp_path):
    assets=[SimpleNamespace(primary_path=tmp_path/str(i),clarity_evidence=dict(recommendation=state,reliability='adequate')) for i,state in enumerate(('keep','keep','review'))]
    results=modes.recommend_group(assets)
    assert [r['recommended'] for r in results.values()]==[True,True,False]
    assert [r['tied'] for r in results.values()]==[True,True,False]
    assert results[str(tmp_path/'2')]['recommendation']=='review'
    assert all('rating' not in item for item in results.values())


def test_screen_assets_landscape_does_not_detect_faces(tmp_path,monkeypatch):
    a=asset_at(tmp_path)
    monkeypatch.setattr('ai_cull_assistant.face_focus.assess_asset_focus',lambda *a,**k:pytest.fail('landscape does not detect people'))
    result=screen_assets([a],analysis_mode='landscape')[a.stem]
    assert a.analysis_mode=='landscape'
    assert a.local_recommendation=='review'
    assert result.focus_evidence['visible_boxes']==[]


def test_supported_pose_produces_body_extent_not_head_rectangle(monkeypatch):
    person=np.array([80,40,130,100,110,250,110,20,110,100,110,40,.9],dtype=np.float32)
    points=np.zeros((33,5),dtype=np.float32)
    for index,xy in {11:(80,100),12:(160,100),13:(55,160),14:(185,160),23:(90,205),24:(150,205),25:(85,260),26:(155,260),27:(80,310),28:(160,310)}.items():
        points[index,:2]=xy; points[index,3:]=.99
    pose={'landmarks':points,'mask':np.full((320,240),255,np.uint8),'confidence':.99}
    monkeypatch.setattr(body_focus,'_get_models',lambda:(SimpleNamespace(infer=lambda image:np.stack([person])),SimpleNamespace(infer=lambda *a:pose)))
    result=body_focus.detect_body_candidates(Image.new('RGB',(240,320),'gray'))
    assert len(result)==1
    assert result[0]['box'][3]>.8
    assert result[0]['eye_evidence'] is False
    assert result[0]['state']=='uncertain'  # plain clothing is not blur evidence
    json.dumps(result)


def test_person_detector_rectangle_without_pose_is_not_a_body_box(monkeypatch):
    person=np.array([80,40,130,100,110,250,110,20,110,100,110,40,.9],dtype=np.float32)
    monkeypatch.setattr(body_focus,'_get_models',lambda:(SimpleNamespace(infer=lambda image:np.stack([person])),SimpleNamespace(infer=lambda *a:None)))
    assert body_focus.detect_body_candidates(Image.new('RGB',(240,320),'gray'))==[]


def test_group_helper_downgrades_unsupported_quality_recommendations(tmp_path):
    assets=[SimpleNamespace(primary_path=tmp_path/str(i),clarity_evidence=evidence) for i,evidence in enumerate((
        {'recommendation':'keep','reliability':'insufficient'}, {'recommendation':'reject'},
        {'recommendation':'reject','reliability':'adequate'}))]
    results=list(modes.recommend_group(assets).values())
    assert [result['recommendation'] for result in results]==['review','review','reject']
    assert all(not result['recommended'] for result in results)


def test_raw_recheck_uses_missing_detail_not_borderline_quality(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from ai_cull_assistant import face_focus
    a=asset_at(tmp_path)
    a.raw_path=tmp_path/'image.nef'
    a.raw_path.write_bytes(b'not decoded by mock')
    monkeypatch.setattr(face_focus,'detail_features',lambda *a,**k:SimpleNamespace(face=(.1,.1,.8,.8),landmarks=((.2,.3),(.7,.3),(.5,.5),(.3,.7),(.7,.7))))
    requests=[]
    @contextmanager
    def image(asset,**options):
        requests.append(options)
        with Image.new('RGB',(512,512),'gray') as value:
            value.info['decode_evidence']={'source_kind':'raw_full_decode' if options.get('insufficient_evidence') else 'jpeg_full','evidence_sufficient':True}
            yield value
    monkeypatch.setattr(face_focus,'full_image',image)
    evidence=iter(({'state':'uncertain','reasons':['high_frequency_noise_or_artifacts']}, {'state':'clear','reasons':['strong_landmark_aligned_detail']}))
    monkeypatch.setattr(face_focus,'detail_metrics',lambda *a,**k:next(evidence))
    result=face_focus.assess_asset_focus(a)
    assert len(requests)==2 and requests[1]['insufficient_evidence']
    assert result.focus_evidence['state']=='clear'
    requests.clear()
    monkeypatch.setattr(face_focus,'detail_metrics',lambda *a,**k:{'state':'uncertain','reasons':['borderline_focus_evidence']})
    assert face_focus.assess_asset_focus(a).focus_evidence['state']=='uncertain'
    assert len(requests)==1
