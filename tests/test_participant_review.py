from types import SimpleNamespace as NS
import pytest
from ai_cull_assistant.participant_review import needs_person_review, participant_labels
from ai_cull_assistant.crop_settings import CropSettings


def test_confirmation_selection_and_draft_labels():
    a=NS(subject_features=NS(candidate_count=3),ai_focus_dirty=False,
         clarity_evidence={'participants':[{'participant':1,'state':'clear'},{'participant':2,'state':'severe_blur'}]})
    assert needs_person_review(a,{})
    entry={'selected_faces':[[.1,.1,.2,.2],[.4,.1,.2,.2]]}
    assert not needs_person_review(a,entry)
    assert '人物 2：明显模糊' in participant_labels(a,entry,entry,2)[1]
    assert all('待重新扫描' in s for s in participant_labels(a,entry,{},2))
    a.ai_focus_result={'participants':[{'participant':1,'status':'uncertain'},{'participant':2,'status':'clear'}]}
    assert '待确认（AI）' in participant_labels(a,entry,entry,2)[0]


def test_unconfirmed_local_and_api_do_not_process(tmp_path, monkeypatch):
    from ai_cull_assistant.models import PhotoAsset
    from datetime import datetime
    from ai_cull_assistant.face_focus import assess_asset_focus
    from ai_cull_assistant.ai_focus import review_focus
    from ai_cull_assistant.ai_project import ReviewProject
    a=PhotoAsset('p',tmp_path/'p.jpg',tmp_path/'p.jpg',None,tmp_path/'p.jpg',datetime.now(),'.jpg')
    a.subject_features=NS(candidate_count=2)
    local=assess_asset_focus(a,crop_settings=CropSettings())
    assert local.reason=='person_selection_pending' and not local.rejected
    assert not ReviewProject._asset_is_admitted(a)
    assert not ReviewProject._photo_is_admitted({'person_review_pending':True,'clarity_version':'old'})
    with pytest.raises(ValueError,match='人物待确认'):
        review_focus(a,CropSettings(),{},tmp_path)

def test_app_guard_blocks_unconfirmed_and_accepts_explicit_choice(tmp_path, monkeypatch):
    from datetime import datetime
    from ai_cull_assistant.models import PhotoAsset
    from ai_cull_assistant.app import App
    from ai_cull_assistant import app as module
    a=PhotoAsset('p',tmp_path/'p.jpg',tmp_path/'p.jpg',None,tmp_path/'p.jpg',datetime.now(),'.jpg')
    a.subject_features=NS(candidate_count=2)
    notices=[]; steps=[]
    monkeypatch.setattr(module.messagebox,'showinfo',lambda *args,**kwargs:notices.append(args))
    settings=CropSettings()
    fake=NS(scan_result=NS(assets=[a]),crop_settings=settings,next_step_var=NS(set=steps.append))
    assert App._warn_unconfirmed_people(fake)
    assert len(notices)==1 and '确认合影主体' in steps[0]
    settings.photos[settings.key(a)]={'selected_faces':[[.1,.1,.2,.2]]}
    assert not App._warn_unconfirmed_people(fake)
