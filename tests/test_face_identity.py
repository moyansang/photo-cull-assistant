from collections import OrderedDict
from types import SimpleNamespace as NS
from datetime import datetime
import threading
import tkinter as tk
import numpy as np
import pytest
from PIL import Image
from ai_cull_assistant import face_identity as fi, group_face_assist as ga
from ai_cull_assistant.assist_feedback import AssistFeedback
from ai_cull_assistant.group_face_assist_dialog import GroupFaceAssistDialog
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.yunet import FaceDetection

BOX=(.1,.1,.3,.4)
def vector(x=1.,y=0.):
    a=np.array([x,y],np.float32);return a/np.linalg.norm(a)
def face(v,box=BOX):return fi.FaceFeature(box,v,.95)
def engine():
    e=fi.IdentityEngine.__new__(fi.IdentityEngine);e.fingerprint='test';e.cache=OrderedDict();return e
def detected():return FaceDetection((10.,10.,30.,40.),((15.,20.),(30.,20.),(22.,30.),(17.,40.),(28.,40.)),.95)

@pytest.mark.parametrize('count,expected',[(1,.52),(2,.53),(3,.53),(4,.56),(5,.56),(6,.56)])
def test_experimental_reference_count_rules(count,expected):assert fi.threshold_for(count)==expected

@pytest.mark.parametrize('count',[0,7])
def test_invalid_reference_count(count):
    with pytest.raises(ValueError):fi.threshold_for(count)

def test_missing_and_corrupt_model_never_falls_back(tmp_path):
    with pytest.raises(fi.IdentityUnavailable):fi.IdentityEngine(tmp_path/'missing.onnx')
    path=tmp_path/'损坏模型.onnx';path.write_bytes(b'bad')
    with pytest.raises(fi.IdentityUnavailable,match='校验失败'):fi.IdentityEngine(path)

def test_packaged_model_cpu_inference():
    e=fi.IdentityEngine()
    # Real CPU model inference on a generated aligned crop; not an accuracy test.
    feature=e.recognizer.feature(np.zeros((112,112,3),np.uint8)).copy()
    assert feature.size==128 and np.isfinite(feature).all() and np.linalg.norm(feature)>0

def test_cache_content_and_stop_even_on_hit(monkeypatch):
    e=engine();calls=[]
    monkeypatch.setattr(fi.yunet,'detect',lambda *_a,**_k:(calls.append(1) or [detected()]))
    monkeypatch.setattr(e,'_feature',lambda *_:vector())
    im=np.zeros((100,100,3),np.uint8)
    e.faces(im);e.faces(im.copy());assert len(calls)==1
    im[0,0]=1;e.faces(im);assert len(calls)==2
    stop=threading.Event();stop.set()
    with pytest.raises(InterruptedError):e.faces(im,stop)
    e.clear();assert not e.cache

def test_model_preprocess_fingerprint_invalidates_cache(monkeypatch):
    e=engine();calls=[];im=np.zeros((100,100,3),np.uint8)
    monkeypatch.setattr(fi.yunet,'detect',lambda *_a,**_k:(calls.append(1) or []))
    e.faces(im);e.fingerprint='different model or preprocessing';e.faces(im);assert len(calls)==2

@pytest.mark.parametrize('faces,reason',[([], '未找到'),([face(vector()),face(vector())],'多张')])
def test_reference_requires_one_real_face(monkeypatch,faces,reason):
    e=engine();monkeypatch.setattr(e,'faces',lambda *_:faces)
    with pytest.raises(fi.IdentityUnavailable,match=reason):e.reference(np.zeros((100,100,3),np.uint8),(0,0,1,1))

def test_reference_box_change_changes_selection(monkeypatch):
    e=engine();regions=[]
    monkeypatch.setattr(e,'faces',lambda rgb,stop:(regions.append(rgb.shape) or [face(vector(),(.4,.4,.2,.2))]))
    im=np.zeros((200,200,3),np.uint8)
    e.reference(im,(.1,.1,.2,.2));e.reference(im,(.1,.1,.5,.5));assert regions[0]!=regions[1]

def test_low_score_and_near_tie_require_review():
    ref=[('main',vector())]
    low=fi.rank_features([face(vector(.2,.98))],ref);assert low[0].reasons
    near=fi.rank_features([face(vector(.9,.43)),face(vector(.89,.46),(.6,.1,.2,.3))],ref)
    assert any('分数接近' in reason for reason in near[0].reasons)

def test_conflicting_positive_and_negative_references():
    features=[face(vector())]
    conflict=fi.rank_features(features,[('main',vector()),('other',vector(0,1))])
    assert any('正参考' in reason for reason in conflict[0].reasons)
    negative=fi.rank_features(features,[('main',vector())],[('rejected',vector())])
    assert any('排除' in reason for reason in negative[0].reasons)

def test_reference_change_recomputes_aggregation_and_records_source():
    features=[face(vector())]
    first=fi.rank_features(features,[('a',vector(0,1))])
    second=fi.rank_features(features,[('b',vector())])
    assert first[0].reasons and not second[0].reasons and second[0].matched_reference=='b'

