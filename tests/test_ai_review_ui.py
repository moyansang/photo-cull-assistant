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
