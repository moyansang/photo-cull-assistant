"""Explicit application routes and production rollback safety."""
from copy import deepcopy
from pathlib import Path
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from test_scan_review_integration import scanned
from ai_cull_assistant.scan_review_actions import prepare_scope
from ai_cull_assistant.scan_review_store import ScanReviewStore, ReviewConflict
from ai_cull_assistant.processing_job import start_job, load_job


def setup_recheck(scanned):
    result,crops,options=scanned
    asset=next(a for a in result.assets if a.screening_reason != 'decode_unavailable')
    draft=dict(kind='face',box=[.3,.25,.6,.6],coordinate_space='normalized_display',
               preview_version='v05',source_path=str(asset.primary_path),
               source_signature=json.loads(json.dumps(__import__('ai_cull_assistant.image_decode',fromlist=['asset_source_signature']).asset_source_signature(asset))))
    changed,context=prepare_scope(result,asset,crops,draft)
    job=start_job(result.input_dir,result.workspace_dir,options,changed,
                  result=deepcopy(result),mode='rescan',scan_review=context)
    return result,asset,options,changed,job


def test_failure_while_publishing_review_rolls_back_scope_and_session(scanned,monkeypatch):
    from ai_cull_assistant import processing_job
    result,asset,options,changed,job=setup_recheck(scanned)
    paths=['scan-session.json','workspace-settings.json','groups.json']
    before={name:(result.workspace_dir/name).read_bytes() if (result.workspace_dir/name).exists() else None for name in paths}
    write=processing_job.atomic_json
    def fail(path,value):
        if Path(path).name=='scan-review.json':raise OSError('simulated disk full')
        return write(path,value)
    monkeypatch.setattr(processing_job,'atomic_json',fail)
    with pytest.raises(OSError,match='disk full'):
        job.run(options,changed,Event(),None)
    for name,original in before.items():
        path=result.workspace_dir/name
        assert (path.read_bytes() if path.exists() else None)==original
    operation=ScanReviewStore(result.workspace_dir).photo(asset)['rechecks'][-1]
    assert operation['status']=='pending' and operation['after'] is None
    assert 'disk full' in operation['last_error']
    monkeypatch.setattr(processing_job,'atomic_json',write)
    resumed=load_job(result.workspace_dir,result.input_dir)
    assert resumed.run(options,changed,Event(),None)
    assert ScanReviewStore(result.workspace_dir).photo(asset)['rechecks'][-1]['status']=='completed'


def test_newer_region_prevents_old_result_publication(scanned):
    result,asset,options,changed,job=setup_recheck(scanned)
    session=(result.workspace_dir/'scan-session.json').read_bytes()
    ScanReviewStore(result.workspace_dir).record_feedback(asset,'region',region={'kind':'hand','box':[.1,.1,.2,.2]})
    with pytest.raises(ReviewConflict,match='范围版本'):
        job.run(options,changed,Event(),None)
    assert (result.workspace_dir/'scan-session.json').read_bytes()==session


def test_app_navigation_is_read_only_and_decision_fields_are_explicit(scanned,monkeypatch):
    from ai_cull_assistant.app import App
    from ai_cull_assistant import scan_review_ui
    result,crops,_=scanned
    factory=Mock(return_value=object())
    monkeypatch.setattr(scan_review_ui,'ScanReviewPage',factory)
    app=SimpleNamespace(_processing_busy=False,updates=SimpleNamespace(busy=False),
        _review_busy=lambda:False,_sync_selected_workspace=lambda:True,
        _show_workspace_page=lambda name:False,scan_result=result,crop_settings=crops,
        _page_host=object(),_mount_workspace_page=Mock(),_apply_scan_review_scope=Mock(),
        _cancel_scan_review_recheck=Mock(),
        _open_group_editor=Mock(),_open_ai_review=Mock())
    before={p.name:p.read_bytes() for p in result.workspace_dir.iterdir() if p.is_file()}
    App._open_scan_review(app)
    assert before=={p.name:p.read_bytes() for p in result.workspace_dir.iterdir() if p.is_file()}
    callbacks=factory.call_args.kwargs
    callbacks['on_read_decisions']()
    callbacks['on_decision'](result.assets[0],None,4)
    row=callbacks['on_read_decisions']()[__import__('ai_cull_assistant.ai_project',fromlist=['photo_id']).photo_id(result.assets[0])]
    assert row['final']['rating']==4 and row['final']['pick_confirmed'] is False


def test_single_recheck_preserves_unchecked_missing_inventory(scanned):
    import json
    from ai_cull_assistant.session_store import load_session
    from ai_cull_assistant.scan_review_ui import result_state
    result,crops,options=scanned
    readable=[a for a in result.assets if a.screening_reason != 'decode_unavailable']
    missing=readable[-1]
    path=missing.primary_path
    original=path.read_bytes()
    old=json.loads((result.workspace_dir/'scan-session.json').read_text('utf-8'))['source_stats']
    path.unlink()
    try:
        current=load_session(result.workspace_dir,result.input_dir,preserve_unavailable=True)
        target=next(a for a in current.assets if a.primary_path==readable[0].primary_path)
        from ai_cull_assistant.image_decode import asset_source_signature
        draft=dict(kind='face',box=[.3,.25,.6,.6],coordinate_space='normalized_display',
                   preview_version='v05',source_path=str(target.primary_path),
                   source_signature=json.loads(json.dumps(asset_source_signature(target))))
        changed,context=prepare_scope(current,target,crops,draft)
        job=start_job(current.input_dir,current.workspace_dir,options,changed,result=current,
                      mode='rescan',scan_review=context)
        assert job.run(options,changed,Event(),None)
        data=json.loads((result.workspace_dir/'scan-session.json').read_text('utf-8'))
        assert data['source_stats'][str(path.resolve())]==old[str(path.resolve())]
        reopened=load_session(result.workspace_dir,result.input_dir,preserve_unavailable=True)
        assert len(reopened.assets)==len(result.assets)
        assert result_state(next(a for a in reopened.assets if a.primary_path==path))=='failed'
    finally:
        path.write_bytes(original)
