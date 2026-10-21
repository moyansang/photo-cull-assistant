from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tkinter as tk
from types import SimpleNamespace
import pytest
from PIL import Image
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.scan_review_ui import result_state, evidence_text, ScanReviewPage, FILTERS
from ai_cull_assistant.scan_review_viewer import valid_rect, recorded_overlays, InspectionPane, ComparisonWindow


def asset(tmp_path):
    path=tmp_path/'合成 图.jpg';Image.new('RGB',(80,60),'white').save(path)
    return PhotoAsset('合成 图',path,path,None,path,datetime(2026,1,1),'.jpg')


def test_all_execution_states_and_missing_evidence(tmp_path):
    a=asset(tmp_path)
    assert result_state(a)=='not_run'
    a.screening_reason='source_unreadable_for_analysis'
    assert result_state(a)=='failed'
    a.screening_reason='source_preview_geometry_mismatch'
    assert result_state(a)=='review'
    assert '历史记录缺项' in evidence_text(a)
    assert '眼带≠逐眼评分' in evidence_text(a)
    assert '无反馈不表示认可' in evidence_text(a)


def test_read_models_do_not_change_records(tmp_path,monkeypatch):
    a=asset(tmp_path);a.clarity_evidence={'recommendation':'review','reasons':['insufficient_native_eye_pixels']}
    before=deepcopy(a)
    for _ in range(5):
        result_state(a);evidence_text(a);recorded_overlays(a,{})
    assert a==before
    assert not (tmp_path/'scan-review.json').exists()


@pytest.mark.parametrize('rect',[[0,0,1,1],[.1,.2,.3,.4]])
def test_valid_normalized_rect(rect):assert valid_rect(rect)


@pytest.mark.parametrize('rect',[[-.1,0,1,1],[0,0,float('nan'),1],[0,0,2,1],[0,0,0,1],['0',0,1,1],None])
def test_bad_normalized_rect(rect):assert not valid_rect(rect)


def test_overlay_requires_exact_recorded_source_and_coordinates(tmp_path):
    a=asset(tmp_path);decode={'source_path':str(a.primary_path),'source_kind':'standard_image','source_size':[80,60]}
    region=dict(decode,rect=[.1,.1,.3,.4],coordinate_space='normalized_display',role='selected')
    a.clarity_evidence={'display_regions':[region]}
    assert len(recorded_overlays(a,decode))==1
    assert recorded_overlays(a,dict(decode,source_kind='raw_embedded_jpeg'))==[]
    assert recorded_overlays(a,dict(decode,source_size=[60,80]))==[]
    region['coordinate_space']='aligned_native_face_pixels'
    assert recorded_overlays(a,decode)==[]
    region.update(coordinate_space='normalized_display',rect=[.9,.9,.5,.5])
    assert recorded_overlays(a,decode)==[]


def test_saved_production_candidates_selected_and_human_not_conflated(tmp_path):
    a=asset(tmp_path);decode={'source_path':str(a.primary_path),'source_kind':'standard_image','source_size':[80,60]}
    a.clarity_evidence={'decode':decode,'subjects':[{'subject_id':'f','evidence_kind':'face','normalized_box':[.1,.1,.4,.4]}],
        'automatic_subject':{'candidate_provenance':{'coordinate_space':'normalized_exif_oriented','initial_candidates':[{'box':[.1,.1,.4,.4]}]}}}
    assert {r['role'] for r in recorded_overlays(a,decode)}=={'candidate','selected'}
    a.clarity_evidence['primary_quality']={'selection_origin':'human','subjects':[{'id':'f'}]}
    assert {r['role'] for r in recorded_overlays(a,decode)}=={'candidate','confirmed'}


@pytest.fixture
def root():
    try:root=tk.Tk()
    except tk.TclError as exc:pytest.skip(f'Tk display unavailable; Windows/Xvfb interaction pending: {exc}')
    root.geometry('1250x850');yield root
    root.destroy()


