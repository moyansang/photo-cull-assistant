from types import SimpleNamespace
from ai_cull_assistant.relative_focus import flag_relative_focus


def photo(lap,curvature):
    return SimpleNamespace(clarity_version='clarity-v2',auto_rejected=False,group_id=1,
        subject_features=SimpleNamespace(face=(.3,.2,.2,.3),landmarks=((.35,.3),(.45,.3),(.4,.35),(.36,.4),(.44,.4))),
        clarity_evidence=dict(state='clear',reasons=[],fine=dict(laplacian_normalized=lap,edge_curvature=curvature)),
        screening_reason='subject_not_obviously_blurred')


def test_relative_evidence_only_withholds_weak_candidate_never_rejects():
    a,b,c=photo(.004,.18),photo(.018,.26),photo(.019,.27)
    assert flag_relative_focus([a,b,c],[a])==[a]
    assert a.clarity_evidence['state']=='uncertain' and not a.auto_rejected
    assert b.clarity_evidence['state']=='clear'


def test_no_comparable_reference_or_all_soft_does_not_invent_clear_or_reject():
    a,b,c=photo(.004,.18),photo(.004,.18),photo(.004,.18)
    assert flag_relative_focus([a,b,c],[a])==[]
    b.subject_features.face=(.1,.1,.7,.8)
    assert flag_relative_focus([a,b],[a])==[]