def test_no_landmarks_or_nan_feature_is_not_evidence():
    e=engine();e.recognizer=NS(alignCrop=lambda *_:np.zeros((112,112,3),np.uint8),feature=lambda *_:np.array([[float('nan')]],np.float32))
    with pytest.raises(fi.IdentityUnavailable):e._feature(np.zeros((100,100,3),np.uint8),detected())
    with pytest.raises(fi.IdentityUnavailable):e._feature(np.zeros((100,100,3),np.uint8),FaceDetection((1,1,20,20),(),.9))

def test_stop_after_inference_does_not_emit_cached_result(monkeypatch):
    e=engine();stop=threading.Event()
    def detect(*_a,**_k):stop.set();return [detected()]
    monkeypatch.setattr(fi.yunet,'detect',detect)
    with pytest.raises(InterruptedError):e.faces(np.zeros((100,100,3),np.uint8),stop)
    assert not e.cache

def test_proposals_keep_alternatives_without_default_and_clear_cache(monkeypatch,tmp_path):
    path=tmp_path/'image.png';Image.new('RGB',(100,100),'gray').save(path)
    e=engine();e.cache['test']='test'
    monkeypatch.setattr(e,'reference',lambda *_:vector())
    monkeypatch.setattr(e,'faces',lambda *_:[face(vector(.2,.98))])
    monkeypatch.setattr(fi,'IdentityEngine',lambda:e)
    out=fi.propose_identity_faces(ga.AssistImage('r','main',str(path)),BOX,[ga.AssistTarget('t','target',str(path))],threading.Event())
    assert out[0].box is None and out[0].candidate_boxes==(BOX,) and out[0].level=='uncertain'
    assert out[0].detector_score is None and '不是身份概率' in out[0].reason and not e.cache

def test_invalid_reference_blocks_whole_run_without_appearance_fallback(monkeypatch,tmp_path):
    def unavailable(*_a,**_k):raise fi.IdentityUnavailable('reference unavailable')
    monkeypatch.setattr(fi,'IdentityEngine',unavailable)
    monkeypatch.setattr(ga,'propose_person_faces',lambda *_a,**_k:pytest.fail('silent fallback'))
    results=fi.propose_identity_faces(ga.AssistImage('r','main',None),BOX,[ga.AssistTarget('t','t',None)],threading.Event())
    assert results[0].box is None and results[0].level=='uncertain'

def test_feedback_mode_is_backward_compatible_and_does_not_store_features(tmp_path):
    f=AssistFeedback(tmp_path,'ref',BOX,[1,2]);assert f.matching_mode()=='appearance'
    f.set_matching_mode('identity');f.save_row('target',[2,3],BOX,'confirmed',True,original_box=BOX)
    reopened=AssistFeedback(tmp_path,'ref',BOX,[1,2]);assert reopened.matching_mode()=='identity' and len(reopened.samples())==1
    assert 'vector' not in reopened.path.read_text(encoding='utf-8')
    with pytest.raises(ValueError):f.set_matching_mode('unknown')

def test_stale_worker_message_cannot_restore_old_mode_proposal():
    fake=NS(_generation=2)
    GroupFaceAssistDialog._handle_message(fake,('run',1,('proposal',1,1,None)))

def test_duplicate_worker_does_not_start_again():
    fake=NS(_worker=NS(is_alive=lambda:True))
    GroupFaceAssistDialog._start_worker(fake)

def test_mode_ui_persistence_and_explicit_review(tmp_path,monkeypatch):
    root=tk.Tk();root.withdraw();assets={};applied=[]
    for key in ('ref','query'):
        p=tmp_path/(key+'.png');Image.new('RGB',(160,160),'gray').save(p)
        assets[key]=PhotoAsset(key,p,p,None,p,datetime.now(),'.png',preview_path=p)
    monkeypatch.setattr(GroupFaceAssistDialog,'_start_worker',lambda self:None)
    def create():return GroupFaceAssistDialog(root,ga.AssistImage('ref','ref',str(assets['ref'].primary_path)),BOX,
        [ga.AssistTarget('query','query',str(assets['query'].primary_path))],lambda *args:(applied.append(args) or (1,[])),
        target_assets=assets,workspace=tmp_path/'workspace',input_dir=tmp_path)
    try:
        d=create();assert '外观' in d.mode_var.get()
        d.mode_var.set('人脸特征（实验性）');d._mode_changed()
        d._handle_message(('proposal',1,1,ga.FaceProposal('query','ref',None,'uncertain',.5,None,'需人工选择',(BOX,))))
        assert d.rows['query']['box'] is None and not applied
        d.candidate_picker.current(0);d._choose_candidate();assert not d.rows['query']['accepted']
        d.confirm_current();assert d.rows['query']['accepted'] and not applied
        d.destroy();d=create();assert '人脸特征' in d.mode_var.get() and d.rows['query']['accepted']
        d._finished=True;d.accept_selected();assert len(applied)==1
    finally:
        for child in root.winfo_children():
            if isinstance(child,tk.Toplevel):child.destroy()
        root.destroy()
