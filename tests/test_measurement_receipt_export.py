"""Adversarial v2 format tests; capture-path image tests live separately."""
from copy import deepcopy

import pytest

from test_frozen_prediction_export import fixture, portrait_fixture, rebind, export as legacy_export, fe, oe


def export(data):
    session, samples, receipt = data
    if receipt['schema_version'] != fe.CAPTURE_SCHEMA:
        return legacy_export(data)
    proof = {'session_sha256': receipt['session_sha256'], 'samples_sha256': oe.canonical_hash(samples),
             'provenance_sha256': oe.canonical_hash(receipt)}
    return fe.build_export(session, samples, receipt, session_sha256=receipt['session_sha256'], completion_proof=proof)


def bind(data):
    rebind(*data)
    receipt = data[2]
    receipt['completion'] = {k: receipt[k] for k in ('session_sha256', 'manifest_sha256')}
    receipt['completion']['status'] = 'complete'
    for row in receipt['rows']:
        asset = data[0]['assets'][row['asset_index']]
        row['capture']['evidence_sha256'] = oe.canonical_hash(asset.get('clarity_evidence'))
        row['capture']['focus_scope_sha256'] = oe.canonical_hash(asset.get('focus_scope'))
    bind_records(data)
    return data


def bind_records(data):
    receipt = data[2]
    captures = [r['capture'] for r in receipt['rows']]
    records = {
        'configuration': [c.get('settings_snapshot') for c in captures],
        'decode_configuration': [{'stages': c.get('stages'),
            'contract': 'image_decode.source-first-v1:actual-decode-evidence'} for c in captures],
        'dependency_snapshot': {'kind': 'actual_dependency_snapshot', 'production_dependency_lock': False,
            'measurements': [c.get('dependency_snapshot') for c in captures]}}
    for name, value in records.items():
        content = __import__('json').dumps(value, sort_keys=True)
        receipt['records'][name] = {'content': content, 'sha256': oe.digest(content.encode())}
    return data


def v2(data=None):
    data = fixture() if data is None else data
    session, samples, receipt = data
    samples['rows'][0]['sources'] = samples['rows'][0]['sources'][:1]
    receipt['schema_version'] = fe.CAPTURE_SCHEMA
    receipt['code_identity'] = dict(source_git=receipt['source_git'], dirty=False, verified=True)
    receipt['recording_status'] = 'complete'
    receipt['records']['dependency_snapshot'] = receipt['records'].pop('dependency_lock')
    row = receipt['rows'][0]; asset = session['assets'][0]
    path = row['source_member']
    row['source_members'] = {samples['rows'][0]['sources'][0]['source_id']: path}
    settings = {'global': {}, 'photo': {}, 'versions': {}}
    state = dict(settings=settings, confirmation=None, source_hashes={path: row['input_sha256']},
                 mode=asset['analysis_mode'], body_check=False)
    row['capture'] = dict(status='complete', kind='new_measurement', measured_at=row['measured_at'],
        finished_at=row['measured_at'], dependency_snapshot={'kind':'actual_dependency_snapshot',
            'production_dependency_lock':False,'installed':{},'loaded':{},'python':'synthetic_metadata_stub','platform':'synthetic'}, settings_snapshot=settings, confirmation_snapshot=None,
        state_before=deepcopy(state), state_after=deepcopy(state), stages=[dict(
            measured_at=row['measured_at'], finished_at=row['measured_at'],
            name='landscape' if asset['analysis_mode']=='landscape' else 'face', source_member=path,
            source_kind=row['source_kind'], input_sha256=row['input_sha256'],
            decode=deepcopy(asset['clarity_evidence']['decode']), options={},
            decoder_configuration={'kind':'actual_decoder_call','injected_backend':True,'backend':'explicit_test_stub','backend_version':'synthetic','source_kind':'standard_image'}, image_size=[512,512],
            coordinate_space='normalized_display', status='complete',
            regions=[] if asset['analysis_mode']=='landscape' else [[asset['clarity_evidence']['automatic_subject']['box'],None]],
            settings_before=deepcopy(settings),settings_after=deepcopy(settings),
            confirmation_before=None, confirmation_after=None)])
    return bind(data)


def test_v2_landscape_preserves_suggestion_and_discloses_dependency_snapshot():
    result = export(v2())
    assert result['predictions']['rows'][0]['status'] == 'ok'
    assert result['diagnostics']['dependency_record_kind'] == 'actual_runtime_snapshot_not_production_lock'
    assert result['summary']['real_world_accuracy']['status'] == '尚未测量'


