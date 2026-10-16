import json
from datetime import datetime
from PIL import Image
import pytest
from ai_cull_assistant import ai_focus
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.ai_api import ApiError
from quality_cases import confirmed_settings, protocol_reply

PROFILE={'id':'test','name':'Test','base_url':'https://example.test/v1','model':'test'}


def photo(tmp_path,mode):
    path=tmp_path/'source.jpg';Image.new('RGB',(1400,1000),'white').save(path)
    return PhotoAsset('source',path,path,None,path,datetime.now(),'.jpg',preview_path=path,analysis_mode=mode)


def test_landscape_review_has_full_overview_distributed_native_details_no_faces(tmp_path,monkeypatch):
    asset=photo(tmp_path,'landscape')
    monkeypatch.setattr(ai_focus,'detail_features',lambda *args:pytest.fail('no portrait detection in landscape'))
    captured={}
    def call(profile,prompt,images):
        captured.update(prompt=prompt,images=images)
        return {'text':json.dumps({'photo_identity':'source','status':'clear','reason':'rock edges clear'})}
    monkeypatch.setattr(ai_focus,'call_model',call)
    result=ai_focus.review_focus(asset,CropSettings(),PROFILE,tmp_path/'cache')
    assert result['status']=='clear' and result['technical_fingerprint']
    assert '不给星级' in captured['prompt'] and '低纹理' in captured['prompt']
    assert captured['images'][0].name=='overview.png'
    assert all(p.name.startswith('landscape_native') for p in captured['images'][1:])
    assert len(captured['images'])>2
    assert all(max(Image.open(p).size)<=1024 for p in captured['images'])


def test_body_fallback_can_review_body_without_claiming_eyes(tmp_path,monkeypatch):
    asset=photo(tmp_path,'portrait')
    settings=CropSettings(photos={CropSettings().key(asset):{'selected_bodies':[[.1,.1,.4,.8]]}})
    confirmed_settings(asset,settings)
    monkeypatch.setattr(ai_focus,'detail_features',lambda *a:pytest.fail('manual body-only scope must not detect a background face'))
    captured={}
    def call(profile,prompt,images):
        captured['prompt']=prompt
        return protocol_reply(prompt)
    monkeypatch.setattr(ai_focus,'call_model',call)
    result=ai_focus.review_focus(asset,settings,PROFILE,tmp_path/'cache')
    assert result['status']=='uncertain'
    assert '身体清楚不能证明脸或眼睛清楚' in captured['prompt']
    assert result['participants'][0]['parts'][0]['severity']=='uncertain'
    assert result['participants'][0]['parts'][1]['severity']=='clear'
    assert result['participants'][0]['subject_id'].startswith('body:')


def test_technical_response_cannot_supply_stars():
    with pytest.raises(ApiError,match='字段无效'):
        ai_focus._parse_response('{"photo_identity":"p","status":"clear","reason":"sharp","rating":5}','p')


def test_paired_jpg_change_invalidates_ai_source_identity(tmp_path):
    asset=photo(tmp_path,'portrait');asset.raw_path=tmp_path/'source.dng';asset.raw_path.write_bytes(b'raw')
    before=ai_focus._source_identity(asset,None)
    Image.new('RGB',(1400,1000),'red').save(asset.jpg_path)
    assert before!=ai_focus._source_identity(asset,None)


def test_multiple_selected_bodies_keep_each_result_and_do_not_reject_whole_photo(tmp_path,monkeypatch):
    asset=photo(tmp_path,'portrait')
    settings=CropSettings(photos={CropSettings().key(asset):{'selected_faces':[],
        'selected_bodies':[[.05,.05,.4,.9],[.55,.05,.4,.9]]}})
    confirmed_settings(asset,settings)
    monkeypatch.setattr(ai_focus,'detail_features',lambda *a:None)
    seen=[]
    def call(profile,prompt,images):
        index=len(seen)+1;seen.append(prompt)
        return protocol_reply(prompt,'clear' if index==1 else 'severe')
    monkeypatch.setattr(ai_focus,'call_model',call)
    result=ai_focus.review_focus(asset,settings,PROFILE,tmp_path/'cache')
    assert result['status']=='blur'
    assert [row['parts'][1]['severity'] for row in result['participants']]==['clear','severe']
    assert all(row['parts'][0]['severity']=='uncertain' for row in result['participants'])
    assert len(seen)==2
    assert all(row['subject_id'].startswith('body:') for row in result['participants'])
    assert len({row['subject_id'] for row in result['participants']})==2


def test_body_geometry_tags_preserve_face_association(tmp_path):
    asset=photo(tmp_path,'portrait')
    settings=CropSettings(photos={CropSettings().key(asset):{'selected_bodies':[[.1,.1,.4,.8]]}})
    asset.clarity_evidence={'body_candidates':[{'box':[.1,.1,.4,.8],
        'associated_face':1,'association':'pose_head_overlap'}]}
    tags=ai_focus._subject_tags(asset,settings,None,'body')
    assert tags['associated_face']==1 and tags['association']=='pose_head_overlap'
    assert tags['body_box']==[.1,.1,.4,.8]
    assert tags['subject_id']=='body:0.100000,0.100000,0.400000,0.800000'
