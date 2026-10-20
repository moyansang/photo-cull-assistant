"""Synthetic, metadata-only adapter contracts; no real photo accuracy evidence."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import offline_evaluation as oe
import frozen_prediction_export as fe
oe = fe.oe


def fixture():
    commit = oe.git_bytes(ROOT, 'rev-parse', 'HEAD').decode().strip()
    versions = fe.expected_versions(ROOT, commit)
    sample = oe.synthetic_documents(oe.make_catalog(ROOT, commit))['samples']
    sample['rows'] = sample['rows'][:1]
    row = sample['rows'][0]
    path = 'C:/合成 样本/photo.jpg'
    inputs = {'source_signature': [['primary_path', [path, 100, 123, 124, 1, 2]], ['jpg_path', [path, 100, 123, 124, 1, 2]]],
              'parameters_digest': oe.digest(b'SYNTHETIC effective configuration'), 'enabled_parts': ['scene']}
    asset = {'stem': 'photo', 'primary_path': path, 'jpg_path': path, 'raw_path': None,
             'analysis_mode': 'landscape', 'clarity_version': 'clarity-v2',
             'local_recommendation': 'keep', 'auto_rejected': False, 'face_found': False,
             'ai_focus_dirty':False, 'ai_focus_stale':False, 'person_review_pending':False,
             'clarity_evidence': {'version': versions['landscape'], 'mode': 'landscape',
                 'recommendation': 'keep', 'state':'clear', 'reliability':'adequate', 'reasons': ['synthetic_unknown_原因'], 'regions': [],
                 'decode': {'version': versions['decode'], 'source_path': path, 'source_kind': 'standard_image'}}}
    session = {'version': 1, 'assets': [asset], 'source_stats': {path: [100, 123]}}
    receipt = {'schema_version': 'frozen-prediction-provenance-v1', 'kind': 'measurement_time_receipt',
               'synthetic': True, 'recorded_at': '2026-10-20T00:00:00+00:00',
               'source_git': commit, 'run_id': 'SYNTHETIC-NO-EXECUTION', 'prediction_version': 'synthetic-v1',
               'records': {}, 'rows': []}
    for name in ('dependency_lock', 'configuration', 'decode_configuration'):
        content = 'SYNTHETIC record: ' + name
        receipt['records'][name] = {'content': content, 'sha256': oe.digest(content.encode())}
    receipt['rows'] = [dict(asset_id=row['asset_id'], sample_version=row['sample_version'],
        evaluated_source_id=row['evaluation_source_id'], input_sha256=row['sources'][0]['sha256'],
        asset_index=0, source_member=path, source_kind='standard_image', measured_at='2026-10-19T23:59:00+00:00',
        measurement_inputs=deepcopy(inputs), frozen_inputs=deepcopy(inputs), versions=versions)]
    return rebind(session, sample, receipt)


def rebind(session, samples, receipt):
    raw = json.dumps(session, ensure_ascii=False).encode('utf-8')
    receipt['session_sha256'] = oe.digest(raw)
    receipt['manifest_sha256'] = oe.canonical_hash(samples)
    for r in receipt['rows']:
        r['asset_sha256'] = oe.canonical_hash(session['assets'][r['asset_index']])
    return session, samples, receipt


def export(data):
    session, samples, receipt = data
    return fe.build_export(session, samples, receipt, session_sha256=receipt['session_sha256'], repo=ROOT)


def test_saved_prediction_not_manual_or_ai():
    data = fixture()
    asset = data[0]['assets'][0]
    asset.update(auto_rejected=True, screening_reason='ai_focus_blur', rating=5,
                 final={'pick': 'reject'}, ai_focus_result={'status': 'blur'})
    result = export(rebind(*data))
    assert result['predictions']['rows'][0]['recommendation'] == 'keep'
    assert result['annotations']['rows'] == []
    assert result['summary']['real_world_accuracy']['status'] == '尚未测量'


def test_unknown_reasons_and_unknown_scene_survive():
    result = export(fixture())
    assert 'synthetic_unknown_原因' in result['predictions']['rows'][0]['reasons']
    assert result['summary']['real_world_accuracy']['value'] is None


def test_false_rejected_cannot_invent_keep():
    data = fixture()
    del data[0]['assets'][0]['clarity_evidence']['recommendation']
    result = export(rebind(*data))
    assert result['predictions']['rows'][0]['status'] == 'failed'


def test_missing_mapping_is_missing_not_keep():
    data = fixture(); data[2]['rows'] = []
    result = export(data)
    assert result['predictions']['rows'] == []


@pytest.mark.parametrize('flag', ['ai_focus_dirty', 'ai_focus_stale', 'person_review_pending'])
def test_explicit_stale_flags_never_export_success(flag):
    data = fixture(); data[0]['assets'][0][flag] = True
    result = export(rebind(*data))
    assert result['predictions']['rows'][0]['status'] == 'failed'


def test_changed_frozen_inputs_fails():
    data = fixture(); data[2]['rows'][0]['frozen_inputs']['parameters_digest'] = oe.digest(b'changed')
    assert export(data)['predictions']['rows'][0]['status'] == 'failed'


@pytest.mark.parametrize('field', ['session_sha256', 'manifest_sha256'])
def test_bad_document_binding_refused(field):
    data = fixture(); data[2][field] = oe.digest(b'mismatch')
    # Session actual hash is supplied independently, not copied from a forged receipt.
    raw = json.dumps(data[0], ensure_ascii=False).encode('utf-8')
    with pytest.raises(oe.ContractError):
        fe.build_export(*data, session_sha256=oe.digest(raw), repo=ROOT)


def test_configuration_record_hash_refused():
    data = fixture(); data[2]['records']['configuration']['content'] += 'changed'
    with pytest.raises(oe.ContractError): export(data)


def test_duplicate_exposure_refused():
    data = fixture(); data[1]['rows'].append(deepcopy(data[1]['rows'][0]))
    with pytest.raises(oe.ContractError): export(rebind(*data))


def test_synthetic_receipt_cannot_label_real_run():
    data = fixture(); data[1]['evaluation_kind'] = 'development'
    with pytest.raises(oe.ContractError): export(rebind(*data))


def test_source_member_mismatch_refused():
    data = fixture(); data[2]['rows'][0]['source_member'] = 'C:/another.jpg'
    with pytest.raises(oe.ContractError): export(data)


def test_source_hash_mismatch_refused_or_failed():
    data = fixture(); data[2]['rows'][0]['input_sha256'] = oe.digest(b'wrong source')
    try:
        result = export(data)
    except oe.ContractError:
        return
    assert result['predictions']['rows'][0]['status'] == 'failed'


def test_cli_chinese_paths_readonly_end_to_end(tmp_path):
    directory = tmp_path / '中文 路径'; directory.mkdir()
    session, samples, receipt = fixture()
    paths = {name: directory / (name + '.json') for name in ('session', 'samples', 'provenance')}
    raw = json.dumps(session, ensure_ascii=False).encode('utf-8')
    receipt['session_sha256'] = oe.digest(raw)
    paths['session'].write_bytes(raw)
    for name, value in [('samples', samples), ('provenance', receipt)]:
        paths[name].write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    before = {name: path.read_bytes() for name, path in paths.items()}
    output = directory / '导出 结果'
    args = [sys.executable, '-X', 'utf8', str(ROOT/'tools/frozen_prediction_export.py')]
    for name, path in paths.items(): args += ['--'+name, str(path)]
    args += ['--output', str(output)]
    run = subprocess.run(args, capture_output=True, encoding='utf-8')
    assert run.returncode == 0, run.stderr
    assert all(path.read_bytes() == before[name] for name,path in paths.items())
    predictions = oe.read_json(output/'predictions.json')
    assert predictions['rows'][0]['recommendation'] == 'keep'
    assert oe.read_json(output/'annotations.json')['rows'] == []
    # A second export must not overwrite the first output.
    second = subprocess.run(args, capture_output=True, encoding='utf-8')
    assert second.returncode != 0


def test_only_raw_and_raw_jpg_pair_bind_actual_member():
    for paired in (False, True):
        data = fixture()
        asset = data[0]['assets'][0]; sample = data[1]['rows'][0]; r = data[2]['rows'][0]
        raw_path = 'C:/合成 样本/photo.RAW'
        jpg_path = asset['jpg_path']
        asset['raw_path'] = asset['primary_path'] = raw_path
        if not paired: asset['jpg_path'] = None
        data[0]['source_stats'][raw_path] = [200, 124]
        asset['clarity_evidence']['decode'].update(source_path=raw_path, source_kind='raw_full_decode')
        raw_source = sample['sources'][1]
        sample['evaluation_source_id'] = raw_source['source_id']
        r.update(evaluated_source_id=raw_source['source_id'], input_sha256=raw_source['sha256'],
                 source_member=raw_path, source_kind='raw_full_decode')
        signatures = [['primary_path',[raw_path,200,124,125,1,3]], ['raw_path',[raw_path,200,124,125,1,3]]]
        if paired: signatures.append(['jpg_path',[jpg_path,100,123,124,1,2]])
        for key in ('measurement_inputs','frozen_inputs'): r[key]['source_signature'] = deepcopy(signatures)
        result = export(rebind(*data))
        row = result['predictions']['rows'][0]
        assert row['status'] == 'ok'
        assert row['evaluated_source_id'] == raw_source['source_id']
        assert len(result['predictions']['rows']) == 1


def test_absent_decode_is_failure_even_if_primary_exists():
    data = fixture(); del data[0]['assets'][0]['clarity_evidence']['decode']
    assert export(rebind(*data))['predictions']['rows'][0]['status'] == 'failed'


def test_version_mismatch_is_not_repaired():
    data = fixture(); data[0]['assets'][0]['clarity_evidence']['version'] = 'unknown-old-version'
    result = export(rebind(*data))
    assert result['predictions']['rows'][0]['status'] == 'failed'
    assert data[0]['assets'][0]['clarity_evidence']['version'] == 'unknown-old-version'


def test_runtime_failure_is_not_review_or_keep():
    data = fixture()
    data[0]['assets'][0]['clarity_evidence'] = {'state':'unavailable','reasons':['decode_unavailable']}
    result = export(rebind(*data))
    row = result['predictions']['rows'][0]
    assert row['status'] == 'failed'
    assert row.get('recommendation') not in ('keep','review')


def test_supplied_blank_digest_refused():
    data = fixture(); data[2]['records']['configuration']['sha256'] = '0'*64
    with pytest.raises(oe.ContractError): export(data)


def test_export_into_evaluator_with_independent_synthetic_labels(tmp_path):
    data = fixture(); result = export(data)
    samples = data[1]; s = samples['rows'][0]
    annotations = deepcopy(result['annotations'])
    annotations['rows'] = [{'asset_id':s['asset_id'], 'sample_version':s['sample_version'],
        'evaluated_source_id':s['evaluation_source_id'], 'input_sha256':s['sources'][0]['sha256'],
        'technical_label':'unusable', 'culling_label':'D', 'final_selection':'keep',
        'label_source':'human', 'annotator_id':'SYNTHETIC-RATER', 'evidence_adequacy':'adequate','regions':[]}]
    args=[sys.executable,'-X','utf8',str(ROOT/'tools/offline_evaluation.py'),'evaluate']
    for name, doc in [('samples',samples),('predictions',result['predictions']),
                      ('annotations',annotations),('catalog',result['catalog'])]:
        path=tmp_path/(name+'.json'); path.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
        args += ['--'+name,str(path)]
    args += ['--output', str(tmp_path/'report')]
    proc=subprocess.run(args,capture_output=True,encoding='utf-8')
    assert proc.returncode==0,proc.stderr
    summary=oe.read_json(tmp_path/'report'/'summary.json')
    cases=[json.loads(line) for line in (tmp_path/'report'/'cases.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(cases)==1
    assert summary['real_world_accuracy']['value'] is None
    # Independent technical truth remains unusable despite final aesthetic keep.
    assert any('unusable' in str(v) for v in cases[0].values())


def portrait_fixture():
    data=fixture(); asset=data[0]['assets'][0]; receipt=data[2]['rows'][0]
    versions=receipt['versions']; inputs=receipt['measurement_inputs']; inputs['enabled_parts']=['face']
    receipt['frozen_inputs']=deepcopy(inputs)
    proof={'version':'single-detected-face-v1','candidate_count':1,'landmarks_available':True,'box':[.1,.1,.2,.2]}
    selection=dict(inputs,version='automatic-primary-selection-v1',origin='automatic',human_confirmed=False,
                   regions=[{'id':'face:demo','kind':'face','box':proof['box']}],detector_provenance=proof)
    selection['digest']=oe.canonical_hash(selection)
    policy={'version':versions['primary_quality'],'selection_origin':'automatic','selection_digest':selection['digest'],
            'confirmation_digest':None,'enabled_parts':['face'],'recommendation':'keep','state':'clear'}
    scope={'version':versions['focus_protocol'],'mode':'portrait','source_digest':oe.canonical_hash(inputs['source_signature']),
           'photo_identity':'photo:'+oe.canonical_hash(inputs['source_signature']), 'settings_digest':inputs['parameters_digest'],
           'confirmation_digest':None,'selection_origin':'automatic','selection_digest':selection['digest'],
           'enabled_parts':['face'],'subjects':[{'id':'face:demo','kind':'face'}]}
    scope['digest']=oe.canonical_hash(scope)
    decode=asset['clarity_evidence']['decode']
    asset.update(analysis_mode='portrait',focus_scope=scope,clarity_evidence={
        'version':versions['analysis_modes'],'mode':'portrait','recommendation':'keep','state':'clear','reliability':'adequate',
        'measurement_inputs':inputs,'enabled_parts':['face'],'primary_quality':policy,
        'automatic_selection':selection,'measurement_selection_digest':selection['digest'],
        'decode':decode,'automatic_subject':proof,
        'subjects':[{'evidence_kind':'face','normalized_box':proof['box'],'reasons':['unknown_portrait_reason']}],
        'body_results':[],'body_candidates':[]})
    return rebind(*data)


def test_supported_automatic_face_portrait():
    result=export(portrait_fixture())
    assert result['predictions']['rows'][0]['status']=='ok',result['diagnostics']
    assert result['predictions']['rows'][0]['recommendation']=='keep'


@pytest.mark.parametrize('change', ['body','scope','selection','recommendation','human'])
def test_portrait_incoherence_is_failed(change):
    data=portrait_fixture(); a=data[0]['assets'][0]; e=a['clarity_evidence']
    if change=='body': e['body_results']=[{'state':'clear'}]
    if change=='scope': a['focus_scope']['settings_digest']=oe.digest(b'changed')
    if change=='selection': e['automatic_selection']['regions']=[]
    if change=='recommendation': e['recommendation']='reject'
    if change=='human': e['primary_quality']['selection_origin']='human'
    assert export(rebind(*data))['predictions']['rows'][0]['status']=='failed'


@pytest.mark.parametrize('change', ['state','signature','decode_kind','format','empty_versions'])
def test_unsupported_saved_records_remain_failure(change):
    data=fixture(); a=data[0]['assets'][0]; r=data[2]['rows'][0]
    if change=='state': a['clarity_evidence']['state']='uncertain'
    if change=='signature':
        for key in ('measurement_inputs','frozen_inputs'): r[key]['source_signature'][0][1]=r[key]['source_signature'][0][1][:3]
    if change=='decode_kind':
        a['clarity_evidence']['decode']['source_kind']='raw_embedded_thumbnail';r['source_kind']='raw_embedded_thumbnail'
    if change=='format': data[1]['rows'][0]['sources'][0]['format']='RAW'
    if change=='empty_versions': r['versions']={}
    assert export(rebind(*data))['predictions']['rows'][0]['status']=='failed'


def test_review_is_preserved_as_review():
    data=fixture();a=data[0]['assets'][0]
    a['local_recommendation']='review'; a['clarity_evidence'].update(recommendation='review',state='uncertain',reliability='insufficient')
    assert export(rebind(*data))['predictions']['rows'][0]['recommendation']=='review'


def test_reject_portrait_preserves_technical_decision():
    data=portrait_fixture();a=data[0]['assets'][0];e=a['clarity_evidence']
    a['local_recommendation']='reject';e.update(recommendation='reject',state='severe_blur')
    e['primary_quality'].update(recommendation='reject',state='severe_blur')
    assert export(rebind(*data))['predictions']['rows'][0]['recommendation']=='reject'


def test_diagnostics_preserve_regions_and_versions():
    data=fixture(); data[0]['assets'][0]['clarity_evidence']['regions']=[{'tile':[0,0],'state':'clear'}]
    result=export(rebind(*data))
    diagnostic=result['diagnostics']['rows'][0]
    assert diagnostic['saved_local_evidence']['regions']==[{'tile':[0,0],'state':'clear'}]


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}'])
def test_invalid_json_rejected(raw):
    with pytest.raises(oe.ContractError): fe.parse_json(raw)


def test_missing_receipt_is_never_reconstructed():
    data=fixture()
    with pytest.raises(oe.ContractError): fe.build_export(data[0],data[1],{},session_sha256=data[2]['session_sha256'],repo=ROOT)


def test_duplicate_receipt_mapping_refused():
    data=fixture();data[2]['rows'].append(deepcopy(data[2]['rows'][0]))
    with pytest.raises(oe.ContractError): export(data)


@pytest.mark.parametrize('value',[None,0,'false'])
def test_ambiguous_freshness_flag_is_not_clean(value):
    data=fixture();data[0]['assets'][0]['ai_focus_dirty']=value
    assert export(rebind(*data))['predictions']['rows'][0]['status']=='failed'


def test_missing_freshness_flag_fails():
    data=fixture();del data[0]['assets'][0]['ai_focus_dirty']
    assert export(rebind(*data))['predictions']['rows'][0]['status']=='failed'


def test_repeated_path_signature_must_match_fully():
    data=fixture()
    for key in ('measurement_inputs','frozen_inputs'): data[2]['rows'][0][key]['source_signature'][1][1][-1]=999
    assert export(data)['predictions']['rows'][0]['status']=='failed'


def test_windows_content_token_matches_actual_sample():
    data=fixture();r=data[2]['rows'][0]
    for key in ('measurement_inputs','frozen_inputs'):
        for item in r[key]['source_signature']: item[1].append(['sha256',oe.digest(b'wrong bytes')])
    assert export(data)['predictions']['rows'][0]['status']=='failed'


def test_unknown_reason_kept_even_when_stale():
    data=fixture();data[0]['assets'][0]['ai_focus_stale']=True
    result=export(rebind(*data))
    assert result['predictions']['rows'][0]['status']=='failed'
    assert 'synthetic_unknown_原因' in result['predictions']['rows'][0]['reasons']
