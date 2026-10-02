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
           _sheets_ready=lambda:False,_log=lines.append,next_step_var=Var("推荐下一步：扫描图片"))
    App._log_workspace_summary(app)
    assert any('身体清晰度检查（实验）关闭' in line for line in lines)
    for method in (App.__init__,App._activate_workspace):
        source=inspect.getsource(method)
        assert source.index('self._restore_processing_job()') < source.index('self._log_workspace_summary()')


def test_recommendations_and_reason_counts(tmp_path):
    from ai_cull_assistant.workspace_summary import recommended_next_step as next_step, focus_counts
    clear=NS(screening_reason='subject_not_obviously_blurred')
    pending=NS(screening_reason='face_focus_uncertain')
    failed=NS(screening_reason='face_focus_uncertain',ai_focus_attempt='failed')
    skipped=NS(screening_reason='face_focus_uncertain',ai_focus_attempt='skipped')
    dirty=NS(ai_focus_dirty=True)
    assert next_step(tmp_path,None,False)=='扫描图片'
    assert next_step(tmp_path,[pending],False)=='AI 复核'
    assert next_step(tmp_path,[failed],False)=='继续 AI 复核'
    assert '重新扫描' in next_step(tmp_path,[dirty],False)
    assert next_step(tmp_path,[skipped],False)=='生成联系表'
    counts=focus_counts([pending,failed,skipped,dirty])
    assert [counts[k] for k in ('pending','failed','skipped','dirty')]==[1,1,1,1]
    assert '继续AI 复核' in next_step(tmp_path,[clear],False,NS(mode='focus'))
    p=tmp_path/'ai_project.json'
    data=dict(tasks=[dict(id='a',batches=[dict(status='failed')])],photos={})
    p.write_text(json.dumps(data))
    assert '继续 AI 选片' in next_step(tmp_path,[clear],True)
    data['tasks'][0]['batches'][0]['status']='complete';p.write_text(json.dumps(data))
    assert '导出 LR' in next_step(tmp_path,[clear],True)
    data['last_export_id']='e';p.write_text(json.dumps(data))
    assert '处理已完成' in next_step(tmp_path,[clear],True)
    data['photos']['p']={'stale':True};p.write_text(json.dumps(data))
    assert '失效' in next_step(tmp_path,[clear],True)


def test_focus_attempt_survives_serialization():
    from datetime import datetime
    from pathlib import Path
    from ai_cull_assistant.models import PhotoAsset
    from ai_cull_assistant.processing_job import _asset_to_dict, _asset_from_dict
    a=PhotoAsset('p',Path('p.jpg'),Path('p.jpg'),None,Path('p.jpg'),datetime.now(),'.jpg',ai_focus_attempt='skipped')
    assert _asset_from_dict(_asset_to_dict(a)).ai_focus_attempt=='skipped'
