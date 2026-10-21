"""Read-only comparison and attention drafts; no scoring or source writes."""
from __future__ import annotations
import math
from copy import deepcopy
import json
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk
from .image_viewer import Viewport, load_source, SOURCE_LABELS


def valid_rect(value):
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return False
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        return False
    x, y, w, h = value
    return 0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x+w <= 1.000001 and y+h <= 1.000001


def recorded_overlays(asset, decode):
    """Missing inverse transforms remain missing, never reconstructed."""
    evidence = getattr(asset, 'clarity_evidence', None) or {}
    result = []
    if getattr(asset, 'ai_focus_dirty', False):
        return result
    recorded_signature = (evidence.get('measurement_inputs') or {}).get('source_signature')
    if recorded_signature is not None:
        from .image_decode import asset_source_signature
        if json.loads(json.dumps(asset_source_signature(asset))) != recorded_signature:
            return result
    recorded = evidence.get('decode') or {}
    same_source = bool(recorded.get('source_path')) and all(
        recorded.get(k) == decode.get(k) for k in ('source_path', 'source_kind', 'source_size'))
    # These normalized boxes were saved by the production measurement. They
    # describe selected faces, NOT measured eye bands or hand regions.
    adapted = list(evidence.get('display_regions', []))
    if same_source:
        policy = evidence.get('primary_quality') or {}
        confirmed_ids = {item.get('id') for item in policy.get('subjects', [])}
        for subject in evidence.get('subjects', []):
            if subject.get('evidence_kind') != 'face':
                continue
            confirmed = (policy.get('selection_origin') == 'human'
                         and subject.get('subject_id') in confirmed_ids)
            adapted.append(dict(recorded, rect=subject.get('normalized_box'),
                coordinate_space='normalized_display', role='confirmed' if confirmed else 'selected',
                label='人工确认人物范围（非测量子区）' if confirmed else '实际选用人物（非人工确认）'))
        automatic = evidence.get('automatic_subject') or {}
        provenance = automatic.get('candidate_provenance') or {}
        if provenance.get('coordinate_space') == 'normalized_exif_oriented':
            for candidate in provenance.get('initial_candidates', []):
                adapted.append(dict(recorded, rect=candidate.get('box'), coordinate_space='normalized_display',
                                    role='candidate', label='检测候选（不等于主体确认）'))
    for item in adapted:
        if not isinstance(item, dict):
            continue
        if (item.get('coordinate_space') not in ('normalized_display', 'normalized_exif_oriented')
                or not item.get('source_path') or item.get('source_path') != decode.get('source_path')
                or item.get('source_kind') != decode.get('source_kind')
                or item.get('source_size') != decode.get('source_size')):
            continue
        points = item.get('polygon')
        if (isinstance(points, list) and len(points) >= 3
                and all(isinstance(p, (list, tuple)) and len(p) == 2
                        and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in p)
                        for p in points)):
            result.append(dict(item, polygon=points))
        elif valid_rect(item.get('rect')):
            x,y,w,h=item['rect']
            result.append(dict(item,polygon=[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]))
    return result


def load_inspection_source(asset):
    """Bind an inspection frame and every derived draft to the loaded members."""
    from .image_decode import asset_source_signature
    before=json.loads(json.dumps(asset_source_signature(asset)))
    saved_decode=(getattr(asset,'clarity_evidence',None) or {}).get('decode') or {}
    frame=load_source(asset,full_raw=saved_decode.get('source_kind')=='raw_full_decode')
    after=json.loads(json.dumps(asset_source_signature(asset)))
    if before != after:
        frame.close()
        raise ValueError('来源在读取期间变化，请重新打开；旧框不可应用')
    return frame,before


