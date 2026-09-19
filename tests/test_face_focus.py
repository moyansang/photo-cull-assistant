from datetime import datetime
from types import SimpleNamespace
import cv2
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
    monkeypatch.setattr(face_focus,'detail_features',lambda a,s,**kw:SimpleNamespace(
        face=s.photos.get('box',(.2,.2,.5,.5)),
        landmarks=((.32,.36),(.56,.36),(.44,.47),(.35,.58),(.53,.58)),
    ))
    def decode(a):
        calls.append(a.primary_path); return Image.new('RGB',(1000,1000),'gray')
    def score(im, *, landmarks=None):
        seen.append((im.shape,landmarks)); return {'state':'severe_blur'}
    monkeypatch.setattr(face_focus,'load_full_image',decode)
    monkeypatch.setattr(face_focus,'detail_metrics',score)
    r=face_focus.assess_asset_focus(a,cache_dir=tmp_path/'cache')
    assert r.rejected and r.source_size==(1000,1000)
    assert seen[0][0]==(500,500,3) and seen[0][1][0]==(120.0,160.0)
    assert face_focus.assess_asset_focus(a,cache_dir=tmp_path/'cache').rejected
    assert len(calls)==1
    face_focus.assess_asset_focus(a,cache_dir=tmp_path/'cache',crop_settings=CropSettings(photos={'box':(.1,.1,.6,.6)}))
    assert len(calls)==2
    Image.new('RGB',(1000,1000),'white').save(a.primary_path)
    face_focus.assess_asset_focus(a,cache_dir=tmp_path/'cache')
    assert len(calls)==3


def test_absent_face_does_not_decode(tmp_path,monkeypatch):
    a=asset(tmp_path)
    monkeypatch.setattr(face_focus,'detail_features',lambda *args,**kwargs:None)
    monkeypatch.setattr(face_focus,'load_full_image',lambda a: (_ for _ in ()).throw(AssertionError('decode')))
    r=face_focus.assess_asset_focus(a)
    assert not r.rejected and not r.face_found


def test_head_only_detection_is_not_face_clarity_evidence(tmp_path, monkeypatch):
    a = asset(tmp_path)
    monkeypatch.setattr(face_focus, 'detail_features', lambda *args, **kw: SimpleNamespace(
        face=None, head=(.2,.1,.3,.4), landmarks=None, head_source='head_detector'))
    monkeypatch.setattr(face_focus, 'load_full_image', lambda a: (_ for _ in ()).throw(AssertionError('decode')))
    result = face_focus.assess_asset_focus(a)
    assert not result.rejected and not result.face_found
    assert result.reason == 'head_only_localized'
    assert result.focus_evidence['state'] == 'uncertain'


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
    monkeypatch.setattr(face_focus,'detail_features',lambda *args,**kwargs:SimpleNamespace(face=(.2,.2,.5,.5)))
    monkeypatch.setattr(face_focus,'load_full_image',lambda a:Image.new('RGB',(1000,500)))
    r=face_focus.assess_asset_focus(a)
    assert not r.rejected and r.reason=='source_preview_geometry_mismatch'


def test_strong_but_soft_edges_do_not_hide_low_detail():
    # Smooth high-contrast transitions have large gradients without sharp detail.
    x = np.arange(512)
    gray = np.tile((127.5 + 120 * np.sin(x * 2 * np.pi * 8 / 512)).astype(np.uint8), (512, 1))
    image = np.repeat(gray[:, :, None], 3, axis=2)
    landmarks=((170,205),(342,205),(256,280),(205,365),(307,365))
    result = focus_metrics(image,landmarks=landmarks)
    assert result['fine']['gradient_p90_normalized'] > .25
    assert result['state'] == 'severe_blur'
    # The same pattern at insufficient native resolution is not safe to reject.
    small_landmarks=tuple((x/4,y/4) for x,y in landmarks)
    assert focus_metrics(image[::4, ::4],landmarks=small_landmarks)['state'] == 'uncertain'


def test_missing_or_invalid_landmarks_never_auto_decide():
    image=np.zeros((500,500,3),dtype=np.uint8)
    cv2.rectangle(image,(120,120),(380,380),(255,255,255),3)
    assert focus_metrics(image)['state']=='uncertain'
    assert focus_metrics(image,landmarks=((1,2),))['reasons']==['missing_reliable_landmarks']


def test_selected_participants_use_any_blur_all_clear_and_uncertain_rules(tmp_path, monkeypatch):
    a = asset(tmp_path)
    key = str(a.primary_path.resolve()).casefold()
    settings = CropSettings(photos={key: {'selected_faces': [[.1,.1,.3,.3], [.6,.1,.3,.3]]}})
    subjects = [
        SimpleNamespace(face=(.1,.1,.3,.3), landmarks=((.15,.15),)*5),
        SimpleNamespace(face=(.6,.1,.3,.3), landmarks=((.65,.15),)*5),
    ]
    monkeypatch.setattr(face_focus, 'detail_features_list', lambda *a, **k: subjects)
    states = iter(({'state':'clear'}, {'state':'severe_blur'}))
    monkeypatch.setattr(face_focus, 'detail_metrics', lambda *a, **k: next(states))

    rejected = face_focus.assess_asset_focus(a, crop_settings=settings)

    assert rejected.rejected and rejected.reason == 'obvious_subject_blur'
    assert [item['state'] for item in rejected.focus_evidence['participants']] == ['clear', 'severe_blur']

    states = iter(({'state':'clear'}, {'state':'uncertain'}))
    monkeypatch.setattr(face_focus, 'detail_metrics', lambda *a, **k: next(states))
    pending = face_focus.assess_asset_focus(a, crop_settings=settings)
    assert not pending.rejected and pending.reason == 'face_focus_uncertain'


def test_explicit_empty_participant_selection_does_not_reject(tmp_path):
    a = asset(tmp_path)
    key = str(a.primary_path.resolve()).casefold()
    result = face_focus.assess_asset_focus(
        a,
        crop_settings=CropSettings(photos={key: {'selected_faces': []}}),
    )
    assert not result.rejected and not result.face_found