def test_real_tk_feedback_compare_and_failed_save(root,tmp_path,monkeypatch):
    a=asset(tmp_path);b=deepcopy(a);b.stem='另一张';b.primary_path=tmp_path/'另一张.jpg';b.jpg_path=b.primary_path;b.display_path=b.primary_path
    Image.new('RGB',(80,60),'blue').save(b.primary_path)
    workspace=tmp_path/'workspace';workspace.mkdir()
    errors=[];monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.showerror',lambda *a,**k:errors.append(a))
    page=ScanReviewPage(root,[a,b],workspace);page.pack(fill='both',expand=True);root.update()
    assert len(page.tree.get_children())==2
    assert page.selected is a
    page.note.insert('1.0','只看表情，不是失焦');page._save_feedback('question');root.update()
    assert page.store.photo(a)['feedback'][0]['text']=='只看表情，不是失焦'
    assert a.screening_reason is None
    page.pane.native();root.update();assert page.pane.viewport.scale==pytest.approx(1)
    page.tree.selection_set('0','1');page._compare();root.update()
    windows=[w for w in page.winfo_children() if isinstance(w,ComparisonWindow)]
    assert len(windows)==1
    monkeypatch.setattr(page.store,'record_feedback',lambda *a,**k:(_ for _ in ()).throw(OSError('disk full')))
    page.note.delete('1.0','end');page.note.insert('1.0','unsaved');page._save_feedback('question')
    assert errors and '未保存' in page.status.get() and 'unsaved' in page.note.get('1.0','end')
    page.destroy()


def test_source_changed_or_dirty_never_projects_old_regions(tmp_path):
    from ai_cull_assistant.image_decode import asset_source_signature
    import json
    a=asset(tmp_path);decode={'source_path':str(a.primary_path),'source_kind':'standard_image','source_size':[80,60]}
    a.clarity_evidence={'display_regions':[dict(decode,rect=[.1,.1,.3,.3],coordinate_space='normalized_display')],
        'measurement_inputs':{'source_signature':json.loads(json.dumps(asset_source_signature(a)))}}
    assert recorded_overlays(a,decode)
    a.ai_focus_dirty=True
    assert recorded_overlays(a,decode)==[]
    a.ai_focus_dirty=False
    a.primary_path.write_bytes(b'changed source')
    assert recorded_overlays(a,decode)==[]


@pytest.mark.parametrize('reason,notes,expected',[
    ('decode_unavailable',[],'failed'),('screening_disabled',[],'not_run'),
    ('subject_not_obviously_blurred',['source_unavailable'],'failed'),
    ('subject_not_obviously_blurred',['source_changed'],'review')])
def test_real_production_failure_and_restored_source_states(tmp_path,reason,notes,expected):
    a=asset(tmp_path);a.screening_reason=reason;a.notes=notes
    assert result_state(a)==expected


def test_stars_only_action_does_not_confirm_keep(tmp_path):
    a=asset(tmp_path);calls=[]
    class Var:
        def __init__(self,value=None):self.value=value
        def get(self):return self.value
        def set(self,value):self.value=value
    page=SimpleNamespace(selected=a,pick=Var('沿用建议（不覆盖）'),rating=Var('4'),status=Var(),
        on_decision=lambda *args,**kwargs:calls.append((args,kwargs)),_load=lambda:None,_detail=lambda:None,
        _error=lambda *args:pytest.fail(str(args)))
    ScanReviewPage._decision(page)
    assert calls[0][0]==(a,None,4)
    page.rating.set('清除星级');ScanReviewPage._decision(page)
    assert calls[1][0]==(a,None,0)


def test_apply_callback_disposes_page_without_false_failure(tmp_path,monkeypatch):
    a=asset(tmp_path);calls=[]
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askokcancel',lambda *a,**k:True)
    class Var:
        def get(self):return 'face'
        def set(self,value):pytest.fail('Touched status after callback destroyed page')
    page=SimpleNamespace(selected=a,kind=Var(),region={'box':[.1,.1,.5,.5]},status=Var(),
        _page_alive=lambda:False,_load=lambda:pytest.fail('Reloaded disposed page'),
        _detail=lambda:pytest.fail('Touched disposed detail'),_error=lambda *a:pytest.fail('False failure'))
    page.on_apply_scope=lambda *args:calls.append(args)
    ScanReviewPage._apply(page)
    assert len(calls)==1


