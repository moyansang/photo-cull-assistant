from types import SimpleNamespace, MethodType
import pytest
from ai_cull_assistant.scan_review_viewer import InspectionPane,load_inspection_source
from ai_cull_assistant.image_viewer import Viewport
from test_scan_review_ui import asset, root

class Canvas:
    def __init__(self):self.items=[]
    def delete(self,tag):
        if tag=='all':self.items=[]
        else:self.items=[i for i in self.items if i[2].get('tags')!=tag]
    def create_image(self,*a,**kw):self.items.append(('image',a,kw))
    def create_rectangle(self,*a,**kw):self.items.append(('rect',a,kw))
    def create_text(self,*a,**kw):self.items.append(('text',a,kw))
    def create_polygon(self,*a,**kw):self.items.append(('polygon',a,kw))

@pytest.fixture
def pane(tmp_path,monkeypatch):
    a=asset(tmp_path);frame,signature=load_inspection_source(a)
    p=SimpleNamespace(asset=a,frame=frame,_loaded_signature=signature,attention_region=None,
        canvas=Canvas(),viewport=Viewport(width=160,height=120,image_width=80,image_height=60),
        status=SimpleNamespace(set=lambda text:None),_drag=None,draft_mode=SimpleNamespace(get=lambda:False))
    for name in ('attention_is_current','set_attention_region','_canvas_point','render','fit','native','_zoom','_resize','_move','_release'):
        setattr(p,name,MethodType(getattr(InspectionPane,name),p))
    monkeypatch.setattr('ai_cull_assistant.scan_review_viewer.ImageTk.PhotoImage',lambda *a,**kw:object())
    yield p
    frame.close()

def region(p):
    decode=p.frame.image.info['decode_evidence']
    return dict(box=[.1,.2,.6,.7],coordinate_space='normalized_display',preview_version='v05',
        source_path=decode['source_path'],source_kind=p.frame.source_kind,source_size=list(p.frame.image.size),source_signature=p._loaded_signature)

def draft(p):return [i for i in p.canvas.items if i[2].get('tags')=='draft']

def test_real_viewport_redraw_native_zoom_pan_resize_and_invalid_drag(pane):
    p=pane;r=region(p);assert p.set_attention_region(r);assert len(draft(p))==1
    for transition in (p.native,lambda:p._zoom(1.25,40,30),lambda:p._resize(SimpleNamespace(width=300,height=200)),p.fit):
        transition();assert len(draft(p))==1 and p.attention_region==r
        assert draft(p)[0][1]==(*p._canvas_point(.1,.2),*p._canvas_point(.6,.7))
    p._drag=(30,20);p._move(SimpleNamespace(x=60,y=40));assert len(draft(p))==1
    p.draft_mode=SimpleNamespace(get=lambda:True);p._drag=(10,10)
    p._move(SimpleNamespace(x=10,y=10));p._release(SimpleNamespace(x=10,y=10))
    assert len(draft(p))==1 and p.attention_region==r
    p.set_attention_region(None);assert not draft(p)

def test_changed_source_never_draws_old_attention(pane):
    p=pane;assert p.set_attention_region(region(p));p.asset.primary_path.write_bytes(b'changed')
    p.render();assert not p.attention_is_current() and not draft(p)

@pytest.mark.parametrize('field,value',[('source_size',[800,600]),('source_kind','raw_full_decode'),('source_path','elsewhere'),('source_signature',None),('preview_version','old')])
def test_history_region_source_binding(pane,field,value):
    r=region(pane);r[field]=value
    assert not pane.set_attention_region(r) and not draft(pane)

def test_real_tk_attention_layer_survives_view_changes(root,tmp_path):
    p=InspectionPane(root,asset(tmp_path),on_region=lambda r:None);p.pack(fill='both',expand=True);root.update()
    assert p.set_attention_region(region(p));root.update()
    for transition in (p.native,p.fit,lambda:p._zoom(1.25,40,30)):
        transition();root.update();assert len(p.canvas.find_withtag('draft'))==1
    p.set_attention_region(None);assert not p.canvas.find_withtag('draft');p.destroy()

@pytest.mark.parametrize('box',[['a',0,1,1],[False,0,1,1],[0,0,float('nan'),1],[0,0,0,1],None])
def test_invalid_history_coordinates_are_unavailable_not_crashes(pane,box):
    r=region(pane);r['box']=box
    assert not pane.set_attention_region(r) and not draft(pane)
