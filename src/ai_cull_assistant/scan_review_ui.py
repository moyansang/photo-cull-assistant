"""Local scan evidence, small corrections and human culling in one surface."""
from __future__ import annotations
import json
from copy import deepcopy
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from .ui_page import WorkspacePage
from .responsive_layout import WrappingToolbar, ScrollableFrame
from .group_editor import grouping_reason_items, grouping_review_stage
from .lightroom_results import local_recommendation, current_focus, current_selection, effective_decision
from .ai_project import photo_id
from .scan_review_store import ScanReviewStore, asset_key
from .scan_review_viewer import InspectionPane, ComparisonWindow

LABELS={'keep':'建议保留','reject':'建议弃置','review':'待确认','failed':'失败','not_run':'未执行'}
FILTERS=('弃置和待确认','全部结果','建议保留','建议弃置','待确认','失败／未执行','当前同组')


def result_state(asset):
    """Keep execution failures separate from a technical verdict."""
    reason=getattr(asset,'screening_reason',None) or ''
    notes=getattr(asset,'notes',[]) or []
    if reason=='screening_disabled':return 'not_run'
    if 'source_unavailable' in notes or reason in ('decode_unavailable','source_unavailable'):return 'failed'
    if 'source_changed' in notes or reason=='source_changed':return 'review'
    if any(code in reason for code in ('unreadable','decode_failed','processing_failed','analysis_failed')):
        return 'failed'
    if not getattr(asset,'clarity_evidence',None) and not reason and not getattr(asset,'clarity_version',None):
        return 'not_run'
    return local_recommendation(asset)


def evidence_text(asset,row=None,review=None):
    row=row or {};review=review or {}
    evidence=getattr(asset,'clarity_evidence',None) or {}
    state=result_state(asset)
    final=row.get('final') or {}
    focus=current_focus(row) if row else None
    selection=current_selection(row) if row else None
    lines=[f'本地技术：{LABELS[state]}（不被人工决定洗掉）',grouping_review_stage(asset),
           f'结果版本：{getattr(asset,"clarity_version",None) or "历史记录缺项"}',
           f'原始来源成员：{asset.primary_path}',f'RAW：{asset.raw_path or "无"}；JPEG：{asset.jpg_path or "无"}',
           '关联仅沿用扫描已有成员，不按同名文件重新配对。',
           f'AI 技术复核（有效）：{json.dumps(focus,ensure_ascii=False) if focus else "未有有效结果／未开启／已过期"}',
           f'AI 摄影表现（有效）：{json.dumps(selection,ensure_ascii=False) if selection else "未有有效结果／未开启／已过期"}',
           f'人工最终决定：{json.dumps(final,ensure_ascii=False) if final.get("confirmed") else "未确认；无反馈不表示认可"}',
           '', '已保存理由：']
    lines.extend(grouping_reason_items(asset) or ['未记录具体依据；不得补猜历史判定条件。'])
    lines.extend(['','检测候选、实际选用与人工确认分别见原始证据；缺少坐标/来源时不补画。',
                  f'来源状态：{getattr(asset,"notes",[]) or "无额外来源告警；历史时效仍需核对"}',
                  '眼带≠逐眼评分；手部未支持；AF参考≠合焦或主体确认。',
                  '疑似方向性模糊不证明运动成因；指标不是概率。',
                  '', '已保存原始证据（只读）：',json.dumps(evidence,ensure_ascii=False,indent=2,default=str)])
    feature=getattr(asset,'subject_features',None)
    if feature is not None:
        lines.extend(['','检测候选记录（不等于人工确认）：',json.dumps(getattr(feature,'candidate_provenance',None),ensure_ascii=False,indent=2)])
    if review.get('rechecks'):
        for operation in review['rechecks']:
            if operation.get('status') == 'pending' and operation.get('last_error'):
                lines.append('重算失败／中断：未发布新结果；可继续原任务。旧依据仍保留。')
        lines.extend(['','改前／改后与中断状态：',json.dumps(review['rechecks'],ensure_ascii=False,indent=2,default=str)])
    return '\n'.join(lines)


