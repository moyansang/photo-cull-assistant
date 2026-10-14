"""Behavior regressions, not an identity-accuracy benchmark."""
from datetime import datetime
import threading
import tkinter as tk
from types import SimpleNamespace as NS

import numpy as np
from PIL import Image
import pytest

from ai_cull_assistant import person_match as pm, group_face_assist as assist
from ai_cull_assistant.group_face_assist_dialog import GroupFaceAssistDialog
from ai_cull_assistant.models import PhotoAsset

BOX = (.2, .1, .25, .25)


def pattern():
    rng = np.random.default_rng(43)
    return rng.integers(20, 235, (160, 160, 3), dtype=np.uint8)


def configured(monkeypatch, scores, refs=None, negatives=()):
    anchors = [pm._Anchor((.05 + .45*i, .1, .2, .2), 'face', .9) for i in range(len(scores))]
    monkeypatch.setattr(pm, '_detector_anchors', lambda *_: anchors)
    monkeypatch.setattr(pm, '_template_anchors', lambda *_: [])
    monkeypatch.setattr(pm, '_information_flags', lambda *_: ())
    monkeypatch.setattr(pm, '_appearance_similarity', lambda _im, ref, a: scores[anchors.index(a)] if ref.name != 'negative' else .95)
    return pm.rank_candidates_multi(pattern(), refs or [NS(name='positive', quality_flags=())], negatives)


def test_normal_separated_evidence_preserves_ranking(monkeypatch):
    candidates = configured(monkeypatch, [.9, .6])
    assert candidates[0].score == pytest.approx(.9)
    assert not candidates[0].review_reasons and not candidates[0].ambiguous


def test_low_positive_evidence_returns_no_candidate(monkeypatch):
    assert configured(monkeypatch, [.1]) == []


def test_near_threshold_is_retained_only_as_uncertain_evidence(monkeypatch):
    candidates = configured(monkeypatch, [.43])
    assert len(candidates) == 1
    assert 'near_threshold' in candidates[0].review_reasons


def test_close_candidates_keep_both_with_reason(monkeypatch):
    candidates = configured(monkeypatch, [.83, .82])
    assert len(candidates) == 2 and candidates[0].ambiguous
    assert 'ambiguous' in candidates[0].review_reasons


def test_rejected_reference_conflict_does_not_silently_drop_candidate(monkeypatch):
    candidates = configured(monkeypatch, [.8], negatives=[NS(name='negative')])
    assert len(candidates) == 1
    assert 'negative_conflict' in candidates[0].review_reasons
    assert candidates[0].score == pytest.approx(.9*.8+.09-.15*.95)


@pytest.mark.parametrize('gray,flat,expected', [
    (True,False,{'achromatic'}), (True,True,{'achromatic','low_texture'}),
    (False,True,{'low_texture'}), (False,False,set()),
])
def test_missing_information_diagnostics(gray, flat, expected):
    image = pattern()
    if gray:
        image[:] = image[:, :, :1]
    if flat:
        image[:] = (120,120,120) if gray else (20,80,180)
    assert set(pm.build_reference(image, BOX).quality_flags) == expected


def test_multi_reference_reports_quality_of_best_supporting_reference(monkeypatch):
    gray = NS(name='gray', quality_flags=('achromatic',))
    colour = NS(name='colour', quality_flags=())
    anchor = pm._Anchor(BOX, 'face', .9)
    monkeypatch.setattr(pm, '_detector_anchors', lambda *_: [anchor])
    monkeypatch.setattr(pm, '_template_anchors', lambda *_: [])
    monkeypatch.setattr(pm, '_information_flags', lambda *_: ())
    monkeypatch.setattr(pm, '_appearance_similarity', lambda _im, ref, _a: .7 if ref is gray else .9)
    ranked = pm.rank_candidates_multi(pattern(), [gray, colour])
    assert not ranked[0].review_reasons
    assert ranked[0].score == pytest.approx(.9)


@pytest.mark.parametrize('candidates,level', [
    ([], 'missing'),
    ([pm.PersonCandidate(BOX,.8,.9,'face',.8)], 'review'),
    ([pm.PersonCandidate(BOX,.8,.9,'face',.8,review_reasons=('reference_achromatic',))], 'uncertain'),
    ([pm.PersonCandidate(BOX,.8,.9,'face',.8,ambiguous=True)], 'uncertain'),
])
def test_proposal_exposes_uncertainty_without_default_identity_box(tmp_path,monkeypatch,candidates,level):
    path = tmp_path/'image.png'
    Image.fromarray(pattern()).save(path)
    monkeypatch.setattr(pm,'rank_candidates',lambda *_a,**_k:candidates)
    proposal = assist.propose_person_faces(assist.AssistImage('ref','ref',str(path)),BOX,
        [assist.AssistTarget('query','query',str(path))],threading.Event())[0]
    assert proposal.level == level
    assert (proposal.box is not None) == (level == 'review')
    assert proposal.candidate_boxes == tuple(c.box for c in candidates)
    assert '目标可能不在本图' in proposal.reason or '目标仍可能不在本图' in proposal.reason


