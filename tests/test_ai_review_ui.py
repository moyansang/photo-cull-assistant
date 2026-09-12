import json
import time
import tkinter as tk
from pathlib import Path
import pytest
from ai_cull_assistant.ai_review_ui import ReviewDialog, RATING_UNSET
from ai_cull_assistant import ai_api
from ai_cull_assistant.crop_settings import CropSettings
from test_ai_project import setup_project, answer


@pytest.fixture(scope='module')
def tk_root():
    root=tk.Tk();root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def ui(tmp_path,monkeypatch,tk_root):
    project,assets,task,batch=setup_project(tmp_path)
    root=tk_root
    profile=dict(id='test',name='test',base_url='https://example.invalid/v1',model='vision',timeout=5)
    monkeypatch.setattr(ai_api,'load_profiles',lambda _: [profile])
    monkeypatch.setattr(root, '_selected_api_profile', lambda: profile, raising=False)
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.messagebox.askyesno',lambda *a,**kw:True)
    errors=[]
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.messagebox.showerror',lambda *a,**kw:errors.append(a))
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.messagebox.showinfo',lambda *a,**kw:None)
    dialog=ReviewDialog(root,project,assets,CropSettings(),tmp_path)
    drive(root,dialog)
    task=project.current_task();batch=task["batches"][0]
    yield root,dialog,project,task,batch,errors
    dialog._destroy_now()


def drive(root,dialog):
    end=time.monotonic()+15
    while (dialog._api_active or dialog._preparing_task) and time.monotonic()<end:
        root.update();time.sleep(.02)
    assert not dialog._api_active and not dialog._preparing_task


def test_api_queue_ingests_records_provider_and_skips_completed(ui,monkeypatch):
    root,dialog,project,task,batch,errors=ui
    calls=[]
    def fake(profile,prompt,paths):
        calls.append((profile,prompt,paths))
        assert all(Path(p).exists() for p in paths)
        return dict(text=answer(task,batch),usage={'total_tokens':10})
    monkeypatch.setattr(ai_api,'call_model',fake)
    dialog._start_api();drive(root,dialog)
    assert not errors and batch['status']=='complete' and len(calls)==1
    assert batch['raw_responses'][-1]['api_profile']['model']=='vision'
    assert batch['raw_responses'][-1]['usage']['total_tokens']==10
    dialog._start_api()
    assert len(calls)==1
    dialog.rating_var.set(RATING_UNSET);dialog.pick_var.set('留用');dialog._confirm_photo()
    row=project.data['photos'][dialog._selected_photo_id()]
    assert row['final']['confirmed'] and row['final']['rating'] is None and row['final']['pick_status']==1


def test_api_failure_stops_without_retry_and_keeps_confirmed(ui,monkeypatch):
    root,dialog,project,task,batch,errors=ui
    pid=batch['photo_ids'][0];project.confirm(pid,5,None)
    calls=[]
    def fake(*args):
        calls.append(1);raise ai_api.ApiError('API 请求失败（HTTP 429）。')
    monkeypatch.setattr(ai_api,'call_model',fake)
    dialog._start_api();drive(root,dialog)
    assert errors and batch['status']=='failed' and len(calls)==1
    assert project.data['photos'][pid]['final']['rating']==5


def test_web_tabs_prepare_and_restore(ui, monkeypatch):
    root, dialog, project, task, batch, errors = ui
    assert [dialog.notebook.tab(t, 'text') for t in dialog.notebook.tabs()] == ['API 选片', '网页选片', '选片结果']
    assert dialog.export_button.cget('text') == '导出到 LR'
    assert dialog.refine_button.cget('text') == '精选照片再选一轮'
    assert dialog.split_button.cget('text') == '拆分所选批次重试'
    assert dialog.split_button.master.master.master is dialog.task_tab
    opened = []
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.os.startfile', lambda path: opened.append(Path(path)))
    dialog.notebook.select(1)
    dialog._web_select_pending()
    dialog._prepare_web()
    drive(root, dialog)
    assert not errors
    submission = task['web_submissions'][0]
    assert submission['prompt'] == dialog.clipboard_get()
    assert opened and all(p.parent == opened[0] for p in project.web_images(task, submission))
    assert task['current_web_submission_id'] == submission['id']
    dialog._save_ui_settings()
    dialog._refresh_web()
    assert dialog._current_web()[1]['id'] == submission['id']
    assert project.data['ui_settings']['submission_tab'] == 1