def test_v2_automatic_face_keeps_old_proof_requirements():
    data = v2(portrait_fixture())
    assert export(data)['predictions']['rows'][0]['status'] == 'ok'
    data[0]['assets'][0]['clarity_evidence']['automatic_subject']['candidate_count'] = 2
    assert export(bind(data))['predictions']['rows'][0]['status'] == 'failed'


@pytest.mark.parametrize('field,value', [('dirty', True), ('verified', False), ('source_git', '1'*40)])
def test_v2_rejects_unverifiable_code_identity(field, value):
    data = v2(); data[2]['code_identity'][field] = value
    with pytest.raises(oe.ContractError): export(data)


@pytest.mark.parametrize('field,value', [('status','pending'), ('session_sha256','1'*64), ('manifest_sha256','1'*64)])
def test_v2_rejects_partial_publication(field,value):
    data=v2(); data[2]['completion'][field]=value
    with pytest.raises(oe.ContractError): export(data)


@pytest.mark.parametrize('status', ['cache_gap','failed','cancelled','partial','changed',None])
def test_v2_incomplete_capture_is_failed_not_keep(status):
    data=v2(); data[2]['rows'][0]['capture']['status']=status
    result=export(data)['predictions']['rows'][0]
    assert result['status']=='failed' and result['recommendation'] is None


@pytest.mark.parametrize('change', ['state', 'evidence', 'scope', 'stage_hash', 'stage_decode', 'stage_name',
                                  'stage_size','stage_coordinates','stage_status','member_map','selected_member','times'])
def test_v2_capture_tampering_fails(change):
    data=v2(); row=data[2]['rows'][0]; cap=row['capture']; stage=cap['stages'][0]
    if change=='state': cap['state_after']['body_check']=True
    if change=='evidence': cap['evidence_sha256']='1'*64
    if change=='scope': cap['focus_scope_sha256']='1'*64
    if change=='stage_hash': stage['input_sha256']='1'*64
    if change=='stage_decode': stage['decode']['source_path']='/other'
    if change=='stage_name': stage['name']='unknown'
    if change=='stage_size': stage['image_size']=[0,512]
    if change=='stage_coordinates': stage['coordinate_space']='unknown'
    if change=='stage_status': stage['status']='cached'
    if change=='member_map': row['source_members']={}
    if change=='selected_member': row['source_members']={next(iter(row['source_members'])):'/other'}
    if change=='times': cap['finished_at']='2026-10-19T00:00:00+00:00'
    assert export(bind_records(data))['predictions']['rows'][0]['status']=='failed'


def manual_v2(body=False, base=None):
    data=v2(portrait_fixture()) if base is None else base; asset=data[0]['assets'][0]; ev=asset['clarity_evidence']
    row=data[2]['rows'][0]; cap=row['capture']; inputs=row['measurement_inputs']
    region=deepcopy(ev['automatic_selection']['regions'][0])
    regions=[region]
    if body: regions.append({'id':'body:demo','kind':'body','box':[.1,.1,.4,.7]})
    versions={'roi':2,'detection':'synthetic-detector-v1','face':row['versions']['face'],'body':'synthetic-body-v1'}
    photo={'selected_faces':[region['box']]}
    if body: photo['selected_bodies']=[regions[1]['box']]
    settings={'global':{},'photo':photo,'versions':versions}
    confirmation={'version':row['versions']['primary_quality'],'human_confirmed':True,
        'source_signature':deepcopy(inputs['source_signature']), 'parameters_digest':oe.canonical_hash(settings),
        'regions':regions}
    confirmation['digest']=oe.canonical_hash(confirmation)
    photo['primary_subject_confirmation']=deepcopy(confirmation)
    parts=['face','body'] if body else ['face']
    measured={'global':{},'photo':{k:v for k,v in photo.items() if k!='primary_subject_confirmation'},
        'confirmation':confirmation,'versions':list(versions.values()),'enabled_parts':parts}
    if body: measured['manual_body_scope']='explicit-body-scope-v1'
    inputs.update(parameters_digest=oe.canonical_hash(measured),enabled_parts=parts,
                  manual_face_scope_contract=row['versions']['manual_face_scope'])
    row['frozen_inputs']=deepcopy(inputs)
    ev.update(measurement_inputs=deepcopy(inputs),enabled_parts=parts,measurement_confirmation_digest=confirmation['digest'])
    ev.pop('automatic_selection'); ev.pop('measurement_selection_digest'); ev.pop('automatic_subject')
    ev['primary_quality'].update(selection_origin='human',selection_digest=confirmation['digest'],
        confirmation_digest=confirmation['digest'],enabled_parts=parts,
        subjects=[{'id':r['id'],'region_ids':[r['id']]} for r in regions])
    scope=asset['focus_scope'];scope.update(selection_origin='human',selection_digest=confirmation['digest'],
        confirmation_digest=confirmation['digest'],enabled_parts=parts,
        manual_face_scope_contract=row['versions']['manual_face_scope'],
        settings_digest=oe.canonical_hash([confirmation['parameters_digest'],row['versions']['manual_face_scope']]),
        subjects=[{'id':r['id'],'kind':r['kind']} for r in regions])
    scope['digest']=oe.canonical_hash({k:v for k,v in scope.items() if k!='digest'})
    cap.update(confirmation_snapshot=deepcopy(confirmation),settings_snapshot=deepcopy(settings))
    for state in (cap['state_before'],cap['state_after']):
        state.update(settings=deepcopy(settings),confirmation=deepcopy(confirmation),body_check=body)
    if body:
        body_stage=deepcopy(cap['stages'][0]);body_stage['name']='body';cap['stages'].append(body_stage)
    for stage in cap['stages']:
        stage.update(settings_before=deepcopy(settings), settings_after=deepcopy(settings),
            confirmation_before=deepcopy(confirmation), confirmation_after=deepcopy(confirmation),
            regions=[[region['box'],None]] if stage['name']=='face' else [regions[1]['box']])
    return bind(data)


