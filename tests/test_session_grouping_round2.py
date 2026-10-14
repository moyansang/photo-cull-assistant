"""Deletion reconciliation must not silently change group ownership."""
import json
import shutil
from threading import Event

import pytest
from PIL import Image

from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.group_store import save_groups
from ai_cull_assistant.processing_job import start_job
from ai_cull_assistant.session_store import load_session


@pytest.mark.parametrize('manual', [False, True])
def test_deletion_preserves_group_origin_and_incremental_behavior(tmp_path, manual):
    source=tmp_path/'photos';source.mkdir()
    Image.new('RGB',(120,100),'orange').save(source/'a.jpg')
    shutil.copy2(source/'a.jpg',source/'b.jpg')
    workspace=tmp_path/'workspace'
    options={'technical_screening':False,'analysis_mode':'landscape'}
    crops=CropSettings()
    initial=start_job(source,workspace,options,crops,mode='scan').run(options,crops,Event(),None)
    assert len({a.group_id for a in initial.assets})==1
    if manual:
        save_groups(initial.assets,initial.group_store_path,source='manual',collection_key=str(source.resolve()))
    # Delete whichever member is not the fixed representative.
    from ai_cull_assistant.grouping import source_photo_id
    survivor=next(a for a in initial.assets if source_photo_id(a)==a.grouping_anchor_id)
    removed=next(a for a in initial.assets if a is not survivor)
    removed.primary_path.unlink()
    restored=load_session(workspace,source)
    assert len(restored.assets)==1
    payload=json.loads(initial.group_store_path.read_text())
    assert payload['source']==('manual' if manual else 'auto')
    assert restored.assets[0].group_id==survivor.group_id
    shutil.copy2(survivor.primary_path,source/'new.jpg')
    updated=start_job(source,workspace,options,crops,result=restored,mode='scan').run(options,crops,Event(),None)
    fresh=next(a for a in updated.assets if a.primary_path.name=='new.jpg')
    assert (fresh.group_id==survivor.group_id) is (not manual)


def test_source_changes_during_incremental_scan_leave_published_session_intact(tmp_path, monkeypatch):
    from ai_cull_assistant import processing_job
    source=tmp_path/'photos';source.mkdir()
    Image.new('RGB',(120,100),'orange').save(source/'a.jpg')
    workspace=tmp_path/'workspace';options={'technical_screening':False};crops=CropSettings()
    initial=start_job(source,workspace,options,crops,mode='scan').run(options,crops,Event(),None)
    before={name:(workspace/name).read_bytes() for name in ['scan-session.json','groups.json','screening_results.json']}
    original_preview=initial.assets[0].preview_path.read_bytes()
    Image.new('RGB',(130,100),'green').save(source/'b.jpg')
    real=processing_job.build_preview
    def replace_during_scan(asset,*args,**kwargs):
        value=real(asset,*args,**kwargs)
        Image.new('RGB',(140,100),'blue').save(source/'a.jpg')
        return value
    monkeypatch.setattr(processing_job,'build_preview',replace_during_scan)
    job=start_job(source,workspace,options,crops,result=initial,mode='scan')
    with pytest.raises(processing_job.SourceChangedError):
        job.run(options,crops,Event(),None)
    assert all((workspace/name).read_bytes()==value for name,value in before.items())
    assert initial.assets[0].preview_path.read_bytes()==original_preview
    assert not job.root.exists()
