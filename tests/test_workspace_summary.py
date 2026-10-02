import json
from types import SimpleNamespace as NS
from ai_cull_assistant.workspace_summary import restoration_summary

SETTINGS=dict(grouping='宽松',per_page=24,columns=6,screening=True,body_screening=False,confidence=.7)

def summary(tmp_path, assets=None, **kwargs):
    return '\n'.join(restoration_summary(tmp_path, assets, SETTINGS, kwargs.get('sheets',False),kwargs.get('job')))

def test_saved_settings_and_new_workspace(tmp_path):
    text=summary(tmp_path)
    assert '分组灵敏度 宽松' in text and '每页 24 张' in text
    assert '身体清晰度检查（实验）关闭' in text
    assert '尚无可恢复' in text

def test_scan_only_and_partial_review(tmp_path):
    assets=[NS(screening_reason='face_focus_uncertain'),NS(ai_focus_result={'status':'uncertain','reason':'细节不足'}),NS(ai_focus_dirty=True)]
    text=summary(tmp_path,assets)
    assert '已复核 1 张' in text and '有效结论 1 张' in text and '重新处理 1 张' in text
    assert '清晰度待确认' in text and '尚未创建任务' in text

def test_completed_culling_and_export_read_only(tmp_path):
    path=tmp_path/'ai_project.json'
    path.write_text(json.dumps(dict(tasks=[dict(id='a',batches=[dict(status='complete')])],photos={},last_export_id='export')),encoding='utf-8')
    before=path.read_bytes()
    text=summary(tmp_path,[NS(ai_focus_result={'status':'clear','reason':'清晰'})],sheets=True)
    assert '当前任务已完成' in text and '联系表：已生成' in text and 'LR 结果：已导出' in text
    assert path.read_bytes()==before

def test_partial_task_stale_export_and_resume(tmp_path):
    (tmp_path/'ai_project.json').write_text(json.dumps(dict(tasks=[dict(id='a',batches=[dict(status='complete'),dict(status='partial')])],photos={'p':{'stale':True}},last_export_id='e',export_dirty=True)))
    text=summary(tmp_path,[],job=NS(mode='focus',percent=42))
    assert '进度约 42%' in text and '已完成 1/2 批' in text
    assert '旧结果已失效' in text and '需重新导出' in text

def test_broken_project_not_claim_complete(tmp_path):
    (tmp_path/'ai_project.json').write_text('{')
    assert '暂不能确认进度' in summary(tmp_path,[])

def test_local_clear_is_not_ai_reviewed(tmp_path):
    text=summary(tmp_path,[NS(screening_reason='subject_not_obviously_blurred')])
    assert '未发现有效 AI 复核记录' in text

def test_app_restore_hooks_and_log_summary(tmp_path):
    import inspect
    from ai_cull_assistant.app import App
    class Var:
        def __init__(self, value): self.value=value
        def get(self): return self.value
    lines=[]
    app=NS(_workspace_blocked=False,workspace_var=Var(str(tmp_path)),preset_var=Var('宽松'),
           per_page_var=Var(24),columns_var=Var(6),screening_var=Var(True),body_screening_var=Var(False),
           crop_settings=NS(detection_confidence=.7),scan_result=None,_processing_job=None,
           _sheets_ready=lambda:False,_log=lines.append)
    App._log_workspace_summary(app)
    assert any('身体清晰度检查（实验）关闭' in line for line in lines)
    for method in (App.__init__,App._activate_workspace):
        source=inspect.getsource(method)
        assert source.index('self._restore_processing_job()') < source.index('self._log_workspace_summary()')