class InspectionPane(ttk.Frame):
    def __init__(self,parent,asset,*,on_region=None):
        super().__init__(parent)
        self.asset,self.on_region=asset,on_region
        self.attention_region=None
        self.frame=None; self.viewport=Viewport(); self.photo=None; self._drag=None
        self.draft_mode=tk.BooleanVar(value=False)
        bar=ttk.Frame(self);bar.pack(fill='x')
        ttk.Button(bar,text='整体',command=self.fit).pack(side='left')
        ttk.Button(bar,text='原生 100%',command=self.native).pack(side='left')
        if on_region:
            ttk.Checkbutton(bar,text='框选关注区域（草稿）',variable=self.draft_mode).pack(side='left')
        self.canvas=tk.Canvas(self,background='#20242a',highlightthickness=0,width=360,height=280)
        self.canvas.pack(fill='both',expand=True)
        self.status=tk.StringVar(value='只读打开对应来源…')
        ttk.Label(self,textvariable=self.status,wraplength=460).pack(fill='x')
        self.canvas.bind('<Configure>',self._resize)
        self.canvas.bind('<ButtonPress-1>',self._press)
        self.canvas.bind('<B1-Motion>',self._move)
        self.canvas.bind('<ButtonRelease-1>',self._release)
        self.canvas.bind('<MouseWheel>',lambda e:self._zoom(1.25 if e.delta>0 else .8,e.x,e.y))
        self.canvas.bind('<Button-4>',lambda e:self._zoom(1.25,e.x,e.y))
        self.canvas.bind('<Button-5>',lambda e:self._zoom(.8,e.x,e.y))
        try:
            self.frame,self._loaded_signature=load_inspection_source(asset)
            self.viewport.set_image(self.frame.image.size);self.render()
        except Exception as exc:
            self.status.set(f'原图不可读：{type(exc).__name__}；原记录保留')
        self.bind('<Destroy>',self._destroyed,add='+')

    def attention_is_current(self):
        region=self.attention_region
        if not isinstance(region,dict) or not self.frame:return False
        from .image_decode import asset_source_signature
        decode=self.frame.image.info.get('decode_evidence') or {}
        box=region.get('box')
        return (isinstance(box,(list,tuple)) and len(box)==4
            and all(type(v) in (int,float) and math.isfinite(v) for v in box)
            and valid_rect([box[0],box[1],box[2]-box[0],box[3]-box[1]])
            and region.get('coordinate_space')=='normalized_display'
            and region.get('preview_version')=='v05'
            and region.get('source_path')==decode.get('source_path')
            and region.get('source_kind')==self.frame.source_kind
            and region.get('source_size')==list(self.frame.image.size)
            and region.get('source_signature')==self._loaded_signature
            and self._loaded_signature==json.loads(json.dumps(asset_source_signature(self.asset))))
    def set_attention_region(self,region):
        self.attention_region=deepcopy(region)
        self.render()
        return self.attention_is_current()
    def _canvas_point(self,x,y):
        return ((x-self.viewport.center_x)*self.viewport.image_width*self.viewport.scale+self.viewport.width/2,
                (y-self.viewport.center_y)*self.viewport.image_height*self.viewport.scale+self.viewport.height/2)

    def _destroyed(self,event):
        if event.widget is self and self.frame:
            self.frame.close();self.frame=None
    def _resize(self,event):
        self.viewport.resize(event.width,event.height);self.render()
    def fit(self):
        self.viewport.fit();self.render()
    def native(self):
        self.viewport.native();self.render()
    def _zoom(self,factor,x,y):
        self.viewport.zoom_at(factor,x,y);self.render()
    def _press(self,event):
        self._drag=(event.x,event.y)
    def _move(self,event):
        if self._drag is None:return
        if self.draft_mode.get():
            self.canvas.delete('draft')
            self.canvas.create_rectangle(*self._drag,event.x,event.y,outline='#e99e3d',width=2,tags='draft')
        else:
            x,y=self._drag;self.viewport.pan(event.x-x,event.y-y);self._drag=(event.x,event.y);self.render()
    def _release(self,event):
        if self._drag and self.draft_mode.get() and self.frame:
            a=self.viewport.image_point(*self._drag);b=self.viewport.image_point(event.x,event.y)
            x,y=max(0,min(a[0],b[0])),max(0,min(a[1],b[1]))
            rect=[x,y,min(1,max(a[0],b[0]))-x,min(1,max(a[1],b[1]))-y]
            if valid_rect(rect) and min(rect[2:])>.001:
                decode=self.frame.image.info.get('decode_evidence') or {}
                self.on_region(dict(box=[x,y,x+rect[2],y+rect[3]],coordinate_space='normalized_display',preview_version='v05',source_path=decode.get('source_path'),
                    source_kind=self.frame.source_kind,source_size=list(self.frame.image.size),
                    source_signature=self._loaded_signature))
        self._drag=None
        if hasattr(self,'render'):self.render()
    def render(self):
        if not self.frame:return
        rendered=self.viewport.render(self.frame.image)
        self.photo=ImageTk.PhotoImage(rendered,master=self.canvas);rendered.close()
        self.canvas.delete('all');self.canvas.create_image(0,0,image=self.photo,anchor='nw')
        decode=self.frame.image.info.get('decode_evidence') or {};overlays=recorded_overlays(self.asset,decode)
        for item in overlays:
            points=[]
            for x,y in item['polygon']:
                points.extend(((x-self.viewport.center_x)*self.viewport.image_width*self.viewport.scale+self.viewport.width/2,
                               (y-self.viewport.center_y)*self.viewport.image_height*self.viewport.scale+self.viewport.height/2))
            color={'candidate':'#e0bc56','selected':'#49b8ff','confirmed':'#66d391'}.get(item.get('role'),'#d286ed')
            self.canvas.create_polygon(*points,outline=color,fill='',width=2)
            self.canvas.create_text(*points[:2],text=item.get('label','已记录范围'),anchor='sw',fill=color)
        if self.attention_is_current():
            x1,y1,x2,y2=self.attention_region['box']
            start=self._canvas_point(x1,y1);end=self._canvas_point(x2,y2)
            self.canvas.create_rectangle(*start,*end,outline='#e99e3d',width=3,dash=(6,3),tags='draft')
            self.canvas.create_text(*start,text='关注草稿（非测量范围）',anchor='sw',fill='#e99e3d',tags='draft-label')
        w,h=self.frame.image.size
        self.status.set(f'{self.asset.stem} · {SOURCE_LABELS.get(self.frame.source_kind,self.frame.source_kind)} · '
            f'{w}×{h} 实际像素 · {self.viewport.scale*100:.0f}%\n{decode.get("source_path","来源成员未记录")}\n'
            +('历史已记录范围（来源尺寸匹配）；候选≠人工确认' if overlays else '无可映射的历史测量范围；不按当前参数补画')+'；放大不增加细节'+('；关注区域已过期／来源不可验证，禁止应用' if self.attention_region and not self.attention_is_current() else ''))
        notify=getattr(self,'on_attention_state',None)
        if notify:notify()


class ComparisonWindow(tk.Toplevel):
    def __init__(self,parent,assets):
        super().__init__(parent);self.title('同组并排比较 · 人工浏览');self.geometry('1100x650')
        ttk.Label(self,text='主体对应关系未确认：仅人工浏览，不生成相对技术结论；各图独立缩放与拖动。').pack(fill='x')
        panes=ttk.Panedwindow(self,orient='horizontal');panes.pack(fill='both',expand=True)
        for asset in assets[:2]:
            panes.add(InspectionPane(panes,asset),weight=1)
