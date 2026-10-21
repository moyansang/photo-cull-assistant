"""Metadata-only synthetic templates, never real sample/accuracy evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import prepare_evaluation_inputs as prepare
import evaluation_inputs as inputs
import offline_evaluation as oe


@pytest.fixture
def docs(tmp_path):
    samples = {'schema_version': oe.SCHEMA, 'kind': 'samples', 'dataset_id': 'collection',
               'dataset_version': 'v2', 'guideline_version': 'unfrozen-v1', 'manifest_version': 'v2', 'evaluation_kind': 'synthetic',
               'rows': [{'asset_id': 'a', 'scene_type': 'unknown', 'evaluation_source_id': 's',
                         'capture_id': 'capture-a', 'variant_family_id': 'variant-a', 'comparison_group_id': 'group-a',
                         'scene_family_id': 'scene-a', 'sample_version': 'v2', 'split': 'development',
                         'source_authorization': 'generated-only test metadata', 'historical_exposure': 'synthetic', 'person_ids': [],
                         'sources': [{'source_id': 's', 'sha256': '1'*64, 'format': 'PNG', 'width': 640, 'height': 640}]}]}
    legacy = {'schema_version': prepare.LEGACY_SCHEMA, 'roots': {'local': str(tmp_path / '照片 不存在')},
              'entries': [{'asset_id': 'a', 'source_id': 's', 'root_id': 'local', 'relative_path': '中文/同名.png'}]}
    registration = {'schema_version': prepare.REGISTRATION_SCHEMA,
                    'member_roles': {'a': {'raw_source_id': None, 'jpeg_source_id': 's'}}}
    return samples, legacy, registration


def complete(docs):
    samples, legacy, registration = deepcopy(docs)
    # Explicit synthetic template choices are caller data, not inferred by adapter.
    samples['rows'][0]['scene_type'] = 'landscape'
    registration['manifest_additions'] = {'dataset_registry': [{'dataset_id': 'synthetic', 'dataset_version': 'v1',
        'source_url': 'local:generated', 'license': 'synthetic-only', 'attribution': 'test',
        'authorization': 'synthetic measurement', 'authorization_confirmed': True, 'historical_exposure': 'development'}],
        'registry_review': 'synthetic only', 'split_registry': [],
        'guideline_review': {'guideline_version': 'unfrozen-v1', 'frozen': True, 'evidence': 'synthetic test review'}}
    registration['sample_additions'] = {'a': {'session_id': None, 'burst_id': None}}
    registration['source_additions'] = {'a': {'s': {'dataset_id': 'synthetic', 'dataset_version': 'v1',
        'original_file_id': 'generated', 'original_sha256': '1'*64, 'preprocessing_history': []}}}
    registration['measurement_authorizations'] = [{'dataset_id': 'synthetic', 'dataset_version': 'v1',
        'scope': 'measurement', 'evidence': 'generated-only test authorization'}]
    return samples, legacy, registration


def test_no_photo_access_and_preserve_unknown(docs, monkeypatch):
    before = deepcopy(docs)
    def forbidden(*args, **kwargs):
        raise AssertionError('metadata preparation accessed filesystem/images')
    for method in ('open', 'stat', 'resolve', 'glob', 'rglob', 'iterdir', 'read_bytes'):
        monkeypatch.setattr(Path, method, forbidden)
    samples, paths, report = prepare.prepare(docs[0], docs[1], registration=docs[2])
    assert docs == before
    assert samples == docs[0]
    assert paths['bindings']['a']['sources']['s'].endswith('中文/同名.png')
    assert report['sample_count'] == 1 and report['image_files_read'] == 0
    assert report['status'] == 'blocked'
    gaps = report['rows'][0]['problems']
    for gap in ('dataset_registry_missing', 'registry_review_missing', 'guideline_freeze_not_confirmed',
                'explicit_subject_scope_required_no_scene_inference', 's:source_metadata_missing:original_sha256',
                's:source_metadata_missing:preprocessing_history'):
        assert gap in gaps


def test_explicit_registration_and_native_structural_path_contract(docs):
    samples, legacy, registration = complete(docs)
    result, paths, report = prepare.prepare(samples, legacy, registration=registration)
    assert report['status'] == 'metadata_prepared'
    assert report['authorization_granted'] is False
    assert report['preflight'] == 'not_run'
    assert set(inputs.validate_samples(result)) == {'a'}
    assert paths['manifest_sha256'] == oe.canonical_hash(result)
    assert inputs.validate_path_map(result, paths)['a']['s'] == Path(paths['bindings']['a']['sources']['s'])
    assert result['rows'][0]['sources'][0]['sha256'] == samples['rows'][0]['sources'][0]['sha256']
    assert 'annotations' not in result and 'labels' not in result


def test_download_authorization_is_not_measurement_authorization(docs):
    samples, legacy, registration = complete(docs)
    registration.pop('measurement_authorizations')
    registration['manifest_additions']['dataset_registry'][0]['authorization'] = 'download only'
    before = deepcopy(registration)
    result, _, report = prepare.prepare(samples, legacy, registration=registration)
    assert result['dataset_registry'][0]['authorization_confirmed'] is False
    assert registration == before
    assert report['status'] == 'blocked'
    assert 's:explicit_measurement_authorization_missing' in report['rows'][0]['problems']


def test_existing_manifest_registry_also_gated_without_permission(docs):
    samples, legacy, registration = complete(docs)
    samples.update(registration.pop('manifest_additions'))
    registration.pop('measurement_authorizations')
    result, _, _ = prepare.prepare(samples, legacy, registration=registration)
    assert samples['dataset_registry'][0]['authorization_confirmed'] is True
    assert result['dataset_registry'][0]['authorization_confirmed'] is False


@pytest.mark.parametrize('relative', ['../bad.png', 'a/../../bad.png', 'C:\\bad.png', 'C:bad.png', '/bad.png', '\\bad.png', 'a\\..\\bad.png', '.', '\x00x'])
def test_lexical_traversal_rejected(relative, docs):
    docs[1]['entries'][0]['relative_path'] = relative
    with pytest.raises(oe.ContractError):
        prepare.prepare(docs[0], docs[1])


def test_windows_roots_are_lexical_on_linux(docs):
    result, paths, _ = prepare.prepare(docs[0], docs[1], roots={'local': r'C:\明确授权\原图'})
    assert paths['bindings']['a']['sources']['s'] == 'C:\\明确授权\\原图\\中文\\同名.png'
    assert result == docs[0]


@pytest.mark.parametrize('location', ['legacy', 'entry', 'registration', 'source', 'roles'])
def test_unknown_fields_rejected(docs, location):
    samples, legacy, registration = docs
    if location == 'legacy':
        legacy['guessed'] = 'value'
    elif location == 'entry':
        legacy['entries'][0]['guessed'] = 'value'
    elif location == 'registration':
        registration['guessed'] = 'value'
    elif location == 'source':
        registration['source_additions'] = {'a': {'s': {'sha256': '2'*64}}}
    else:
        registration['member_roles']['a']['guessed'] = 'value'
    with pytest.raises(oe.ContractError, match='unsupported fields'):
        prepare.prepare(samples, legacy, registration=registration)


def test_no_existing_metadata_replaced(docs):
    docs[2]['source_additions'] = {'a': {'s': {'width': 800}}}
    with pytest.raises(oe.ContractError, match='cannot replace existing width'):
        prepare.prepare(docs[0], docs[1], registration=docs[2])


def test_missing_binding_keeps_denominator(docs):
    docs[1]['entries'] = []
    _, paths, report = prepare.prepare(docs[0], docs[1])
    assert paths['bindings'] == {}
    assert report['sample_count'] == 1 and report['bound_sample_count'] == 0
    assert 's:path_binding_missing' in report['rows'][0]['problems']


def test_roles_not_inferred_from_file_extension(docs):
    _, paths, report = prepare.prepare(docs[0], docs[1])
    assert paths['bindings']['a']['jpeg_source_id'] is None
    assert 'measurement_member_roles_incomplete' in report['rows'][0]['problems']


def test_manifest_binding_rejected(docs):
    docs[1]['manifest_sha256'] = '0'*64
    with pytest.raises(oe.ContractError, match='manifest_sha256 mismatch'):
        prepare.prepare(docs[0], docs[1])


def test_pair_explicit_ids_no_filename_inference(docs):
    samples, legacy, registration = docs
    samples['rows'][0]['sources'].append({'source_id': 'r', 'format': 'DNG', 'sha256': '2'*64})
    legacy['entries'].append({'asset_id': 'a', 'source_id': 'r', 'root_id': 'local', 'relative_path': 'different-name.dng'})
    registration['member_roles']['a'] = {'primary_source_id': 'r', 'raw_source_id': 'r', 'jpeg_source_id': 's'}
    _, paths, report = prepare.prepare(samples, legacy, registration=registration)
    assert paths['bindings']['a']['primary_path'].endswith('different-name.dng')
    assert set(paths['bindings']['a']['sources']) == {'s', 'r'}
    assert report['sample_count'] == 1


def test_cli_exclusive_new_outputs_and_input_hashes_unchanged(docs, tmp_path):
    samples, legacy, registration = docs
    for name, document in [('old-samples.json', samples), ('old-bindings.json', legacy), ('registration.json', registration)]:
        (tmp_path / name).write_text(json.dumps(document), encoding='utf-8')
    before = {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}
    args = ['--samples', str(tmp_path/'old-samples.json'), '--bindings', str(tmp_path/'old-bindings.json'),
            '--registration', str(tmp_path/'registration.json'), '--output', str(tmp_path/'new')]
    assert prepare.main(args) == 3
    assert {p.name: p.read_bytes() for p in tmp_path.glob('*.json')} == before
    assert (tmp_path/'new'/'preparation-report.json').is_file()
    with pytest.raises(SystemExit) as error:
        prepare.main(args)
    assert error.value.code == 2
    assert {p.name: p.read_bytes() for p in tmp_path.glob('*.json')} == before


def test_cli_refuses_output_in_photo_root(docs, tmp_path):
    samples, legacy, _ = docs
    for name, doc in [('samples.json', samples), ('bindings.json', legacy)]:
        (tmp_path/name).write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(oe.ContractError, match='overlaps photo root'):
        prepare.run(tmp_path/'samples.json', tmp_path/'bindings.json', Path(legacy['roots']['local'])/'out')


def test_known_original_identity_and_unfrozen_guideline_preserved(docs):
    samples, legacy, registration = complete(docs)
    samples['rows'][0]['scene_type'] = 'unknown'
    samples['rows'][0]['sources'][0].update(registration.pop('source_additions')['a']['s'])
    registration['manifest_additions'].pop('guideline_review')
    before = deepcopy(samples)
    result, _, report = prepare.prepare(samples, legacy, registration=registration)
    assert result['rows'][0]['sources'][0] == before['rows'][0]['sources'][0]
    assert samples == before
    assert result['guideline_version'] == 'unfrozen-v1'
    assert result['rows'][0]['scene_type'] == 'unknown'
    assert report['status'] == 'blocked'
    assert 'guideline_freeze_not_confirmed' in report['global_problems']


@pytest.mark.parametrize('target', ['asset_id', 'source_id', 'root_id'])
def test_unknown_entry_identity_rejected(docs, target):
    docs[1]['entries'][0][target] = 'unlisted'
    with pytest.raises(oe.ContractError):
        prepare.prepare(docs[0], docs[1])


def test_duplicate_entry_rejected(docs):
    docs[1]['entries'].append(deepcopy(docs[1]['entries'][0]))
    with pytest.raises(oe.ContractError, match='duplicate source binding'):
        prepare.prepare(docs[0], docs[1])


def test_authorization_download_scope_rejected(docs):
    samples, legacy, registration = complete(docs)
    registration['measurement_authorizations'][0]['scope'] = 'download'
    with pytest.raises(oe.ContractError, match='explicit scope'):
        prepare.prepare(samples, legacy, registration=registration)
