"""Headless interaction-contract tests; actual Tk layout is tested separately."""
from types import SimpleNamespace as NS

from ai_cull_assistant.body_subject_dialog import BodySubjectDialog, visible_body_boxes, toggle_body_box


def test_body_candidates_render_and_explicit_ignore_persists():
    box=(.1,.1,.3,.7)
    a=NS(clarity_evidence={'body_candidates':[{'box':box}]})
    assert visible_body_boxes(a,{})==[box]
    assert visible_body_boxes(a,{'selected_bodies':[]})==[]
    assert visible_body_boxes(a,{'hidden':True})==[]
    assert visible_body_boxes(a,{'selected_bodies':[[0,0,2,1],box]})==[box]


def test_toggle_adds_removes_only_clicked_body():
    first=(.1,.1,.3,.7); second=(.6,.1,.3,.7)
    assert toggle_body_box([first],second)==[first,second]
    assert toggle_body_box([first,second],first)==[second]


def test_drag_adds_body_and_click_removes_without_face_fields():
    canvas=NS(delete=lambda *a:None)
    renders=[]
    fake=NS(_image_rect=(0,0,400,400),_drag=None,selected=[],candidates=[],canvas=canvas,render=lambda:renders.append(1))
    fake._normalized=lambda x,y:BodySubjectDialog._normalized(fake,x,y)
    BodySubjectDialog.pointer_down(fake,NS(x=40,y=40))
    BodySubjectDialog.pointer_up(fake,NS(x=160,y=320))
    assert fake.selected==[(.1,.1,.30000000000000004,.7000000000000001)]
    BodySubjectDialog.pointer_down(fake,NS(x=100,y=100))
    BodySubjectDialog.pointer_up(fake,NS(x=100,y=100))
    assert fake.selected==[] and len(renders)==2


def test_apply_and_restore_are_explicit_different_operations():
    results=[]; closes=[]
    fake=NS(selected=[(.1,.1,.3,.7)],on_apply=results.append,destroy=lambda:closes.append(1))
    BodySubjectDialog.apply(fake)
    BodySubjectDialog.restore_auto(fake)
    assert results==[[[.1,.1,.3,.7]],None]
    assert len(closes)==2


def test_each_body_has_separate_quality_label_and_manual_edit_goes_pending():
    from ai_cull_assistant.participant_review import body_participant_labels, needs_person_review
    boxes=[[.1,.1,.3,.7],[.6,.1,.3,.7]]
    a=NS(clarity_evidence={'body_candidates':[{'box':box} for box in boxes],
                          'subjects':[{'evidence_kind':'body','state':'clear','box':boxes[0]},{'evidence_kind':'body','state':'severe_blur','box':boxes[1]}],
                          'selection_pending':True})
    assert needs_person_review(a,{})
    entry={'selected_bodies':boxes}
    assert not needs_person_review(a,entry)
    labels=body_participant_labels(a,entry,entry)
    assert '身体细节清晰' in labels[0] and '身体明显模糊' in labels[1]
    assert all('眼部未评估' in label for label in labels)
    assert all('待重新扫描' in label for label in body_participant_labels(a,entry,{}))


def test_stale_ai_participant_labels_use_fresh_local_after_rescan():
    from ai_cull_assistant.participant_review import participant_labels
    entry={'selected_faces':[[.1,.1,.2,.2],[.5,.1,.2,.2]]}
    a=NS(ai_focus_dirty=False,ai_focus_stale=True,subject_features=NS(candidate_count=2),
         ai_focus_result={'status':'blur','participants':[{'participant':1,'status':'blur'},{'participant':2,'status':'blur'}]},
         clarity_evidence={'participants':[{'participant':1,'state':'clear'},{'participant':2,'state':'uncertain'}]})
    labels=participant_labels(a,entry,entry,2)
    assert '清晰（本地）' in labels[0]
    assert '待确认（本地）' in labels[1]
    assert all('AI' not in label for label in labels)


def test_landscape_never_requires_subject_confirmation_from_old_face_data():
    from ai_cull_assistant.participant_review import needs_person_review
    a=NS(analysis_mode='landscape',subject_features=NS(candidate_count=4),
         clarity_evidence={'selection_pending':True,'body_candidates':[{},{}]})
    assert not needs_person_review(a,{})


def test_body_labels_match_valid_ai_by_box_not_mixed_participant_position(tmp_path):
    from ai_cull_assistant.participant_review import body_participant_labels
    boxes=[[.1,.1,.3,.7],[.6,.1,.3,.7]]
    entry={'selected_bodies':boxes}
    from test_ai_focus_redesign import photo
    from quality_cases import technical_result
    from ai_cull_assistant.crop_settings import CropSettings, face_box_key
    from ai_cull_assistant import focus_protocol
    a=photo(tmp_path,'portrait')
    settings=CropSettings(photos={CropSettings().key(a):entry})
    a.clarity_evidence={'body_results':[{'evidence_kind':'body','box':box,'state':'uncertain'} for box in boxes]}
    result=technical_result(a,settings)
    for person in result['participants']:
        if person['subject_id']=='body:'+face_box_key(boxes[1]):
            person['parts'][1]['severity']='severe'
    a.ai_focus_result=focus_protocol.assemble_result(result['scope'],result['participants'])
    labels=body_participant_labels(a,entry,entry)
    assert '身体细节清晰（AI' in labels[0] and '身体严重模糊（AI' in labels[1]
    a.ai_focus_stale=True
    assert all('身体待复核（本地' in value for value in body_participant_labels(a,entry,entry))
    a.ai_focus_stale=False
    a.ai_focus_result['participants'][1]['parts'].pop()  # malformed new evidence is not displayed as valid
    assert '身体待复核（本地' in body_participant_labels(a,entry,entry)[1]


def test_body_draft_edit_invalidates_displayed_face_judgments():
    from ai_cull_assistant.participant_review import participant_labels
    original={'selected_faces':[[.1,.1,.2,.2]]}
    draft=dict(original,selected_bodies=[[.1,.1,.3,.7]])
    a=NS(ai_focus_dirty=False,ai_focus_stale=False,subject_features=NS(candidate_count=1),
         ai_focus_result={'status':'clear'},clarity_evidence={'state':'clear'})
    assert participant_labels(a,draft,original,1)==['人物 1：待重新扫描']
