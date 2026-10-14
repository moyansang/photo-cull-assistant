"""Derived local evidence must use the decoder's cheap source-version contract."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import os

from PIL import Image
import pytest

from ai_cull_assistant import face_focus, body_pipeline, image_decode
from ai_cull_assistant.body_focus import VERSION as BODY_VERSION
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.screening import ScreeningResult


@pytest.fixture(autouse=True)
def no_paid_model_calls(monkeypatch):
    monkeypatch.setattr('ai_cull_assistant.ai_api.call_model',
                        lambda *args,**kwargs:pytest.fail('Local cache revalidation cannot call paid AI'))
    image_decode.clear_decode_cache()
    yield
    image_decode.clear_decode_cache()


def make_asset(tmp_path,paired=False):
    source=tmp_path/'photo.bmp'
    Image.new('RGB',(320,320),'red').save(source)
    raw=tmp_path/'photo.dng' if paired else None
    if raw:raw.write_bytes(b'raw-variant-one!')
    return PhotoAsset('photo',source,source,raw,source,datetime.now(),'.bmp',preview_path=source)


def replace_preserving_size_mtime(path,kind):
    before=path.stat()
    before_signature=image_decode.source_signature(path)
    if path.suffix=='.bmp':
        replacement=path.with_name('replacement.bmp')
        Image.new('RGB',(320,320),'blue').save(replacement)
        payload=replacement.read_bytes()
    else:
        replacement=path.with_name('replacement.dng')
        payload=b'raw-variant-two!';replacement.write_bytes(payload)
    assert len(payload)==before.st_size
    if kind=='atomic':
        os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns))
        replacement.replace(path)
    else:
        path.write_bytes(payload)
        os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    after=path.stat()
    assert (after.st_size,after.st_mtime_ns)==(before.st_size,before.st_mtime_ns)
    assert image_decode.source_signature(path)!=before_signature, \
        'Public source identity must detect the completed rewrite'


def evaluator(stage,asset,cache,monkeypatch):
    subject=SimpleNamespace(face=(0.,0.,1.,1.),landmarks=None)
    calls=[]
    if stage=='face':
        monkeypatch.setattr(face_focus,'detail_features',lambda *args,**kwargs:subject)
        def measure(image,participants):
            calls.append(tuple(image.getpixel((0,0))))
            return [dict(participant=1,state='clear',reasons=[],face_box=(0,0,320,320),
                         laplacian_variance=10.,tenengrad=20.,detail_ratio=.25,
                         local_generation=len(calls))]
        monkeypatch.setattr(face_focus,'_measure_participants',measure)
        def evaluate():
            return face_focus.assess_asset_focus(asset,cache_dir=cache).focus_evidence
    else:
        monkeypatch.setattr('ai_cull_assistant.subject.detail_features',lambda *args,**kwargs:subject)
        def measure(image,box):
            calls.append(tuple(image.getpixel((0,0))))
            return dict(version=BODY_VERSION,state='clear',review_kind='clear',reasons=[],
                        regions=[],local_generation=len(calls))
        monkeypatch.setattr('ai_cull_assistant.body_focus.assess_body_focus',measure)
        def evaluate():
            screened=ScreeningResult(False,'subject_not_obviously_blurred',True,
                                     focus_evidence={'state':'clear'})
            return body_pipeline.apply_body_check(asset,screened,CropSettings(),cache).focus_evidence['body']
    return evaluate,calls


@pytest.mark.parametrize('stage',['face','body'])
@pytest.mark.parametrize('source_role',['primary','paired_raw'])
@pytest.mark.parametrize('replacement',['inplace','atomic'])
def test_local_disk_cache_revalidates_equal_size_preserved_mtime_rewrite(
        tmp_path,monkeypatch,stage,source_role,replacement):
    asset=make_asset(tmp_path,paired=source_role=='paired_raw')
    evaluate,calls=evaluator(stage,asset,tmp_path/'local-cache',monkeypatch)
    assert evaluate()['local_generation']==1
    assert evaluate()['local_generation']==1 and len(calls)==1, 'Unchanged inputs should reuse local evidence'
    changed=asset.raw_path if source_role=='paired_raw' else asset.primary_path
    replace_preserving_size_mtime(changed,replacement)
    assert evaluate()['local_generation']==2, 'Explicit re-evaluation reused stale local disk evidence'
    assert len(calls)==2
    assert evaluate()['local_generation']==2 and len(calls)==2
    if source_role=='primary':assert calls==[(255,0,0),(0,0,255)]


@pytest.mark.parametrize('stage',['face','body'])
def test_local_cache_preserves_old_derived_files_and_paid_result(tmp_path,monkeypatch,stage):
    asset=make_asset(tmp_path)
    asset.ai_focus_result={'status':'clear','reason':'Existing paid result','technical_fingerprint':'unchanged'}
    paid=dict(asset.ai_focus_result)
    cache=tmp_path/'cache';cache.mkdir()
    legacy=cache/'legacy-existing.json';legacy.write_text('{"old_local_evidence":true}')
    evaluate,calls=evaluator(stage,asset,cache,monkeypatch)
    evaluate()
    replace_preserving_size_mtime(asset.primary_path,'atomic')
    evaluate()
    assert len(calls)==2
    assert asset.ai_focus_result==paid
    assert legacy.read_text()=='{"old_local_evidence":true}'


@pytest.mark.parametrize('stage',['face','body'])
def test_changed_source_during_measurement_is_not_saved_under_old_key(tmp_path,monkeypatch,stage):
    asset=make_asset(tmp_path)
    cache=tmp_path/'cache'
    evaluate,calls=evaluator(stage,asset,cache,monkeypatch)
    module=face_focus if stage=='face' else __import__('ai_cull_assistant.body_focus',fromlist=['assess_body_focus'])
    name='_measure_participants' if stage=='face' else 'assess_body_focus'
    measure=getattr(module,name)
    def change_during_measurement(*args,**kwargs):
        result=measure(*args,**kwargs)
        if len(calls)==1:replace_preserving_size_mtime(asset.primary_path,'atomic')
        return result
    monkeypatch.setattr(module,name,change_during_measurement)
    evaluate()
    assert not list(cache.rglob('*.json')), 'Inputs changed during scoring; result must not enter disk cache'
    assert evaluate()['local_generation']==2
    assert calls==[(255,0,0),(0,0,255)]
    assert len(list(cache.rglob('*.json')))==1


@pytest.mark.parametrize('stage',['face','body'])
def test_changed_source_during_cache_read_rechecks_current_signature(tmp_path,monkeypatch,stage):
    asset=make_asset(tmp_path)
    cache=tmp_path/'cache'
    evaluate,calls=evaluator(stage,asset,cache,monkeypatch)
    evaluate()
    original=Path.read_text
    replaced=[]
    def read_then_replace(path,*args,**kwargs):
        value=original(path,*args,**kwargs)
        if path.suffix=='.json' and cache in path.parents and not replaced:
            replaced.append(True)
            replace_preserving_size_mtime(asset.primary_path,'atomic')
        return value
    monkeypatch.setattr(Path,'read_text',read_then_replace)
    assert evaluate()['local_generation']==2
    assert calls==[(255,0,0),(0,0,255)]


@pytest.mark.parametrize('stage',['face','body'])
def test_missing_and_reappearing_raw_pair_versions_local_evidence(tmp_path,monkeypatch,stage):
    asset=make_asset(tmp_path,paired=True)
    evaluate,calls=evaluator(stage,asset,tmp_path/'cache',monkeypatch)
    evaluate()
    before=asset.raw_path.stat();payload=asset.raw_path.read_bytes()
    asset.raw_path.unlink()
    assert evaluate()['local_generation']==2
    asset.raw_path.write_bytes(payload)
    os.utime(asset.raw_path,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert evaluate()['local_generation']==3 and len(calls)==3


@pytest.mark.parametrize('stage',['face','body'])
def test_old_weak_key_is_recomputed_locally_without_deleting_legacy_cache(tmp_path,monkeypatch,stage):
    import hashlib
    import json
    from dataclasses import asdict
    asset=make_asset(tmp_path)
    cache=tmp_path/'cache'
    version=face_focus.VERSION if stage=='face' else BODY_VERSION
    stat=asset.primary_path.stat()
    identity=dict(version=version,path=str(asset.primary_path.resolve()),size=stat.st_size,
                  mtime=stat.st_mtime_ns,face=(0.,0.,1.,1.))
    if stage=='face':identity['landmarks']=None
    identity['sources']=[[str(asset.primary_path.resolve()),stat.st_size,stat.st_mtime_ns]]
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    legacy=cache/version/(key+'.json');legacy.parent.mkdir(parents=True)
    old_body=dict(version=version,state='clear',review_kind='clear',reasons=[],regions=[],local_generation=999)
    payload=(asdict(ScreeningResult(False,'subject_not_obviously_blurred',True,
                                   focus_evidence={'state':'clear','local_generation':999}))
             if stage=='face' else old_body)
    legacy.write_text(json.dumps(payload));original=legacy.read_bytes()
    evaluate,calls=evaluator(stage,asset,cache,monkeypatch)
    assert evaluate()['local_generation']==1 and len(calls)==1
    assert legacy.read_bytes()==original
    assert len(list(cache.rglob('*.json')))==2


@pytest.mark.parametrize('stage',['face','body'])
def test_candidate_versioning_does_not_read_or_hash_large_raw_file(tmp_path,monkeypatch,stage):
    asset=make_asset(tmp_path,paired=True)
    with asset.raw_path.open('wb') as stream:stream.truncate(16*1024*1024)
    original=Path.read_bytes
    def no_raw_reads(path):
        if path==asset.raw_path:pytest.fail('Cache identity must use cheap stat, not read/hash full RAW bytes')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',no_raw_reads)
    evaluate,calls=evaluator(stage,asset,tmp_path/'cache',monkeypatch)
    assert evaluate()['local_generation']==1
    assert evaluate()['local_generation']==1 and len(calls)==1