def test_web_import_dispatch_and_api_guard(ui, monkeypatch):
    root, dialog, project, task, batch, errors = ui
    submission = project.create_web_submission(task, [batch['id']])
    dialog._refresh_web(submission['id'])
    callbacks = []
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.PasteResponseDialog', lambda parent, callback, **kw: callbacks.append(callback))
    dialog._api_active = True
    dialog._paste_web_response()
    assert not callbacks
    dialog._api_active = False
    dialog._paste_web_response()
    assert callbacks[0](answer(task, submission))
    assert batch['status'] == 'complete'
    assert all(project.data['photos'][pid]['ai']['batch_id'] == batch['id'] for pid in batch['photo_ids'])
    assert not errors


def test_review_only_loads_selected_preview(ui,monkeypatch):
    root,dialog,project,task,batch,errors=ui
    calls=[]
    monkeypatch.setattr(dialog,'_make_thumb',lambda photo,size:calls.append(size))
    dialog.notebook.select(0);root.update();dialog._refresh_review()
    assert not calls
    dialog.notebook.select(1);root.update()
    assert not calls


def test_export_warns_about_unscored_and_can_return_without_writing(ui, monkeypatch):
    root, dialog, project, task, batch, errors = ui
    project.ingest(task, batch, answer(task, batch))
    project.data['photos'][batch['photo_ids'][0]].pop('ai')
    project._assets[0].auto_rejected = True
    project._assets[0].screening_reason = 'severe_subject_blur'
    project.refresh(project._assets, project._crops)
    prompts = []
    monkeypatch.setattr(
        'ai_cull_assistant.ai_review_ui.messagebox.askyesno',
        lambda title, body, **kw: prompts.append((title, body)) or False,
    )
    monkeypatch.setattr(project, 'export_final', lambda **kw: pytest.fail('取消后不应写出结果'))
    dialog._export_ai_ratings()
    assert prompts and prompts[0][0] == '仍有照片未评分'
    assert 'AI 已评分：1 张' in prompts[0][1]
    assert '技术筛选弃置：1 张' in prompts[0][1]
    assert '尚无 AI 评分：1 张' in prompts[0][1]
    assert '独立统计' in prompts[0][1]
    assert dialog.status_var.get() == '已取消导出，可继续完成 AI 选片。'


def test_export_current_results_after_unscored_confirmation(ui, monkeypatch):
    root, dialog, project, task, batch, errors = ui
    project.ingest(task, batch, answer(task, batch))
    project.data['photos'][batch['photo_ids'][0]].pop('ai')
    project._assets[0].auto_rejected = True
    project._assets[0].screening_reason = 'severe_subject_blur'
    project.refresh(project._assets, project._crops)
    opened = []
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.os.startfile', lambda path: opened.append(Path(path)))
    dialog._export_ai_ratings()
    payload = json.loads((project.workspace / 'lightroom_results.json').read_text('utf-8'))
    assert len(payload['photos']) == 2
    first = next(row for row in payload['photos'] if row['filename'].casefold().startswith('a.'))
    second = next(row for row in payload['photos'] if row['filename'].casefold().startswith('b.'))
    assert first['pick_status'] == -1 and 'rating' not in first
    assert second['rating'] == 4
    assert opened == [project.workspace]


def test_enter_creates_fresh_task_without_api_or_history_controls(ui):
    root, dialog, project, task, batch, errors = ui
    assert len(project.data['tasks']) == 1
    assert all(b['status'] == 'pending' for b in task['batches'])
    assert not hasattr(dialog, 'task_combo')
    def labels(parent):
        result=[]
        for w in parent.winfo_children():
            if 'text' in w.keys():result.append(str(w.cget('text')))
            result.extend(labels(w))
        return result
    text=labels(dialog)
    assert '任务历史' not in text and '新建全量初选' not in text and '导入 LR 回执' not in text
    assert '重新提交' in text