def test_real_tk_apply_callback_destroys_page(root,tmp_path,monkeypatch):
    a=asset(tmp_path);workspace=tmp_path/'workspace';workspace.mkdir()
    errors=[]
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askokcancel',lambda *a,**k:True)
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.showerror',lambda *a,**k:errors.append(a))
    page=ScanReviewPage(root,[a],workspace);page.pack(fill='both',expand=True);root.update()
    decode=page.pane.frame.image.info['decode_evidence']
    page._region_drawn(dict(box=[.1,.1,.5,.5],coordinate_space='normalized_display',preview_version='v05',
        source_path=decode['source_path'],source_kind=page.pane.frame.source_kind,
        source_size=list(page.pane.frame.image.size),source_signature=page.pane._loaded_signature))
    page._save_feedback('region');page.on_apply_scope=lambda *args:page.destroy()
    page._apply();root.update()
    assert not errors and not page.winfo_exists()


def test_load_inspection_frame_rejects_source_changed_during_decode(tmp_path,monkeypatch):
    from ai_cull_assistant import scan_review_viewer as viewer
    from ai_cull_assistant.image_viewer import DisplayFrame
    a=asset(tmp_path);closed=[]
    class Frame:
        def close(self):closed.append(True)
    def load(*args,**kwargs):
        a.primary_path.write_bytes(b'replaced while decoding')
        return Frame()
    monkeypatch.setattr(viewer,'load_source',load)
    with pytest.raises(ValueError,match='来源在读取期间变化'):viewer.load_inspection_source(a)
    assert closed


def test_draft_is_bound_to_loaded_signature_not_later_file(tmp_path,monkeypatch):
    from ai_cull_assistant.scan_review_viewer import load_inspection_source
    from ai_cull_assistant.image_decode import asset_source_signature
    from ai_cull_assistant.image_viewer import Viewport
    import json
    a=asset(tmp_path);frame,signature=load_inspection_source(a)
    a.primary_path.write_bytes(b'replaced after loading')
    assert signature != json.loads(json.dumps(asset_source_signature(a)))
    drafts=[]
    pane=SimpleNamespace(_drag=(10,10),draft_mode=SimpleNamespace(get=lambda:True),frame=frame,
        viewport=Viewport(width=80,height=60,image_width=80,image_height=60),
        _loaded_signature=signature,on_region=drafts.append)
    InspectionPane._release(pane,SimpleNamespace(x=40,y=40))
    assert drafts[0]['source_signature']==signature
    frame.close()


def test_empty_filter_clears_action_target_without_tk():
    class Tree:
        def get_children(self):return ()
        def delete(self,*args):pass
    class Text:
        def configure(self,**kwargs):pass
        def delete(self,*args):pass
        def insert(self,*args):pass
    page=SimpleNamespace(selected=object(),assets=[],tree=Tree(),filter_var=SimpleNamespace(get=lambda:'建议保留'),
        count=SimpleNamespace(configure=lambda **kwargs:None),pane=None,_new_feedback=lambda:None,
        evidence=Text(),feedback_list=SimpleNamespace(configure=lambda **kwargs:None),
        status=SimpleNamespace(set=lambda value:None))
    ScanReviewPage._populate(page)
    assert page.selected is None


def test_real_tk_no_results_does_not_target_hidden_photo(root,tmp_path):
    a=asset(tmp_path);workspace=tmp_path/'workspace';workspace.mkdir()
    decisions=[]
    page=ScanReviewPage(root,[a],workspace,on_decision=lambda *a,**k:decisions.append(a))
    page.pack(fill='both',expand=True);root.update()
    assert page.selected is a
    page.filter_var.set('建议保留');page._populate();root.update()
    assert page.selected is None and page.pane is None
    page._decision();assert not decisions
    page.destroy()


