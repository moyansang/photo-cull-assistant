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


def test_manual_detail_reuses_detection_for_crop_only_changes(tmp_path, monkeypatch):
    path = tmp_path/'manual.jpg'
    Image.new('RGB', (200, 100), 'gray').save(path)
    asset = _asset(path)
    key = settings_key(asset)
    settings = CropSettings(photos={key:{'manual_face':[.2,.2,.5,.6]}})
    monkeypatch.setattr(subject, 'asset_features', lambda *a, **kw: None)
    calls = []
    monkeypatch.setattr(subject, 'detect', lambda *a, **kw: calls.append(1) or [])
    subject.detail_features(asset, settings)
    settings.photos[key].update(scale_factor=1.3, shift_factor=.3, offset_x_factor=.2)
    subject.detail_features(asset, settings)
    assert len(calls) == 1
    settings.photos[key]['manual_face'] = [.3,.2,.4,.6]
    subject.detail_features(asset, settings)
    assert len(calls) == 2


def test_old_missing_face_only_refreshes_when_explicitly_scanning(tmp_path, monkeypatch):
    path = tmp_path/'old.jpg'
    Image.new('RGB', (200, 100), 'gray').save(path)
    asset = _asset(path)
    asset.subject_checked = True
    asset.subject_features = subject.SubjectFeatures('a', 'b', None, None)
    calls = []
    found = subject.SubjectFeatures('a', 'b', None, None, (.2,.2,.3,.4),
                                    head_source='head_detector', detection_version=subject.DETECTION_VERSION)
    monkeypatch.setattr(subject, 'features', lambda *a: calls.append(1) or found)
    assert subject.detail_features(asset, CropSettings()).head is None
    assert not calls
    result = subject.detail_features(asset, CropSettings(), require_landmarks=True)
    assert result.head and result.face is None
    subject.detail_features(asset, CropSettings(), require_landmarks=True)
    assert len(calls) == 1


def test_larger_body_anchored_head_suppresses_weak_background_face(monkeypatch):
    import numpy as np
    from ai_cull_assistant.head_detection import HeadDetection
    logo = FaceDetection((10,10,19,20), ((12,12),)*5, .82)
    head = HeadDetection((150,100,150,180), .96, 'body_anchor_head')
    monkeypatch.setattr(subject, '_head_candidates', lambda image: [head])
    monkeypatch.setattr(subject, '_refine_head_face', lambda *a: None)
    found, located = subject._choose_subject(np.zeros((500,400,3),dtype=np.uint8),[logo],.8)
    assert found is None and located == head


def test_strong_visible_face_avoids_extra_head_model(monkeypatch):
    import numpy as np
    face = FaceDetection((150,100,150,180), ((170,150),)*5, .96)
    monkeypatch.setattr(subject, '_head_candidates', lambda image: (_ for _ in ()).throw(AssertionError('fallback')))
    found, located = subject._choose_subject(np.zeros((500,400,3),dtype=np.uint8),[face],.8)
    assert found == face and located is None


def test_ambiguous_head_candidates_do_not_choose_arbitrarily(monkeypatch):
    import numpy as np
    from ai_cull_assistant.head_detection import HeadDetection
    heads = [HeadDetection((20,20,100,100),.96,'body_anchor_head'),
             HeadDetection((200,20,100,100),.94,'body_anchor_head')]
    monkeypatch.setattr(subject,'_head_candidates',lambda image:heads)
    assert subject._choose_subject(np.zeros((500,400,3),dtype=np.uint8),[],.8) == (None,None)