def test_resubmit_uses_changed_preferences_and_displays_photo_results(ui, monkeypatch):
    root, dialog, project, old_task, old_batch, errors = ui
    project.ingest(old_task, old_batch, answer(old_task, old_batch))
    dialog.preference_vars['extra'].set('优先眼神清楚')
    calls=[]
    def fake(profile,prompt,paths):
        task=project.current_task();batch=task['batches'][0]
        calls.append(prompt)
        return {'text':answer(task,batch)}
    monkeypatch.setattr(ai_api,'call_model',fake)
    dialog._resubmit_api();drive(root,dialog)
    task=project.current_task()
    assert not errors and len(calls)==1 and task['id']!=old_task['id']
    assert '优先眼神清楚' in calls[0] and len(project.data['tasks'])==1
    dialog.notebook.select(2);root.update()
    for pid in task['batches'][0]['photo_ids']:
        row=project.data['photos'][pid]
        values=dialog.review_tree.item(pid,'values')
        assert values[0]==row['stem'] and values[2]==str(row['ai']['rating'])
        assert values[4]==row['ai']['reason']


def test_tab_round_trip_keeps_api_layout(ui):
    root, dialog, project, task, batch, errors = ui
    root.deiconify(); dialog.deiconify(); root.update()
    dialog.notebook.select(0); root.update()
    def layout():
        return [(w.winfo_x(),w.winfo_y(),w.winfo_width(),w.winfo_height()) for w in
                (dialog.common_tasks,dialog.notebook,dialog.batch_tree,dialog.run_button)]
    before=layout()
    dialog.notebook.select(2);root.update()
    assert dialog.common_tasks.winfo_manager()=='pack'
    dialog.notebook.select(0);root.update()
    assert layout()==before
    root.withdraw()


def test_reopen_preserves_answers_until_home_sheet_changes(ui, monkeypatch, tmp_path):
    root,dialog,project,task,batch,errors=ui
    project.ingest(task,batch,answer(task,batch))
    page=tmp_path/'main.jpg';page.write_bytes(b'unchanged')
    signature=project.home_sheet_signature(dialog.assets,dialog.crop_settings,[page])
    project.data['home_sheet_signature']=signature;project.save()
    previous_id=task['id']
    reopened=ReviewDialog(root,project,dialog.assets,dialog.crop_settings,tmp_path,home_pages=[page])
    try:
        drive(root,reopened)
        assert project.current_task()['id']==previous_id
        assert project.current_task()['batches'][0]['status']=='complete'
        assert all(p.get('ai') for p in project.data['photos'].values())
    finally:
        reopened._destroy_now()
    page.write_bytes(b'new layout')
    changed=ReviewDialog(root,project,dialog.assets,dialog.crop_settings,tmp_path,home_pages=[page])
    try:
        drive(root,changed)
        assert project.current_task()['id']!=previous_id
        assert all(b['status']=='pending' for b in project.current_task()['batches'])
        assert all(not p.get('ai') for p in project.data['photos'].values())
    finally:
        changed._destroy_now()


def test_review_uses_homepage_profile_and_refreshes_changes(ui, monkeypatch):
    root, dialog, project, task, batch, errors = ui
    profile = {'id': 'changed', 'name': '主页配置', 'model': 'vision-test'}
    monkeypatch.setattr(root, '_selected_api_profile', lambda: profile)
    dialog._refresh_profiles()
    assert dialog.profile_var.get() == '主页配置 · vision-test'
    assert list(dialog._profile_labels.values()) == [profile]
    profile['model'] = 'changed-after-snapshot'
    assert list(dialog._profile_labels.values())[0]['model'] == 'vision-test'


def test_manual_web_mode_does_not_require_api_profile(ui, monkeypatch):
    root, dialog, project, task, batch, errors = ui
    monkeypatch.setattr(root, '_selected_api_profile', lambda: None)
    dialog._refresh_profiles()
    assert not dialog._profile_labels
    assert '网页选片' in dialog.profile_var.get()
    assert project.current_task()['id'] == task['id']
    assert not errors
