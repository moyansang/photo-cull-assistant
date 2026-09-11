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
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.messagebox.askyesno',lambda *a,**kw:True)
    errors=[]
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.messagebox.showerror',lambda *a,**kw:errors.append(a))
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.messagebox.showinfo',lambda *a,**kw:None)
    dialog=ReviewDialog(root,project,assets,CropSettings(),tmp_path)
    yield root,dialog,project,task,batch,errors
    dialog._destroy_now()


def drive(root,dialog):
    end=time.monotonic()+5
    while dialog._api_active and time.monotonic()<end:
        root.update();time.sleep(.02)
    assert not dialog._api_active


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
    assert [dialog.notebook.tab(t, 'text') for t in dialog.notebook.tabs()] == ['API 选片', '网页选片']
    assert dialog.export_button.cget('text') == '导出到 LR'
    assert dialog.refine_button.cget('text') == '精选照片再选一轮'
    assert dialog.split_button.cget('text') == '拆分所选批次重试'
    assert dialog.split_button.master.master is dialog.task_tab
    opened = []
    monkeypatch.setattr('ai_cull_assistant.ai_review_ui.os.startfile', lambda path: opened.append(Path(path)))
    dialog.notebook.select(1)
    dialog._web_select_pending()
    dialog._prepare_web()
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
