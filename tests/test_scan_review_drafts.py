"""Exercise editor transitions without a display; Tk rendering is covered separately."""
from copy import deepcopy
from types import SimpleNamespace, MethodType
import pytest
from ai_cull_assistant.scan_review_ui import ScanReviewPage
from ai_cull_assistant.scan_review_store import ScanReviewStore
from test_scan_review_ui import asset

class Value:
    def __init__(self,value=''):self.value=value
    def get(self,*args):return self.value
    def set(self,value):self.value=value
    def delete(self,*args):self.value=''
    def insert(self,index,value,**kw):self.value=value
    def configure(self,**kw):pass
    def current(self,index=None):
        if index is not None:self.index=index
        return getattr(self,'index',-1)

class Tree:
    def __init__(self):self.ids=['0','1'];self.selection_ids=('0',)
    def selection(self):return self.selection_ids
    def selection_set(self,*ids):self.selection_ids=ids
    def get_children(self):return self.ids
    def delete(self,*args):self.ids=[]
    def insert(self,*args,iid,**kwargs):self.ids.append(iid)

class Pane:
    def __init__(self,*a,**kw):self.region=None;self.destroyed=False
    def set_attention_region(self,region):self.region=deepcopy(region);return bool(region)
    def attention_is_current(self):return bool(self.region)
    def destroy(self):self.destroyed=True
    def pack(self,**kw):pass

@pytest.fixture
def page(tmp_path,monkeypatch):
    a=asset(tmp_path);b=deepcopy(a);b.stem='B';b.primary_path=tmp_path/'b.jpg';b.primary_path.write_bytes(a.primary_path.read_bytes())
    b.jpg_path=b.primary_path;b.display_path=b.primary_path
    p=SimpleNamespace(selected=a,assets=[a,b],store=ScanReviewStore(tmp_path/'workspace'),
        region=None,feedback_id=None,feedback_items=[],pane=Pane(),note=Value(),kind=Value('face'),
        feedback_list=Value(),region_label=Value(),status=Value(),filter_var=Value('全部结果'),
        tree=Tree(),count=Value(),evidence=Value(),image_host=None,_read_errors={},_document={},_rows={},
        _update_read_health=lambda:None,on_read_decisions=None,pick=Value(),rating=Value(),
        errors=[],_error=lambda *a:None)
    for name in ('_editor_state','can_leave','_reset_feedback','_new_feedback','_sync_region','_select',
                 '_populate','_edit_feedback','_save_feedback','_withdraw_feedback','_load','_detail','_region_drawn','_leave_for'):
        setattr(p,name,MethodType(getattr(ScanReviewPage,name),p))
    p._error=lambda *args:p.errors.append(args)
    p._saved_editor=p._editor_state();p._filter_value=p.filter_var.get()
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.InspectionPane',Pane)
    return p

@pytest.mark.parametrize('action',['select','filter','new','history','leave'])
def test_cancel_keeps_original_photo_text_region_and_history(page,monkeypatch,action):
    p=page;p.note.insert('1.0','请看手部');p._region_drawn({'box':[.1,.1,.4,.4]})
    p.feedback_items=[{'id':'old','kind':'question','text':'历史'}]
    before=p._editor_state();original=p.selected;calls=[]
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askyesnocancel',lambda *a,**k:None)
    if action=='select':p.tree.selection_set('1');p._select()
    elif action=='filter':p.filter_var.set('建议保留');p._populate();assert p.filter_var.get()=='全部结果'
    elif action=='new':p._new_feedback()
    elif action=='history':p.feedback_list.current(0);p._edit_feedback()
    else:p._leave_for(lambda:calls.append(True))
    assert p.selected is original and p._editor_state()==before and not calls
    assert p.tree.selection()==('0',)
    assert (p.store.photo(original) or {}).get('feedback',[])==[]


def test_failed_save_on_switch_preserves_draft_and_original_target(page,monkeypatch):
    p=page;p.note.insert('1.0','请看手部');before=p._editor_state()
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askyesnocancel',lambda *a,**k:True)
    monkeypatch.setattr(p.store,'record_feedback',lambda *a,**k:(_ for _ in ()).throw(OSError('disk full')))
    p.tree.selection_set('1');p._select()
    assert p.selected is p.assets[0] and p._editor_state()==before and p.errors