def test_optional_corrupt_files_do_not_block_scan_population(tmp_path):
    from ai_cull_assistant.scan_review_store import ScanReviewStore
    workspace=tmp_path/'workspace';workspace.mkdir()
    store=ScanReviewStore(workspace)
    # Discover the real path from a real save, then corrupt just that file.
    a=asset(tmp_path);store.record_feedback(a,kind='question',text='original')
    path=next(p for p in workspace.glob('*.json') if 'scan-review' in p.name)
    path.write_text('{broken',encoding='utf-8');before=path.read_bytes()
    calls=[]
    page=SimpleNamespace(store=store,on_read_decisions=lambda:(_ for _ in ()).throw(ValueError('AI JSON corrupt')),
        _update_read_health=lambda:None,_populate=lambda:calls.append('populated'))
    page._load=lambda:ScanReviewPage._load(page)
    ScanReviewPage.refresh_assets(page,[a])
    assert page.assets==[a] and calls==['populated']
    assert set(page._read_errors)=={'反馈记录','人工／AI项目'}
    assert path.read_bytes()==before
    page._update_read_health=lambda:calls.append('blocked')
    assert ScanReviewPage._can_mutate(page) is False
    assert calls[-1]=='blocked'


def test_successful_reread_clears_degraded_state(tmp_path):
    from ai_cull_assistant.scan_review_store import ScanReviewStore
    page=SimpleNamespace(store=ScanReviewStore(tmp_path),on_read_decisions=lambda:{'id':{}},
        _read_errors={'old':'failure'},_update_read_health=lambda:None)
    ScanReviewPage._load(page)
    assert page._read_errors=={} and page._rows=={'id':{}}


def test_cancel_pending_recheck_confirmation_and_disposed_callback(tmp_path,monkeypatch):
    a=asset(tmp_path);calls=[]
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askokcancel',lambda *a,**k:False)
    page=SimpleNamespace(selected=a,on_cancel_recheck=lambda asset:calls.append(asset),_pending_recheck=lambda:True,
        _page_alive=lambda:False,_load=lambda:pytest.fail('Reloaded disposed page'),_error=lambda *a:pytest.fail(str(a)))
    ScanReviewPage._cancel_recheck(page);assert not calls
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askokcancel',lambda *a,**k:True)
    ScanReviewPage._cancel_recheck(page);assert calls==[a]


def test_cancel_recheck_guard_requires_current_pending_record(tmp_path):
    from ai_cull_assistant.scan_review_store import asset_key
    a=asset(tmp_path)
    page=SimpleNamespace(selected=a,_document={'photos':{asset_key(a):{'rechecks':[{'status':'completed'}]}}})
    assert not ScanReviewPage._pending_recheck(page)
    page._document['photos'][asset_key(a)]['rechecks'].append({'status':'pending'})
    assert ScanReviewPage._pending_recheck(page)
    page.selected=None;assert not ScanReviewPage._pending_recheck(page)


def test_real_tk_draft_cancel_switch_filter_and_failed_save(root,tmp_path,monkeypatch):
    a=asset(tmp_path);b=deepcopy(a);b.primary_path=tmp_path/'B.jpg';b.jpg_path=b.display_path=b.primary_path
    Image.new('RGB',(80,60),'blue').save(b.primary_path)
    workspace=tmp_path/'workspace';workspace.mkdir()
    page=ScanReviewPage(root,[a,b],workspace);page.pack(fill='both',expand=True);root.update()
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askyesnocancel',lambda *a,**k:None)
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.showerror',lambda *a,**k:None)
    page.note.insert('1.0','请看手部')
    page.tree.selection_set('1');root.update()
    assert page.selected is a and page.tree.selection()==('0',) and '请看手部' in page.note.get('1.0','end')
    page.filter_var.set('建议保留');page._populate();root.update()
    assert page.selected is a and page.filter_var.get()==FILTERS[0]
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askyesnocancel',lambda *a,**k:True)
    monkeypatch.setattr(page.store,'record_feedback',lambda *a,**k:(_ for _ in ()).throw(OSError('disk full')))
    page.tree.selection_set('1');root.update()
    assert page.selected is a and '请看手部' in page.note.get('1.0','end')
    assert not page.store.photo(a)
    page.destroy()
