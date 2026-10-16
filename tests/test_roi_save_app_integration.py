"""Real App save callback without requiring a Tk display."""
from types import SimpleNamespace as NS
from copy import deepcopy
import pytest
from ai_cull_assistant.app import App
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant import app as app_module


def make_app(tmp_path):
    first=NS(primary_path=tmp_path/'first.png',ai_focus_dirty=False,ai_focus_stale=False,ai_focus_result=None)
    second=NS(primary_path=tmp_path/'second.png',ai_focus_dirty=False,ai_focus_stale=False,ai_focus_result=None)
    calls=[]
    app=NS(_processing_busy=False,_processing_job=None,_sync_selected_workspace=lambda:True,
           _active_input=str(tmp_path),_active_workspace=str(tmp_path/'work'),
           scan_result=NS(assets=[first,second]),crop_settings=CropSettings(),
           _workspace_options=lambda:{},_save_session=lambda:calls.append('session'),
           detection_confidence_var=NS(set=lambda v:None),_set_sheets_ready=lambda v:None,
           _log=lambda v:None,_start_processing=lambda **kw:calls.append(kw))
    return app,calls


def test_saved_batch_refreshes_and_explicitly_regroups_once(tmp_path,monkeypatch):
    app,calls=make_app(tmp_path);monkeypatch.setattr(app_module,'save_workspace_preferences',lambda *a:None)
    settings=CropSettings(photos={CropSettings().key(a):{'selected_faces':[[.1,.1,.2,.2]]} for a in app.scan_result.assets})
    App._save_crop_settings(app,settings)
    assert calls==['session',dict(mode='rescan',regroup=True)]
    assert all(a.ai_focus_dirty for a in app.scan_result.assets)
    settings.photos.clear()
    assert len(app.crop_settings.photos)==2


@pytest.mark.parametrize('block',['busy','paused','review','update'])
def test_inflight_or_paused_job_cannot_save_over_roi_snapshot(tmp_path,monkeypatch,block):
    app,calls=make_app(tmp_path);writes=[];monkeypatch.setattr(app_module,'save_workspace_preferences',lambda *a:writes.append(a))
    if block=='busy':app._processing_busy=True
    if block=='paused':app._processing_job=object()
    if block=='review':app._review_busy=lambda:True
    if block=='update':app.updates=NS(busy=True)
    settings=CropSettings(photos={'new':{'selected_faces':[]}})
    with pytest.raises(RuntimeError):App._save_crop_settings(app,settings)
    assert not writes and not calls and app.crop_settings.photos=={}
    assert not any(a.ai_focus_dirty for a in app.scan_result.assets)


def test_paused_job_blocks_opening_manual_group_editor(tmp_path,monkeypatch):
    app,calls=make_app(tmp_path);app._processing_job=object();messages=[]
    monkeypatch.setattr(app_module.messagebox,'showinfo',lambda *a,**kw:messages.append(a))
    App._open_group_editor(app)
    assert messages and not calls
