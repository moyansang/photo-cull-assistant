from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.group_face_assist import AssistImage, AssistTarget
from ai_cull_assistant.group_face_assist_dialog import GroupFaceAssistDialog
from ai_cull_assistant.assist_feedback import AssistFeedback

BOX=[.1,.1,.2,.2]

def test_restore_relative_progress_and_ignore_changed_sources(tmp_path):
    source=tmp_path/'photos';source.mkdir()
    assets={}
    for key in ('ref','target'):
        p=source/(key+'.jpg');p.write_bytes(b'photo')
        assets[key]=PhotoAsset(key,p,p,None,p,datetime.now(),'.jpg',preview_path=p)
    def fresh():
        return NS(target_assets=assets,reference=AssistImage('ref','ref',str(assets['ref'].primary_path)),reference_box=BOX,
            rows={'target':dict(target=AssistTarget('target','target',str(assets['target'].primary_path)),box=None,accepted=False,status='排队中',proposal=None)},
            _feedback=None,_feedback_ids={},_feedback_signatures={},_feedback_error=None)
    first=fresh();GroupFaceAssistDialog._init_feedback(first,tmp_path/'workspace',source)
    first._feedback.save_row('target.jpg',first._feedback_signatures['target'],BOX,'confirmed',True)
    first._feedback.set_position('target.jpg')
    second=fresh();GroupFaceAssistDialog._init_feedback(second,tmp_path/'workspace',source)
    assert second.rows['target']['accepted']
    assert second._feedback.get_position()=='target.jpg'
    assets['target'].primary_path.write_bytes(b'changed')
    third=fresh();GroupFaceAssistDialog._init_feedback(third,tmp_path/'workspace',source)
    assert not third.rows['target']['accepted']


def test_saved_candidates_not_automatically_applied(tmp_path):
    from ai_cull_assistant.assist_feedback import AssistFeedback
    state=AssistFeedback(tmp_path,'a',BOX,[1,2])
    state.save_row('b',[3,4],BOX,'pending',False)
    state.save_row('c',[5,6],None,'skipped',False)
    restored=AssistFeedback(tmp_path,'a',BOX,[1,2])
    assert not restored.samples() and not restored.rejected()
    assert restored.rows()['c']['status']=='skipped'

def test_dialog_confirm_restore_and_remove_reference(tmp_path, monkeypatch):
    import tkinter as tk
    from PIL import Image
    root=tk.Tk();root.withdraw()
    assets={};source=tmp_path/'photos';source.mkdir()
    for key in ('ref','target'):
        p=source/(key+'.jpg');Image.new('RGB',(160,160),'gray').save(p)
        assets[key]=PhotoAsset(key,p,p,None,p,datetime.now(),'.jpg',preview_path=p)
    monkeypatch.setattr(GroupFaceAssistDialog,'_start_worker',lambda self:None)
    reference=AssistImage('ref','ref',str(assets['ref'].primary_path))
    targets=[AssistTarget('target','target',str(assets['target'].primary_path))]
    def open_dialog():
        return GroupFaceAssistDialog(root,reference,BOX,targets,lambda *args:(0,[]),
            target_assets=assets,workspace=tmp_path/'workspace',input_dir=source)
    try:
        dialog=open_dialog();dialog._set_manual_box(BOX);dialog.confirm_current()
        assert len(dialog._feedback.samples())==1
        dialog.destroy()
        dialog=open_dialog()
        assert dialog.rows['target']['accepted']
        assert dialog.rows['target']['box']==BOX
        dialog._remove_reference()
        assert dialog._feedback.samples()==[]
        dialog.destroy()
        dialog=open_dialog()
        assert dialog.rows['target']['accepted'] and not dialog._feedback.samples()
        dialog.destroy()
    finally:
        root.destroy()
