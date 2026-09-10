from datetime import datetime
from pathlib import Path
import json
import pytest
from PIL import Image
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.lightroom_results import write_lightroom_results
from ai_cull_assistant.workflow import apply_selection_text, run_scan


def asset(root, stem='中文照片'):
    root.mkdir(parents=True,exist_ok=True)
    p=root/f'{stem}.jpg'
    Image.new('RGB',(80,120),'gray').save(p)
    return PhotoAsset(stem,p,p,None,p,datetime.now(),'.jpg')


def test_export_preserves_photo_folder_and_existing_xmp(tmp_path):
    a=asset(tmp_path/'photos','TEST01')
    xmp=a.primary_path.with_suffix('.xmp'); xmp.write_bytes(b'existing edits')
    a.auto_rejected=True; a.screening_reason='blur'
    before={p.name:p.read_bytes() for p in a.primary_path.parent.iterdir()}
    output=tmp_path/'work/results.json'
    write_lightroom_results([a],output)
    record=json.loads(output.read_text('utf-8'))['photos'][0]
    assert record['pick_status']==-1 and 'rating' not in record
    apply_selection_text([a],'TEST01,5',results_path=output)
    record=json.loads(output.read_text('utf-8'))['photos'][0]
    assert record['rating']==5 and record['pick_status']==0
    assert before=={p.name:p.read_bytes() for p in a.primary_path.parent.iterdir()}


def test_raw_jpeg_exact_paths_and_ambiguous_stems(tmp_path):
    a=asset(tmp_path/'a')
    a.raw_path=a.primary_path.with_suffix('.RW2'); a.raw_path.write_bytes(b'raw')
    result=tmp_path/'work/results.json'
    write_lightroom_results([a],result,{a.stem:4})
    rows=json.loads(result.read_text('utf-8'))['photos']
    assert len(rows)==2 and {r['path'] for r in rows}=={str(p.resolve()) for p in a.rating_target_paths}
    b=asset(tmp_path/'b')
    previous=result.read_bytes()
    with pytest.raises(ValueError,match='重复'):
        write_lightroom_results([a,b],result,{a.stem:5})
    assert previous==result.read_bytes()


def test_scan_produces_result_not_xmp(tmp_path):
    a=asset(tmp_path/'photos','TEST01')
    before=a.primary_path.read_bytes()
    result=run_scan(a.primary_path.parent,tmp_path/'work')
    assert not (result.workspace_dir/'lightroom_results.json').exists()
    assert not list(a.primary_path.parent.glob('*.xmp'))
    assert before==a.primary_path.read_bytes()