@pytest.fixture
def dialog_setup(tmp_path,monkeypatch):
    root=tk.Tk();root.withdraw()
    source=tmp_path/'photos';source.mkdir()
    assets={}
    for key in ('ref','target'):
        path=source/(key+'.png');Image.fromarray(pattern()).save(path)
        assets[key]=PhotoAsset(key,path,path,None,path,datetime.now(),'.png',preview_path=path)
    monkeypatch.setattr(GroupFaceAssistDialog,'_start_worker',lambda self:None)
    writes=[]
    def create():
        return GroupFaceAssistDialog(root,assist.AssistImage('ref','ref',str(assets['ref'].primary_path)),BOX,
            [assist.AssistTarget('target','target',str(assets['target'].primary_path))],
            lambda *items: (writes.append(items) or (1,[])),target_assets=assets,
            workspace=tmp_path/'workspace',input_dir=source)
    yield create,writes
    for child in root.winfo_children():
        if isinstance(child,tk.Toplevel):child.destroy()
    root.destroy()


def uncertain():
    return assist.FaceProposal('target','ref',None,'uncertain',.9,.9,
        '不确定：多个候选分数接近；目标可能不在本图', (BOX,(.6,.1,.2,.2)))


def test_uncertain_ui_needs_choice_then_confirmation_and_restores(dialog_setup,monkeypatch):
    create,writes=dialog_setup
    monkeypatch.setattr('ai_cull_assistant.group_face_assist_dialog.messagebox.showinfo',lambda *a,**k:None)
    dialog=create()
    dialog._handle_message(('proposal',1,1,uncertain()))
    dialog.confirm_current()
    dialog.accept_selected()
    assert not dialog.rows['target']['accepted'] and not writes and not dialog._feedback.samples()
    dialog.candidate_picker.current(1);dialog._choose_candidate()
    assert dialog.rows['target']['box'] == uncertain().candidate_boxes[1]
    assert not dialog.rows['target']['accepted'] and not dialog._feedback.samples()
    dialog.confirm_current()
    assert len(dialog._feedback.samples())==1 and not writes
    dialog.destroy()
    dialog=create()
    assert dialog.rows['target']['accepted'] and len(dialog._feedback.samples())==1
    dialog._finished=True;dialog.accept_selected()
    assert len(writes)==1


@pytest.mark.parametrize('action,status', [('skip_current','跳过'),('reject_current','不是这个人')])
def test_late_candidate_cannot_undo_explicit_no_box_decision(dialog_setup,action,status):
    create,writes=dialog_setup
    dialog=create()
    getattr(dialog,action)()
    proposal=assist.FaceProposal('target','ref',BOX,'review',.9,.9,'test')
    dialog._handle_message(('proposal',1,1,proposal))
    assert dialog.rows['target']['box'] is None
    assert dialog.rows['target']['status']==status
    getattr(dialog,action)()  # No-box feedback remains valid even after a late proposal.
    assert dialog._feedback is not None
    assert not dialog._feedback.samples() and not dialog._feedback.rejected() and not writes
    dialog.destroy()
    dialog=create()
    assert dialog.rows['target']['restored_decision'] and dialog.rows['target']['box'] is None


def test_pending_feedback_is_rechecked_not_restored_as_confirmed(dialog_setup):
    create,writes=dialog_setup
    dialog=create()
    dialog._feedback.save_row('target.png',dialog._feedback_signatures['target'],BOX,'pending',False)
    dialog.destroy()
    dialog=create()
    row=dialog.rows['target']
    assert not row['restored_decision'] and not row['accepted'] and row['box'] is None
    assert not dialog._feedback.samples() and not writes


def test_missing_candidate_still_allows_manual_box_and_confirmation(dialog_setup):
    create,writes=dialog_setup
    dialog=create()
    dialog._handle_message(('proposal',1,1,assist.FaceProposal('target','ref',None,'missing',None,None,'无可靠候选')))
    dialog._set_manual_box(BOX)
    assert not dialog.rows['target']['accepted']
    dialog.confirm_current()
    assert dialog.rows['target']['accepted'] and len(dialog._feedback.samples())==1 and not writes


def test_explicit_candidate_rejection_restores_negative_only(dialog_setup):
    create,writes=dialog_setup
    dialog=create()
    dialog._handle_message(('proposal',1,1,uncertain()))
    dialog.candidate_picker.current(0);dialog._choose_candidate()
    dialog.reject_current()
    assert not dialog._feedback.samples() and len(dialog._feedback.rejected())==1
    dialog.destroy()
    dialog=create()
    assert not dialog.rows['target']['accepted'] and dialog.rows['target']['status']=='不是这个人'
    assert len(dialog._feedback.rejected())==1 and not writes