@pytest.mark.parametrize('body',[False,True])
def test_v2_manual_snapshot_and_independent_body_stage(body):
    result=export(manual_v2(body))
    assert result['predictions']['rows'][0]['status']=='ok',result['diagnostics']


@pytest.mark.parametrize('change',['snapshot','digest','settings','body_stage','scope_subject','policy_subject'])
def test_v2_manual_missing_or_stale_binding_failed(change):
    data=manual_v2(True);a=data[0]['assets'][0];cap=data[2]['rows'][0]['capture']
    if change=='snapshot':cap['confirmation_snapshot']=None
    if change=='digest':cap['confirmation_snapshot']['digest']='1'*64
    if change=='settings':cap['settings_snapshot']['global']['scale_factor']=9
    if change=='body_stage':cap['stages']=cap['stages'][:1]
    if change=='scope_subject':a['focus_scope']['subjects'].pop()
    if change=='policy_subject':a['clarity_evidence']['primary_quality']['subjects'].pop()
    assert export(bind(data))['predictions']['rows'][0]['status']=='failed'


def test_v1_never_gains_manual_or_body_support_from_extra_capture_field():
    data=manual_v2(True);data[2]['schema_version']=fe.PROVENANCE_SCHEMA
    data[2]['records']['dependency_lock']=data[2]['records'].pop('dependency_snapshot')
    assert export(bind_records(data))['predictions']['rows'][0]['status']=='failed'


def test_v2_face_and_body_independent_raw_jpg_members():
    data=v2(portrait_fixture()); session,samples,receipt=data
    asset=session['assets'][0]; row=receipt['rows'][0]; cap=row['capture']
    raw_path='C:/合成 样本/photo.RW2'; raw_sha=oe.digest(b'SYNTHETIC RAW stage bytes')
    asset['raw_path']=raw_path
    session['source_stats'][raw_path]=[200,123]
    row['measurement_inputs']['source_signature'].append(['raw_path',[raw_path,200,123,124,1,3]])
    samples['rows'][0]['sources'].append({'source_id':'raw-pair','sha256':raw_sha,'format':'RW2'})
    row['source_members']['raw-pair']=raw_path
    for state in (cap['state_before'],cap['state_after']): state['source_hashes'][raw_path]=raw_sha
    scope=asset['focus_scope']; source_digest=oe.canonical_hash(row['measurement_inputs']['source_signature'])
    scope.update(source_digest=source_digest,photo_identity='photo:'+source_digest)
    data=manual_v2(True,data); cap=row['capture']; body=cap['stages'][1]
    body.update(source_member=raw_path,source_kind='raw_full_decode',input_sha256=raw_sha,
        decode={'version':row['versions']['decode'],'source_path':raw_path,'source_kind':'raw_full_decode'})
    body['decoder_configuration'].update(source_kind='raw_full_decode', postprocess={'use_camera_wb':True,
        'half_size':False,'no_auto_bright':True,'output_bps':8}, orientation='LibRaw-postprocess-only',unpack=False)
    result=export(bind(data))
    assert result['predictions']['rows'][0]['status']=='ok',result['diagnostics']
    body['input_sha256']=row['input_sha256']
    assert export(bind_records(data))['predictions']['rows'][0]['status']=='failed'