def test_save_then_switch_persists_feedback_only(page,monkeypatch):
    p=page;p.note.insert('1.0','请看手部');a=p.selected
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askyesnocancel',lambda *a,**k:True)
    p.tree.selection_set('1');p._select()
    assert p.selected is p.assets[1] and not p.note.get()
    assert p.store.photo(a)['feedback'][0]['text']=='请看手部'
    assert a.screening_reason is None and not p._rows


def test_discard_new_and_withdraw_clear_actual_and_visible_region(page,monkeypatch):
    p=page;p.note.insert('1.0','问题');p._save_feedback('question')
    p.note.insert('1.0','修改')
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askyesnocancel',lambda *a,**k:False)
    p._withdraw_feedback()
    assert p.region is None and p.pane.region is None and p.feedback_id is None
    assert p.store.photo(p.selected)['feedback'][0]['withdrawn']
    p._region_drawn({'box':[.1,.1,.4,.4]});p._new_feedback()
    assert p.region is None and p.pane.region is None


def test_history_restores_attention_and_withdrawn_history_does_not(page):
    p=page;region={'box':[.1,.1,.4,.4],'kind':'face'}
    p.feedback_items=[{'id':'one','kind':'region','text':'范围','region':region}]
    p.feedback_list.current(0);p._edit_feedback()
    assert p.region==p.pane.region==region
    p.feedback_items[0]['withdrawn']=True;p._edit_feedback()
    assert p.region is None and p.pane.region is None


def test_question_save_cannot_mark_unpersisted_region_saved(page,monkeypatch):
    p=page;p.note.insert('1.0','文字');assert p._save_feedback('question')
    saved=p._saved_editor;p._region_drawn({'box':[.1,.1,.4,.4]})
    assert not p._save_feedback('question') and p._saved_editor==saved
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askyesnocancel',lambda *a,**k:True)
    assert not p.can_leave() and p.region and p._editor_state()!=p._saved_editor


def test_apply_discard_resolves_target_before_confirmation(page,monkeypatch):
    p=page;p.on_apply_scope=lambda *a:pytest.fail('Must not apply discarded region')
    p._region_drawn({'box':[.1,.1,.4,.4]});messages=[]
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askyesnocancel',lambda *a,**k:False)
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askokcancel',lambda *a,**k:pytest.fail('No retained target'))
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.showinfo',lambda *a,**k:messages.append(a))
    ScanReviewPage._apply(p)
    assert p.region is None and p.pane.region is None and messages


def test_application_navigation_cancel_keeps_visible_page(page):
    from ai_cull_assistant.app import App
    p=page;p.note.insert('1.0','未保存');calls=[]
    p.winfo_exists=lambda:True;p.can_leave=lambda:False
    home=SimpleNamespace(winfo_exists=lambda:True,grid_remove=lambda:calls.append('hidden'))
    app=SimpleNamespace(_home_page=home,_workspace_pages={'scan_review':p},_visible_page='scan_review')
    assert App._show_workspace_page(app,'home') is True
    assert not calls and app._visible_page=='scan_review'


def test_application_destructive_guard_prevents_start_and_clear(page):
    from ai_cull_assistant.app import App
    p=page;p.winfo_exists=lambda:True;p.can_leave=lambda:False
    app=SimpleNamespace(_workspace_pages={'scan_review':p})
    app._confirm_scan_feedback_leave=MethodType(App._confirm_scan_feedback_leave,app)
    assert not app._confirm_scan_feedback_leave()
    # Returning before reading any processing/clear state proves cancellation is
    # before the production entry points can mutate or dispose the editor.
    App._start_processing(app);App._clear_workspace(app)


def test_apply_discard_confirms_and_uses_retained_saved_region(page,monkeypatch):
    p=page;old={'box':[.1,.1,.4,.4]};p._region_drawn(old);assert p._save_feedback('region')
    p._region_drawn({'box':[.2,.2,.6,.6]});calls=[];confirmed=[]
    p.on_apply_scope=lambda asset,region:calls.append(region);p._page_alive=lambda:False
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askyesnocancel',lambda *a,**k:False)
    monkeypatch.setattr('ai_cull_assistant.scan_review_ui.messagebox.askokcancel',lambda title,text,**kw:confirmed.append(text) or True)
    ScanReviewPage._apply(p)
    assert calls==[dict(old,kind='face')] and str(old['box']) in confirmed[0]