class ScanReviewPage(WorkspacePage):
    def __init__(self,parent,assets,workspace,*,on_apply_scope=None,on_decision=None,
                 on_read_decisions=None,on_groups=None,on_ai_review=None,on_lr_export=None,on_cancel_recheck=None,embedded=True):
        super().__init__(parent,embedded=embedded)
        self.assets=list(assets);self.workspace=workspace;self.store=ScanReviewStore(workspace)
        self.on_apply_scope=on_apply_scope;self.on_cancel_recheck=on_cancel_recheck;self.on_decision=on_decision;self.on_read_decisions=on_read_decisions
        self.on_groups=on_groups;self.on_ai_review=on_ai_review;self.on_lr_export=on_lr_export
        self.selected=None;self.pane=None;self.region=None;self.feedback_id=None;self._rows={};self._document={};self._read_errors={}
        self.filter_var=tk.StringVar(value=FILTERS[0]);self.status=tk.StringVar(value='查看、翻页、筛选与反馈不重算、不上传、不调用 AI。')
        self.kind=tk.StringVar(value='face');self.pick=tk.StringVar(value='沿用建议（不覆盖）');self.rating=tk.StringVar(value='不设置')
        self._build();self._saved_editor=self._editor_state();self._filter_value=self.filter_var.get();self.refresh_assets(assets)
        if not embedded:self.title('扫描结果与依据');self.geometry('1250x850');self.deiconify()

    def _build(self):
        toolbar=WrappingToolbar(self);toolbar.pack(fill='x')
        toolbar.add(ttk.Button(toolbar,text='返回主页',command=self.return_home))
        toolbar.add(ttk.Label(toolbar,text='扫描结果与依据'))
        selector=ttk.Combobox(toolbar,textvariable=self.filter_var,values=FILTERS,state='readonly',width=16)
        toolbar.add(selector);selector.bind('<<ComboboxSelected>>',lambda e:self._populate())
        for label,command in [('组选片／拆合组',self.on_groups),('AI 摄影选片',self.on_ai_review),('Lightroom 导出',self.on_lr_export)]:
            if command:toolbar.add(ttk.Button(toolbar,text=label,command=lambda command=command:self._leave_for(command)))
        toolbar.add(ttk.Button(toolbar,text='重新读取记录',command=lambda:self.refresh_assets(self.assets)))
        self.count=ttk.Label(toolbar);toolbar.add(self.count)
        body=ttk.Panedwindow(self,orient='horizontal');body.pack(fill='both',expand=True)
        left=ttk.Frame(body);body.add(left,weight=1)
        self.tree=ttk.Treeview(left,columns=('state','group'),show='tree headings',selectmode='extended',height=14)
        self.tree.heading('#0',text='照片（Ctrl 选两张比较）');self.tree.heading('state',text='技术');self.tree.heading('group',text='组')
        self.tree.column('#0',width=170);self.tree.column('state',width=80);self.tree.column('group',width=40)
        scroll=ttk.Scrollbar(left,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.tree.bind('<<TreeviewSelect>>',self._select);self.tree.bind('<Double-1>',lambda e:self._fullscreen())
        right_scroll=ScrollableFrame(body);body.add(right_scroll,weight=3)
        right=right_scroll.content
        tabs=ttk.Notebook(right,height=360);tabs.pack(fill='both',expand=True)
        self.image_host=ttk.Frame(tabs);tabs.add(self.image_host,text='整体／原生细节／关注区域')
        evidence_host=ttk.Frame(tabs);tabs.add(evidence_host,text='理由与展开证据／前后对照')
        self.evidence=tk.Text(evidence_host,wrap='word',height=12)
        es=ttk.Scrollbar(evidence_host,command=self.evidence.yview);self.evidence.configure(yscrollcommand=es.set)
        self.evidence.pack(side='left',fill='both',expand=True);es.pack(side='right',fill='y')
        action=ttk.Frame(right);action.pack(fill='x')
        ttk.Button(action,text='并排比较选中两张',command=self._compare).pack(side='left')
        ttk.Button(action,text='同组全屏查看',command=self._fullscreen).pack(side='left')
        feedback=ttk.LabelFrame(right,text='少量纠错：普通照片无需填写；疑问与最终留弃分开');feedback.pack(fill='x')
        self.note=tk.Text(feedback,height=2,wrap='word');self.note.pack(fill='x')
        line=WrappingToolbar(feedback);line.pack(fill='x')
        line.add(ttk.Button(line,text='保存判断疑问',command=lambda:self._save_feedback('question')))
        line.add(ttk.Combobox(line,textvariable=self.kind,values=('face','body','hand','other'),state='readonly',width=7))
        line.add(ttk.Button(line,text='保存关注区域草稿',command=lambda:self._save_feedback('region')))
        line.add(ttk.Button(line,text='应用范围并重新检查',command=self._apply))
        line.add(ttk.Button(line,text='取消未发布的重查',command=self._cancel_recheck,state='disabled'))
        self.region_label=ttk.Label(feedback,text='在图上启用框选；face=脸；body/hand/other仅待支持草稿。');self.region_label.pack(fill='x')
        history=ttk.Frame(feedback);history.pack(fill='x')
        self.feedback_list=ttk.Combobox(history,state='readonly',width=45);self.feedback_list.pack(side='left',fill='x',expand=True)
        self.feedback_list.bind('<<ComboboxSelected>>',self._edit_feedback)
        ttk.Button(history,text='新建反馈',command=self._new_feedback).pack(side='left')
        ttk.Button(history,text='撤回选中反馈',command=self._withdraw_feedback).pack(side='left')
        final_label=ttk.LabelFrame(right,text='人工最终决定（保留技术原状态）');final_label.pack(fill='x')
        final=WrappingToolbar(final_label);final.pack(fill='x')
        final.add(ttk.Combobox(final,textvariable=self.pick,values=('沿用建议（不覆盖）','保留','弃置','不标记'),state='readonly',width=18))
        final.add(ttk.Combobox(final,textvariable=self.rating,values=('不设置','清除星级','1','2','3','4','5'),state='readonly',width=10))
        final.add(ttk.Button(final,text='保存人工决定',command=self._decision))
        final.add(ttk.Button(final,text='撤回人工决定',command=lambda:self._decision(clear=True)))
        final.add(ttk.Button(final,text='导出脱敏诊断',command=lambda:self._export(True)))
        final.add(ttk.Button(final,text='导出本机私有诊断',command=lambda:self._export(False)))
        ttk.Label(self,textvariable=self.status,wraplength=1100).pack(fill='x')

    def _load(self):
        # Corrupt optional state must never hide the scan's input denominator.
        # Empty display data here is NOT writable recovery/default state.
        self._read_errors={}
        try:self._document=self.store.read()
        except Exception as exc:
            self._document={};self._read_errors['反馈记录']=str(exc)
        try:self._rows=self.on_read_decisions() if self.on_read_decisions else {}
        except Exception as exc:
            self._rows={};self._read_errors['人工／AI项目']=str(exc)
        self._update_read_health()
    def _update_read_health(self):
        errors=getattr(self,'_read_errors',{})
        if errors:
            self.status.set('记录读取失败；扫描结果仍可浏览。保存／重算／人工操作已禁用，修复后点重新读取：'
                            +'；'.join(f'{k}: {v}' for k,v in errors.items()))
        blocked={'保存判断疑问','保存关注区域草稿','应用范围并重新检查',
                 '撤回选中反馈','保存人工决定','撤回人工决定','Lightroom 导出','取消未发布的重查'}
        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child,ttk.Button) and child.cget('text') in blocked:
                    enabled=not errors
                    if child.cget('text')=='应用范围并重新检查':
                        enabled=enabled and bool(self.region) and bool(self.pane) and self.pane.attention_is_current()
                    if child.cget('text')=='取消未发布的重查':
                        enabled=enabled and bool(self.on_cancel_recheck) and self._pending_recheck()
                    child.configure(state='normal' if enabled else 'disabled')
                walk(child)
        walk(self)
    def _pending_recheck(self):
        if self.selected is None:return False
        review=self._document.get('photos',{}).get(asset_key(self.selected),{})
        return any(op.get('status')=='pending' for op in review.get('rechecks',[]))
    def _can_mutate(self):
        if getattr(self,'_read_errors',{}):
            self._update_read_health()
            return False
        return True
    def _editor_state(self):
        return (self.note.get('1.0','end').strip(),deepcopy(self.region),self.kind.get(),self.feedback_id)
    def can_leave(self):
        if self.selected is None or self._editor_state()==self._saved_editor:return True
        answer=messagebox.askyesnocancel('尚未保存反馈草稿',
            '保存当前疑问／关注区域后继续？\n是：保存；否：放弃修改；取消：留在当前照片。',parent=self)
        if answer is None:return False
        if answer:
            kind='region' if self.region else 'question'
            if self.feedback_id:
                kind=next((f['kind'] for f in self.feedback_items if f['id']==self.feedback_id),kind)
            return bool(self._save_feedback(kind))
        text,region,kind,feedback_id=self._saved_editor
        self.note.delete('1.0','end');self.note.insert('1.0',text)
        self.region=deepcopy(region);self.kind.set(kind);self.feedback_id=feedback_id
        self._sync_region()
        return True
    def _leave_for(self,command):
        if self.can_leave():return command()
    def return_home(self):
        if self.can_leave():super().return_home()
    def close_page(self):
        if self.can_leave():super().close_page()
    def refresh_assets(self,assets):
        if hasattr(self,'_saved_editor') and not self.can_leave():return False
        self.assets=list(assets)
        self._load()
        if not self._read_errors:
            self.status.set('记录读取成功；查看与反馈不触发重算或 AI。')
        self._populate()
        self._update_read_health()
    def on_activate(self):self.refresh_assets(self.assets)
    def _populate(self):
        if hasattr(self,'_saved_editor') and not self.can_leave():
            self.filter_var.set(self._filter_value)
            return
        self._filter_value=self.filter_var.get()
        selected=self.selected;self.tree.delete(*self.tree.get_children())
        mode=self.filter_var.get();shown=0
        for index,asset in enumerate(self.assets):
            state=result_state(asset)
            visible=(mode=='全部结果' or mode=='弃置和待确认' and state!='keep'
                or mode==LABELS.get(state) or mode=='失败／未执行' and state in ('failed','not_run')
                or mode=='当前同组' and selected is not None and asset.group_id==selected.group_id)
            if visible:self.tree.insert('', 'end',iid=str(index),text=asset.stem,values=(LABELS[state],asset.group_id));shown+=1
        self.count.configure(text=f'显示 {shown} / 全部 {len(self.assets)}')
        children=self.tree.get_children()
        old=next((str(i) for i,a in enumerate(self.assets) if a is selected and str(i) in children),None)
        if children:
            self.tree.selection_set(old or children[0])
        else:
            self.selected=None;self._new_feedback()
            if self.pane:self.pane.destroy();self.pane=None
            self.evidence.configure(state='normal');self.evidence.delete('1.0','end')
            self.evidence.insert('1.0','当前筛选无照片；没有可操作的隐藏照片。');self.evidence.configure(state='disabled')
            self.feedback_items=[];self.feedback_list.configure(values=())
            self.status.set('当前筛选无结果，请更改筛选。')
    def _select(self,_event=None):
        ids=self.tree.selection()
        if not ids:return
        asset=self.assets[int(ids[0])]
        if asset is not self.selected:
            if not self.can_leave():
                old=next((str(i) for i,a in enumerate(self.assets) if a is self.selected),None)
                if old is not None:self.tree.selection_set(old)
                return
            self.selected=asset;self._reset_feedback()
            if self.pane:self.pane.destroy()
            self.pane=InspectionPane(self.image_host,asset,on_region=self._region_drawn);self.pane.pack(fill='both',expand=True)
            self.pane.on_attention_state=self._update_read_health
        self._detail()
    def _detail(self):
        if self.selected is None:return
        review=self._document.get('photos',{}).get(asset_key(self.selected),{})
        row=self._rows.get(photo_id(self.selected),{})
        self.evidence.configure(state='normal');self.evidence.delete('1.0','end')
        self.evidence.insert('1.0',('读取失败（不是空记录）：'+str(self._read_errors)+'\n\n' if self._read_errors else '')+evidence_text(self.selected,row,review));self.evidence.configure(state='disabled')
        self.feedback_items=review.get('feedback',[])
        self.feedback_list.configure(values=[f'{i+1}. {f.get("kind")} {"[已撤回]" if f.get("withdrawn") else ""} {f.get("text","")[:35]}' for i,f in enumerate(self.feedback_items)])
        final=row.get('final') or {}
        self.pick.set({1:'保留',-1:'弃置',0:'不标记'}.get(final.get('pick_status'),'沿用建议（不覆盖）'))
        rating=final.get('rating');self.rating.set('清除星级' if rating==0 else str(rating) if rating in range(1,6) else '不设置')
        self._update_read_health()
    def _region_drawn(self,region):
        self.region=region;self._sync_region()
    def _sync_region(self):
        valid=self.pane.set_attention_region(self.region) if self.pane else False
        text=('关注草稿（非测量范围）：'+str(self.region.get('box')) if valid else '关注区域已过期／来源不可验证；禁止应用，请重新打开原图框选') if self.region else '新反馈；在图上框选后保存，或只写一句疑问。'
        self.region_label.configure(text=text)
        self._update_read_health()
    def _new_feedback(self):
        if not self.can_leave():return False
        self._reset_feedback()
        return True
    def _reset_feedback(self):
        self.feedback_id=None;self.region=None;self.note.delete('1.0','end');self.feedback_list.set('')
        self._sync_region();self._saved_editor=self._editor_state()
    def _edit_feedback(self,_event=None):
        i=self.feedback_list.current()
        if i<0:return
        if not self.can_leave():
            old=next((j for j,f in enumerate(self.feedback_items) if f['id']==self.feedback_id),None)
            if old is None:self.feedback_list.set('')
            else:self.feedback_list.current(old)
            return
        item=self.feedback_items[i];self.feedback_id=item['id'];self.region=None if item.get('withdrawn') else deepcopy(item.get('region'))
        self.note.delete('1.0','end');self.note.insert('1.0',item.get('text',''))
        if self.region:self.kind.set(self.region.get('kind','face'))
        self._sync_region();self._saved_editor=self._editor_state()
    def _save_feedback(self,kind):
        if getattr(self,'_read_errors',{}) and not self._can_mutate():return
        if self.selected is None:return
        try:
            if kind=='region' and not self.region:raise ValueError('请先在图片上框选关注区域。')
            if kind=='question' and self.region:raise ValueError('当前含关注区域；请保存关注区域草稿，或新建纯文字疑问，避免丢失区域。')
            if self.feedback_id and next(f for f in self.feedback_items if f['id']==self.feedback_id)['kind'] != kind:
                raise ValueError('编辑须保持反馈类型；请点新建反馈创建另一类型。')
            region=dict(self.region,kind=self.kind.get()) if kind=='region' else None
            self.store.record_feedback(self.selected,kind=kind,text=self.note.get('1.0','end').strip(),region=region,
                feedback_id=self.feedback_id,expected_revision=self._document.get('revision',0))
            self._load()
            if self.feedback_id is None:
                saved=self._document.get('photos',{}).get(asset_key(self.selected),{}).get('feedback',[])
                if saved:self.feedback_id=saved[-1]['id']
            self._saved_editor=self._editor_state()
            self._detail();self.status.set('反馈已保存；未重算、未改组、未改变人工留弃。');self._update_read_health()
            return True
        except Exception as exc:self._error('反馈未保存',exc)
        return False
    def _withdraw_feedback(self):
        if getattr(self,'_read_errors',{}) and not self._can_mutate():return
        if not self.feedback_id:return
        if not self.can_leave():return
        if not self.feedback_id:return
        item=next(f for f in self.feedback_items if f['id']==self.feedback_id)
        try:
            self.store.record_feedback(self.selected,kind=item['kind'],feedback_id=self.feedback_id,withdraw=True,
                expected_revision=self._document.get('revision',0))
            self._load();self._detail();self._reset_feedback();self.status.set('反馈已撤回，历史保留。');self._update_read_health()
        except Exception as exc:self._error('撤回未保存',exc)
    def _apply(self):
        if getattr(self,'_read_errors',{}) and not self._can_mutate():return
        if self.selected is None or not self.on_apply_scope:return
        if hasattr(self,'_saved_editor') and not self.can_leave():return
        if not self.region:messagebox.showinfo('未选范围','请先框选或选择已保存的区域反馈。',parent=self);return
        if getattr(self,'pane',None) is not None and not self.pane.attention_is_current():
            self._sync_region();self.status.set('关注区域来源已过期；未应用，请重新打开原图框选。');return
        if self.kind.get() != 'face':
            messagebox.showinfo('待支持','该检查部位尚未支持；可保存草稿，不会假装完成测量。',parent=self);return
        if not messagebox.askokcancel('应用范围并重新检查',f'{self.selected.primary_path}\n{self.kind.get()}：{self.region["box"]}\n此框替换当前照片已选人脸，并确认其为本次主要人物。已有身体范围保留，请确认仍属于本次主体。\n本入口仅单个人脸；多人请取消，使用原有多人编辑。\n保存改前证据，整张重测，旧 AI 证据失效；人工决定与分组保持，不调用 AI。取消不应用。',parent=self):return
        try:
            self.on_apply_scope(self.selected,dict(self.region,kind=self.kind.get()))
            # Starting the production job may synchronously destroy this page.
            # Its owner now owns progress/status; never touch disposed widgets.
            if self._page_alive():
                self._load();self._detail();self.status.set('重算请求已提交；完成与失败状态见前后对照。')
        except Exception as exc:
            if self._page_alive():
                self._error('范围未成功应用',exc)
            else:
                # A real start error must not disappear merely because the
                # page was removed. Report via the surviving application owner.
                messagebox.showerror('范围未成功应用',str(exc),parent=self._page_parent.winfo_toplevel())
    def _cancel_recheck(self):
        if getattr(self,'_read_errors',{}) and not self._can_mutate():return
        if self.selected is None or not self.on_cancel_recheck or not self._pending_recheck():return
        if not messagebox.askokcancel('取消未发布的重查',
                f'{self.selected.primary_path}\n取消该照片后台未发布的重查操作，保留原结果、原范围和反馈历史。'
                '\n若任务仍运行，请先返回主页停止并等待结束；取消后可重新画框。',parent=self):return
        try:
            result=self.on_cancel_recheck(self.selected)
            if result is False:return
            if self._page_alive():
                self._load();self._detail()
                self.status.set('未发布的重查已取消；原结果、原范围及反馈历史保留，可重新画框。')
                self._update_read_health()
        except Exception as exc:
            if self._page_alive():self._error('重查未取消',exc)
            else:messagebox.showerror('重查未取消',str(exc),parent=self._page_parent.winfo_toplevel())
    def _decision(self,clear=False):
        if getattr(self,'_read_errors',{}) and not self._can_mutate():return
        if self.selected is None or not self.on_decision:return
        rating=self.rating.get();rating=0 if rating=='清除星级' else int(rating) if rating.isdigit() else None
        try:
            self.on_decision(self.selected,{'保留':1,'弃置':-1,'不标记':0}.get(self.pick.get()),rating,clear=clear)
            self._load();self._detail();self.status.set('人工决定已保存。技术依据未改写。' if not clear else '人工决定已撤回至原有建议，历史保留。')
            if getattr(self,'_read_errors',{}):self._update_read_health()
        except Exception as exc:self._error('人工决定未保存',exc)
    def _compare(self):
        assets=[self.assets[int(i)] for i in self.tree.selection()]
        if len(assets)!=2 or assets[0].group_id!=assets[1].group_id:
            messagebox.showinfo('同组比较','请用 Ctrl 选中同一组的两张照片。',parent=self);return
        ComparisonWindow(self,assets)
    def _fullscreen(self):
        if self.selected is None:return
        from .image_viewer import ImageViewer
        members=[a for a in self.assets if a.group_id==self.selected.group_id]
        ImageViewer(self,members,index=members.index(self.selected),fullscreen=True)
    def _export(self,shared):
        path=filedialog.asksaveasfilename(parent=self,title='保存脱敏诊断' if shared else '保存本机私有诊断（含路径，不要默认上传）',defaultextension='.json')
        if not path:return
        try:self.store.export(path,shared=shared,assets=self.assets);self.status.set('诊断已保存本机；没有上传。')
        except Exception as exc:self._error('诊断未导出',exc)
    def _page_alive(self):
        try:return bool(self.winfo_exists())
        except tk.TclError:return False
    def _error(self,prefix,exc):
        self.status.set(f'{prefix}：{exc}');messagebox.showerror(prefix,str(exc),parent=self)