def test_landscape_cannot_borrow_a_face_stage():
    data=v2();data[2]['rows'][0]['capture']['stages'][0]['name']='face'
    assert export(bind_records(data))['predictions']['rows'][0]['status']=='failed'


def test_automatic_face_cannot_borrow_an_independent_body_stage():
    data = v2(portrait_fixture())
    stage = deepcopy(data[2]['rows'][0]['capture']['stages'][0])
    stage['name'] = 'body'
    data[2]['rows'][0]['capture']['stages'].append(stage)
    assert export(bind(data))['predictions']['rows'][0]['status'] == 'failed'


@pytest.mark.parametrize('record', ['configuration', 'decode_configuration', 'dependency_snapshot'])
def test_v2_records_cannot_describe_unrelated_capture(record):
    data = v2()
    content = '{}'
    data[2]['records'][record] = {'content': content, 'sha256': oe.digest(content.encode())}
    with pytest.raises(oe.ContractError, match='not bound'):
        export(data)


@pytest.mark.parametrize('change', ['settings_before', 'settings_after', 'confirmation_before', 'confirmation_after', 'regions', 'measured_at', 'finished_at'])
def test_v2_stage_observation_cannot_differ_from_capture(change):
    data = manual_v2(True)
    stage = data[2]['rows'][0]['capture']['stages'][0]
    stage[change] = '2025-01-01T00:00:00+00:00' if change.endswith('_at') else None
    assert export(bind_records(data))['predictions']['rows'][0]['status'] == 'failed'


def write_completed(data, path):
    import json
    path.mkdir()
    files = {}
    for name, document in zip(('scan-session.json','samples.json','measurement-receipt.json'), data):
        raw = json.dumps(document, ensure_ascii=False).encode()
        if name == 'scan-session.json':
            data[2]['session_sha256'] = oe.digest(raw)
            data[2]['completion']['session_sha256'] = oe.digest(raw)
        (path/name).write_bytes(raw)
        files[name] = oe.digest(raw)
    (path/'complete.json').write_text(json.dumps({'schema_version':'measurement-freeze-v1','status':'complete','files':files}))


def test_completed_set_reader_requires_external_marker(tmp_path):
    data = v2(); path = tmp_path/'completed'; write_completed(data,path)
    assert fe.load_completed_set(path)['predictions']['rows'][0]['status'] == 'ok'
    (path/'complete.json').unlink()
    with pytest.raises(OSError): fe.load_completed_set(path)


@pytest.mark.parametrize('change', ['bytes','pending','staging'])
def test_completed_set_reader_rejects_incomplete_sets(tmp_path,change):
    import json
    data=v2();path=tmp_path/('.output.staging-run' if change=='staging' else 'completed');write_completed(data,path)
    if change=='bytes': (path/'samples.json').write_text('{}')
    if change=='pending':
        marker=json.loads((path/'complete.json').read_text());marker['status']='pending'
        (path/'complete.json').write_text(json.dumps(marker))
    with pytest.raises(oe.ContractError):fe.load_completed_set(path)


def test_v2_direct_builder_without_external_completion_fails():
    data=v2()
    with pytest.raises(oe.ContractError,match='completed-set proof'):
        fe.build_export(*data,session_sha256=data[2]['session_sha256'])


@pytest.mark.parametrize('configuration', [None, {}, {'status':'unverifiable_cached_decode'}, {'kind':'configuration_unverifiable'}, {'kind':'actual_decoder_call','status':'unverifiable'}])
def test_v2_requires_actual_decoder_configuration(configuration):
    data=v2(); data[2]['rows'][0]['capture']['stages'][0]['decoder_configuration']=configuration
    assert export(bind_records(data))['predictions']['rows'][0]['status']=='failed'


def test_v2_automatic_stage_must_use_saved_detector_region():
    data=v2(portrait_fixture())
    data[2]['rows'][0]['capture']['stages'][0]['regions'][0][0]=[.2,.2,.1,.1]
    assert export(bind_records(data))['predictions']['rows'][0]['status']=='failed'


def test_v2_complete_measurement_cannot_omit_dependency_snapshot():
    data=v2();data[2]['rows'][0]['capture'].pop('dependency_snapshot')
    assert export(bind_records(data))['predictions']['rows'][0]['status']=='failed'


@pytest.mark.parametrize('field', ['source_kind','backend','backend_version'])
def test_v2_decoder_config_must_identify_actual_backend_and_source(field):
    data=v2();data[2]['rows'][0]['capture']['stages'][0]['decoder_configuration'].pop(field)
    assert export(bind_records(data))['predictions']['rows'][0]['status']=='failed'
