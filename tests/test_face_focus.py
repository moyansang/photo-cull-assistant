from datetime import datetime
from types import SimpleNamespace
import numpy as np
from PIL import Image
from ai_cull_assistant import face_focus
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.focus_metrics import focus_metrics


def asset(tmp_path):
    p=tmp_path/'source.png'; Image.new('RGB',(1000,1000),'gray').save(p)
    preview=tmp_path/'preview.jpg'; Image.new('RGB',(100,100),'gray').save(preview)
    return PhotoAsset('source',p,p,None,p,datetime.now(),'.png',preview_path=preview)


def test_native_source_and_cache_invalidation(tmp_path,monkeypatch):
    a=asset(tmp_path); calls=[]; seen=[]
    monkeypatch.setattr(face_focus,'detail_features',lambda a,s:SimpleNamespace(face=s.photos.get('box',(.2,.2,.5,.5))))
    def decode(a):
        calls.append(a.primary_path); return Image.new('RGB',(1000,1000),'gray')
    def score(im):
        seen.append(im.shape); return {'state':'severe_blur'}
    monkeypatch.setattr(face_focus,'load_full_image',decode)
    monkeypatch.setattr(face_focus,'detail_metrics',score)
    r=face_focus.assess_asset_focus(a,cache_dir=tmp_path/'cache')
    assert r.rejected and r.source_size==(1000,1000) and seen==[(500,500,3)]
    assert face_focus.assess_asset_focus(a,cache_dir=tmp_path/'cache').rejected
    assert len(calls)==1
    face_focus.assess_asset_focus(a,cache_dir=tmp_path/'cache',crop_settings=CropSettings(photos={'box':(.1,.1,.6,.6)}))
    assert len(calls)==2
    Image.new('RGB',(1000,1000),'white').save(a.primary_path)
    face_focus.assess_asset_focus(a,cache_dir=tmp_path/'cache')
    assert len(calls)==3


def test_absent_face_does_not_decode(tmp_path,monkeypatch):
    a=asset(tmp_path)
    monkeypatch.setattr(face_focus,'detail_features',lambda *args:None)
    monkeypatch.setattr(face_focus,'load_full_image',lambda a: (_ for _ in ()).throw(AssertionError('decode')))
    r=face_focus.assess_asset_focus(a)
    assert not r.rejected and not r.face_found


def test_manual_face_and_hidden_face(tmp_path,monkeypatch):
    a=asset(tmp_path)
    monkeypatch.setattr('ai_cull_assistant.subject.asset_features',lambda *args,**kwargs:None)
    settings=CropSettings(); settings.photos[settings.key(a)]={'manual_face':[.2,.2,.5,.5]}
    r=face_focus.assess_asset_focus(a,crop_settings=settings)
    assert r.face_found and r.face_box==(200,200,500,500) and not r.rejected
    settings.photos[settings.key(a)]={'hidden':True}
    assert not face_focus.assess_asset_focus(a,crop_settings=settings).face_found


def test_resolution_and_contrast_are_not_blur_evidence():
    rng=np.random.default_rng(4)
    assert focus_metrics(rng.integers(0,255,(250,250,3),dtype=np.uint8))['state']=='uncertain'
    assert focus_metrics(np.full((500,500,3),128,dtype=np.uint8))['state']=='uncertain'


def test_source_geometry_mismatch_is_not_rejected(tmp_path,monkeypatch):
    a=asset(tmp_path)
    monkeypatch.setattr(face_focus,'detail_features',lambda *args:SimpleNamespace(face=(.2,.2,.5,.5)))
    monkeypatch.setattr(face_focus,'load_full_image',lambda a:Image.new('RGB',(1000,500)))
    r=face_focus.assess_asset_focus(a)
    assert not r.rejected and r.reason=='source_preview_geometry_mismatch'
