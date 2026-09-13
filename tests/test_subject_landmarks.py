from datetime import datetime

from PIL import Image

from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant import subject
from ai_cull_assistant.yunet import FaceDetection


def _asset(path):
    return PhotoAsset(
        path.stem, path, path, None, path, datetime.now(), path.suffix,
        preview_path=path,
    )


def test_features_persist_real_normalized_yunet_landmarks(tmp_path, monkeypatch):
    path=tmp_path/'face.jpg'
    Image.new('RGB',(200,100),'gray').save(path)
    detection=FaceDetection(
        (50,20,80,60),
        ((70,42),(110,42),(90,56),(75,68),(105,68)),
        .95,
    )
    monkeypatch.setattr(subject,'detect',lambda *args,**kwargs:[detection])
    subject._cached_features.cache_clear()
    found=subject.features(path,.8)
    assert found.landmarks == ((.35,.42),(.55,.42),(.45,.56),(.375,.68),(.525,.68))


def test_manual_box_never_invents_landmarks(tmp_path, monkeypatch):
    path=tmp_path/'face.jpg'
    Image.new('RGB',(200,100),'gray').save(path)
    asset=_asset(path)
    settings=CropSettings(photos={settings_key(asset):{'manual_face':[.2,.2,.5,.6]}})
    monkeypatch.setattr(subject,'asset_features',lambda *args,**kwargs:None)
    monkeypatch.setattr(subject,'detect',lambda *args,**kwargs:[])
    found=subject.detail_features(asset,settings)
    assert found.face == (.2,.2,.5,.6)
    assert found.landmarks is None


def test_manual_box_uses_only_detected_landmarks(tmp_path, monkeypatch):
    path=tmp_path/'face.jpg'
    Image.new('RGB',(200,100),'gray').save(path)
    asset=_asset(path)
    settings=CropSettings(photos={settings_key(asset):{'manual_face':[.2,.2,.5,.6]}})
    # Expanded ROI starts at (10, 2); these points therefore map to known
    # global positions rather than a fixed fraction of the manual rectangle.
    detection=FaceDetection(
        (35,20,80,50),
        ((50,35),(90,35),(70,47),(55,60),(85,60)),
        .92,
    )
    monkeypatch.setattr(subject,'asset_features',lambda *args,**kwargs:None)
    monkeypatch.setattr(subject,'detect',lambda *args,**kwargs:[detection])
    found=subject.detail_features(asset,settings)
    assert found.landmarks[0] == (.3,.37)
    assert found.landmarks[1] == (.5,.37)


def test_explicit_focus_refreshes_legacy_face_points_without_crop_view_refresh(tmp_path, monkeypatch):
    path=tmp_path/'face.jpg'
    Image.new('RGB',(200,100),'gray').save(path)
    asset=_asset(path)
    legacy=subject.SubjectFeatures('a','b','c',(.25,.2,.4,.6),(.2,.1,.5,.8))
    refreshed=subject.SubjectFeatures(
        'a','b','c',(.25,.2,.4,.6),(.2,.1,.5,.8),
        ((.35,.4),(.55,.4),(.45,.52),(.38,.65),(.52,.65)),
    )
    asset.subject_checked=True
    asset.subject_confidence=.8
    asset.subject_features=legacy
    calls=[]
    monkeypatch.setattr(subject,'features',lambda *args:(calls.append(args),refreshed)[1])

    assert subject.detail_features(asset,CropSettings()).landmarks is None
    assert calls == []
    assert subject.detail_features(asset,CropSettings(),require_landmarks=True).landmarks == refreshed.landmarks
    assert len(calls) == 1


def settings_key(asset):
    return CropSettings().key(asset)
