"""Synthetic acquisition metadata only: never real sample data or authorization."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_prepare_evaluation_inputs import docs, complete, prepare, oe  # noqa: F401


@pytest.fixture
def acquisition(docs, tmp_path):
    samples, legacy, registration = deepcopy(docs)
    entry = legacy['entries'][0]
    entry['root'] = entry.pop('root_id')
    entry.update(sha256='1' * 64, bytes=123)
    legacy['note'] = 'Synthetic acquisition only; download is not measurement permission.'
    legacy['manifest_sha256'] = oe.canonical_hash(samples)
    roots = {'schema_version': prepare.LOCAL_ROOTS_SCHEMA,
             'roots': {'local': str(tmp_path / '中文 空格' / '不存在')}}
    return samples, legacy, roots, registration


def test_acquisition_preserves_provenance_and_missing_bytes(acquisition, monkeypatch):
    samples, legacy, roots, registration = acquisition
    before = deepcopy(acquisition)
    def forbidden(*args, **kwargs):
        raise AssertionError('photo filesystem access forbidden')
    with monkeypatch.context() as patch:
        for method in ('open', 'stat', 'resolve', 'glob', 'rglob', 'iterdir', 'read_bytes'):
            patch.setattr(Path, method, forbidden)
        result, paths, report = prepare.prepare(samples, legacy, roots=roots, registration=registration)
    assert acquisition == before
    assert result == samples
    assert report['status'] == 'blocked'
    assert report['authorization_granted'] is False
    assert report['manifest_binding_check'] == 'metadata_match'
    assert report['acquisition_provenance'] == {'bindings': legacy, 'roots_document': roots}
    identity = report['identity_checks'][0]
    assert identity['asset_id'] == 'a' and identity['source_id'] == 's'
    assert identity['file_identity'] == 'not_verified_no_photo_access'
    assert identity['checks']['sha256']['status'] == 'metadata_match'
    assert identity['checks']['bytes'] == {'status': 'unverified', 'binding_value': 123,
        'manifest_value': None, 'binding_field_present': True, 'manifest_field_present': False,
        'missing': ['manifest']}
    assert Path(paths['bindings']['a']['sources']['s']).parts[-2:] == ('中文', '同名.png')


def test_matching_bytes_and_hash_are_only_metadata_matches(acquisition):
    samples, legacy, roots, _ = acquisition
    samples['rows'][0]['sources'][0]['bytes'] = 123
    legacy['manifest_sha256'] = oe.canonical_hash(samples)
    _, _, report = prepare.prepare(samples, legacy, roots=roots)
    assert all(c['status'] == 'metadata_match' for c in report['identity_checks'][0]['checks'].values())
    assert report['preflight'] == 'not_run'
    assert report['image_files_read'] == 0


@pytest.mark.parametrize('field,value', [('sha256', '2'*64), ('bytes', 124)])
def test_identity_conflict_rejected(acquisition, field, value):
    samples, legacy, roots, _ = acquisition
    samples['rows'][0]['sources'][0]['bytes'] = 123
    legacy['manifest_sha256'] = oe.canonical_hash(samples)
    legacy['entries'][0][field] = value
    with pytest.raises(oe.ContractError, match='binding ' + field + ' mismatch'):
        prepare.prepare(samples, legacy, roots=roots)


@pytest.mark.parametrize('field,value', [('sha256', 'bad'), ('sha256', 1), ('bytes', True),
                                       ('bytes', -1), ('bytes', 1.5), ('bytes', '123')])
def test_invalid_identity_values_rejected(acquisition, field, value):
    samples, legacy, roots, _ = acquisition
    legacy['entries'][0][field] = value
    with pytest.raises(oe.ContractError, match='invalid ' + field):
        prepare.prepare(samples, legacy, roots=roots)


def test_missing_binding_identity_explicitly_unverified(acquisition):
    samples, legacy, roots, _ = acquisition
    legacy.pop('manifest_sha256')
    legacy['entries'][0].pop('sha256')
    legacy['entries'][0].pop('bytes')
    _, _, report = prepare.prepare(samples, legacy, roots=roots)
    assert report['manifest_binding_check'] == 'unverified_missing_binding_hash'
    checks = report['identity_checks'][0]['checks']
    assert checks['sha256']['status'] == checks['bytes']['status'] == 'unverified'
    assert checks['sha256']['missing'] == ['binding']
    assert checks['bytes']['missing'] == ['binding', 'manifest']


def test_alias_equal_accepted_conflict_rejected(acquisition):
    samples, legacy, roots, _ = acquisition
    legacy['entries'][0]['root_id'] = 'local'
    prepare.prepare(samples, legacy, roots=roots)
    legacy['entries'][0]['root_id'] = 'other'
    with pytest.raises(oe.ContractError, match='root and root_id conflict'):
        prepare.prepare(samples, legacy, roots=roots)


@pytest.mark.parametrize('change', ['version', 'extra', 'missing'])
def test_roots_wrapper_strict(acquisition, change):
    samples, legacy, roots, _ = acquisition
    if change == 'version':
        roots['schema_version'] = 'sample-local-roots-v999'
    elif change == 'extra':
        roots['ignored'] = True
    else:
        roots.pop('roots')
    with pytest.raises(oe.ContractError):
        prepare.prepare(samples, legacy, roots=roots)


@pytest.mark.parametrize('change', ['duplicate', 'traversal', 'manifest', 'unknown'])
def test_acquisition_rejects_unsafe_metadata(acquisition, change):
    samples, legacy, roots, _ = acquisition
    if change == 'duplicate':
        legacy['entries'].append(deepcopy(legacy['entries'][0]))
    elif change == 'traversal':
        legacy['entries'][0]['relative_path'] = '../escape.png'
    elif change == 'manifest':
        legacy['manifest_sha256'] = '0' * 64
    else:
        legacy['entries'][0]['unknown'] = 'not silently discarded'
    with pytest.raises(oe.ContractError):
        prepare.prepare(samples, legacy, roots=roots)


def test_versioned_roots_cli_keeps_input_bytes(acquisition, tmp_path):
    samples, legacy, roots, registration = acquisition
    paths = {}
    for name, document in [('samples', samples), ('bindings', legacy), ('roots', roots), ('registration', registration)]:
        paths[name] = tmp_path / (name + '.json')
        paths[name].write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
    before = {name: path.read_bytes() for name, path in paths.items()}
    args = [arg for name, path in paths.items() for arg in ('--' + name, str(path))]
    assert prepare.main(args + ['--output', str(tmp_path / '新 输出')]) == 3
    assert {name: path.read_bytes() for name, path in paths.items()} == before
    report = json.loads((tmp_path / '新 输出' / 'preparation-report.json').read_text(encoding='utf-8'))
    assert report['acquisition_provenance']['bindings'] == legacy
    assert report['identity_checks'][0]['checks']['bytes']['status'] == 'unverified'


def test_versioned_roots_output_overlap_refused(acquisition, tmp_path):
    samples, legacy, roots, _ = acquisition
    paths = []
    for name, document in [('samples', samples), ('bindings', legacy), ('roots', roots)]:
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(document), encoding='utf-8')
        paths.append(path)
    with pytest.raises(oe.ContractError, match='overlaps photo root'):
        prepare.run(paths[0], paths[1], Path(roots['roots']['local'])/'out', roots_path=paths[2])


def test_cli_duplicate_json_fields_rejected(tmp_path):
    path = tmp_path / 'duplicate.json'
    path.write_text('{"schema_version":"sample-bindings-v1","schema_version":"other"}', encoding='utf-8')
    with pytest.raises(oe.ContractError, match='duplicate JSON field: schema_version'):
        prepare.run(path, path, tmp_path / 'new')
    assert not (tmp_path / 'new').exists()


@pytest.mark.parametrize('change', ['sha256', 'bytes', 'same_path'])
def test_second_source_checked_and_lexical_alias_rejected(acquisition, change):
    samples, legacy, roots, _ = acquisition
    source = deepcopy(samples['rows'][0]['sources'][0])
    source.update(source_id='s2', sha256='2' * 64, bytes=456)
    samples['rows'][0]['sources'].append(source)
    entry = deepcopy(legacy['entries'][0])
    entry.update(source_id='s2', relative_path='中文/另一张.png', sha256='2' * 64, bytes=456)
    legacy['entries'].append(entry)
    legacy['manifest_sha256'] = oe.canonical_hash(samples)
    if change == 'same_path':
        entry['relative_path'] = '中文/同名.png'
        message = 'source path reused'
    else:
        entry[change] = '3' * 64 if change == 'sha256' else 789
        message = 'a/s2: binding ' + change + ' mismatch'
    with pytest.raises(oe.ContractError, match=message):
        prepare.prepare(samples, legacy, roots=roots)
