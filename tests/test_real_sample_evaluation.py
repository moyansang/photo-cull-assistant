"""Generated PNGs exercise the production scorer, not real-photo acceptance.

Only code_identity is an explicitly marked dependency stub for dirty test trees;
clean-HEAD command replay is recorded separately in the engineering evidence.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import real_sample_evaluation as batch
from ai_cull_assistant import measurement_receipts as mr
from ai_cull_assistant.screening import screen_assets


@pytest.fixture(autouse=True)
def identity_dependency_stub(monkeypatch):
    identity = mr.code_identity()
    identity.update(verified=True, dirty=False, test_stub='SYNTHETIC test identity dependency only')
    monkeypatch.setattr(mr, 'code_identity', lambda: deepcopy(identity))
    monkeypatch.setattr(batch, 'code_identity', lambda: deepcopy(identity))


def inputs(tmp_path, count=2):
    rows, bindings = [], {}
    for i in range(count):
        path = tmp_path / '输入 图片' / str(i) / '同名 空格.png'
        path.parent.mkdir(parents=True)
        yy, xx = np.indices((640, 640))
        pixels = np.repeat((((xx // (5 + i) + yy // 5) % 2) * (255-i)).astype(np.uint8)[..., None], 3, axis=2)
        Image.fromarray(pixels).save(path)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        aid, sid = f'asset-{i}', f'source-{i}'
        rows.append(dict(asset_id=aid, capture_id=f'capture-{i}', variant_family_id=f'variant-{i}',
            comparison_group_id=f'group-{i}', scene_family_id=f'scene-{i}', sample_version='1',
            split='development', scene_type='landscape', source_authorization='generated pixels by test',
            historical_exposure='synthetic development', evaluation_source_id=sid, person_ids=[],
            session_id='synthetic-session', burst_id=None, exception_tags=[],
            sources=[dict(source_id=sid, sha256=sha, format='PNG', dataset_id='generated', dataset_version='1',
                original_file_id=f'generated-{i}', original_sha256=sha, width=640, height=640, preprocessing_history=[])]))
        bindings[aid] = dict(primary_path=str(path), sources={sid: str(path)}, jpeg_source_id=sid, raw_source_id=None)
    samples = dict(schema_version=batch.oe.SCHEMA, kind='samples', dataset_id='synthetic-batch', dataset_version='1',
        guideline_version='1', manifest_version='1', evaluation_kind='synthetic', rows=rows, split_registry=[],
        registry_review='generated inputs only; no external history', dataset_registry=[dict(dataset_id='generated',
            dataset_version='1', source_url='local:generated-fixture', license='test generated pixels',
            attribution='synthetic test', authorization='generated test only', authorization_confirmed=True,
            historical_exposure='synthetic development')])
    paths = dict(schema_version='real-sample-path-map-v1', manifest_sha256=batch.oe.canonical_hash(samples), bindings=bindings)
    config = dict(schema_version=batch.CONFIG_SCHEMA, analysis_mode='landscape', body_check=False,
                  crop_settings={k: getattr(batch.CropSettings(), k) for k in batch.CROP_FIELDS})
    return samples, paths, config


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_actual_png_collection_freeze_export_full_manifest_and_same_names(tmp_path):
    samples, paths, config = inputs(tmp_path)
    before = {p: Path(p).read_bytes() for b in paths['bindings'].values() for p in b['sources'].values()}
    output = tmp_path / '输出 批次'
    audit = batch.run_batch(samples, paths, config, output)
    assert audit['status'] == 'complete'
    assert [r['status'] for r in audit['rows']] == ['success', 'success']
    assert audit['measurement_seconds'] > 0
    assert audit['total_seconds'] >= audit['measurement_seconds']
    assert audit['receipt_cost']['extra_decodes'] == 0
    assert audit['real_world_accuracy']['status'] == '尚未测量'
    session = read(output / 'frozen/scan-session.json')
    assert len({a['stem'] for a in session['assets']}) == 2
    assert all(Path(p).read_bytes() == raw for p, raw in before.items())
    assert read(output / 'export/annotations.json')['rows'] == []
    summary = read(output / 'export/summary.json')
    assert summary['overall']['n'] == 2
    assert summary['strata']['dataset_id']['generated']['n'] == 2
    marker = read(output / 'complete.json')
    assert marker['publication_complete'] is True
    for name, sha in marker['files'].items():
        assert batch.oe.digest((output / name).read_bytes()) == sha


def test_partial_missing_and_invalid_bytes_not_hidden_or_labeled_blurry(tmp_path):
    samples, paths, config = inputs(tmp_path, 3)
    Path(paths['bindings']['asset-1']['primary_path']).unlink()
    Path(paths['bindings']['asset-2']['primary_path']).write_bytes(b'changed invalid bytes')
    output = tmp_path / 'partial'
    audit = batch.run_batch(samples, paths, config, output)
    assert [r['status'] for r in audit['rows']] == ['success', 'missing', 'failed']
    assert audit['status'] == 'partial'
    summary = read(output / 'export/summary.json')
    assert summary['overall']['n'] == 3
    assert summary['overall']['run_status_counts'] == {'ok': 1, 'failed': 1, 'skipped': 0, 'missing': 1}
    assert summary['overall']['metrics']['runtime_failure_rate']['denominator'] == 3
    assert summary['real_world_accuracy']['status'] == '尚未测量'


def test_unreadable_matching_hash_preserves_decode_failure(tmp_path):
    samples, paths, config = inputs(tmp_path, 1)
    path = Path(paths['bindings']['asset-0']['primary_path'])
    path.write_bytes(b'not a png despite extension')
    samples['rows'][0]['sources'][0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    samples['rows'][0]['sources'][0]['original_sha256'] = samples['rows'][0]['sources'][0]['sha256']
    paths['manifest_sha256'] = batch.oe.canonical_hash(samples)
    audit = batch.run_batch(samples, paths, config, tmp_path / 'decode-failure')
    assert audit['rows'][0]['status'] == 'failed'
    assert any('Decode' in reason or 'image' in reason.lower() or '图片' in reason for reason in audit['rows'][0]['reasons'])
    predictions = read(tmp_path / 'decode-failure/export/predictions.json')
    assert predictions['rows'][0]['status'] == 'failed'
    assert predictions['rows'][0]['recommendation'] is None


def test_cancel_preserves_all_cases_without_complete_marker(tmp_path):
    docs = inputs(tmp_path, 3)
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        return calls == 2
    output = tmp_path / 'cancelled'
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(*docs, output, cancel_check=cancel)
    assert not (output / 'complete.json').exists()
    audit = read(output / 'batch-status.json')
    assert audit['status'] == 'cancelled'
    assert len(audit['rows']) == 3
    assert all(row['status'] != 'pending' for row in audit['rows'])
    assert not (output / 'frozen').exists()


def test_existing_output_never_overwritten(tmp_path):
    docs = inputs(tmp_path, 1)
    output = tmp_path / 'existing'
    output.mkdir()
    sentinel = output / 'keep.txt'
    sentinel.write_text('user data', encoding='utf-8')
    with pytest.raises(ValueError, match='already exists'):
        batch.run_batch(*docs, output)
    assert sentinel.read_text(encoding='utf-8') == 'user data'
    assert list(output.iterdir()) == [sentinel]


def test_write_failure_never_publishes_complete(tmp_path, monkeypatch):
    docs = inputs(tmp_path, 1)
    original = batch.atomic_json
    def fail(path, value):
        if Path(path).name == 'predictions.json':
            raise OSError('synthetic disk full')
        return original(path, value)
    monkeypatch.setattr(batch, 'atomic_json', fail)
    output = tmp_path / 'failed-write'
    with pytest.raises(OSError, match='disk full'):
        batch.run_batch(*docs, output)
    assert not (output / 'complete.json').exists()
    assert read(output / 'batch-status.json')['publication_complete'] is False


def test_config_document_mutation_during_measurement_invalidates_publication(tmp_path, monkeypatch):
    samples, paths, config = inputs(tmp_path, 1)
    original = batch.screen_assets
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        config['body_check'] = True
        return result
    monkeypatch.setattr(batch, 'screen_assets', change)
    output = tmp_path / 'changed-config'
    with pytest.raises(ValueError, match='documents changed'):
        batch.run_batch(samples, paths, config, output)
    assert not (output / 'complete.json').exists()


def test_technical_label_cannot_forge_subject_confirmation(tmp_path):
    samples, paths, config = inputs(tmp_path, 1)
    confirmations = dict(schema_version='real-sample-subject-confirmations-v1',
        manifest_sha256=batch.oe.canonical_hash(samples), entries={'asset-0': {'technical_label': 'clear'}})
    audit = batch.run_batch(samples, paths, config, tmp_path / 'no-forged-confirmation', confirmations=confirmations)
    assert audit['rows'][0]['status'] == 'needs_confirmation'
    assert 'quality annotations are not confirmations' in audit['rows'][0]['reasons'][0]


def test_receipt_switch_keeps_asset_judgments_manual_notes_groups_identical(tmp_path):
    samples, paths, config = inputs(tmp_path, 1)
    row = samples['rows'][0]
    first = batch.make_asset(row, paths['bindings'][row['asset_id']])
    first.preview_path = first.primary_path
    first.notes = ['manual note preserved']
    first.group_id = 42
    second = deepcopy(first)
    settings = batch.validate_config(config)
    screen_assets([first], analysis_mode='landscape', crop_settings=settings)
    screen_assets([second], analysis_mode='landscape', crop_settings=settings,
                  evaluation_recorder=mr.MeasurementRecorder(synthetic=True))
    assert asdict(first) == asdict(second)
    assert second.group_id == 42 and second.notes == ['manual note preserved']


def test_active_workspace_output_rejected(tmp_path):
    docs = inputs(tmp_path, 1)
    workspace = tmp_path / 'active'
    workspace.mkdir()
    (workspace / 'scan-session.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='workspace'):
        batch.run_batch(*docs, workspace / 'child')


def test_source_changes_after_measurement_never_get_valid_receipt(tmp_path, monkeypatch):
    samples, paths, config = inputs(tmp_path, 1)
    original = batch.screen_assets
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        with Path(paths['bindings']['asset-0']['primary_path']).open('ab') as stream:
            stream.write(b'changed after measurement')
        return result
    monkeypatch.setattr(batch, 'screen_assets', change)
    output = tmp_path / 'changed-source'
    with pytest.raises(batch.oe.ContractError, match='listed source changed'):
        batch.run_batch(samples, paths, config, output)
    assert not (output / 'complete.json').exists()
    assert read(output / 'export/predictions.json')['rows'][0]['status'] == 'failed'
    receipt = read(output / 'frozen/measurement-receipt.json')
    assert receipt['rows'][0]['capture']['status'] != 'complete'


def test_explicit_raw_jpg_members_bound_without_guessing_pair(tmp_path):
    samples, paths, config = inputs(tmp_path, 1)
    png = Path(paths['bindings']['asset-0']['primary_path'])
    jpg = png.with_suffix('.jpg')
    with Image.open(png) as image:
        image.save(jpg, quality=95)
    jpg_sha = hashlib.sha256(jpg.read_bytes()).hexdigest()
    samples['rows'][0]['sources'][0].update(sha256=jpg_sha, original_sha256=jpg_sha, format='JPG')
    paths['bindings']['asset-0']['primary_path'] = str(jpg)
    paths['bindings']['asset-0']['sources']['source-0'] = str(jpg)
    raw = tmp_path / '输入 图片' / '0' / 'unrelated-name.dng'
    raw.write_bytes(b'SYNTHETIC RAW MEMBER STUB; JPEG SELECTED; NEVER RAW DECODED')
    sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    source = deepcopy(samples['rows'][0]['sources'][0])
    source.update(source_id='explicit-raw', sha256=sha, original_sha256=sha, format='DNG', original_file_id='raw-stub')
    samples['rows'][0]['sources'].append(source)
    paths['bindings']['asset-0']['sources']['explicit-raw'] = str(raw)
    paths['bindings']['asset-0']['raw_source_id'] = 'explicit-raw'
    paths['manifest_sha256'] = batch.oe.canonical_hash(samples)
    output = tmp_path / 'explicit-pair'
    audit = batch.run_batch(samples, paths, config, output)
    assert audit['rows'][0]['status'] == 'success'
    receipt = read(output / 'frozen/measurement-receipt.json')['rows'][0]
    assert set(receipt['source_members']) == {'source-0', 'explicit-raw'}
    assert receipt['evaluated_source_id'] == 'source-0'
    assert receipt['source_member'] == paths['bindings']['asset-0']['sources']['source-0']
    assert read(output / 'export/summary.json')['overall']['n'] == 1


def test_freeze_lock_conflict_refuses_publication(tmp_path, monkeypatch):
    from ai_cull_assistant.publication_transaction import operation, PublicationRecoveryError
    docs = inputs(tmp_path, 1)
    original = mr.MeasurementRecorder.freeze
    def locked(self, result, *args, **kwargs):
        with operation(result.workspace_dir):
            return original(self, result, *args, **kwargs)
    monkeypatch.setattr(mr.MeasurementRecorder, 'freeze', locked)
    output = tmp_path / 'lock-conflict'
    with pytest.raises(PublicationRecoveryError):
        batch.run_batch(*docs, output)
    assert not (output / 'complete.json').exists()
    assert not (output / 'frozen').exists()


def test_cached_measurement_is_not_retroactively_signed(tmp_path, monkeypatch):
    # Explicit cache-observer dependency stub: exercise entry-point rejection,
    # not a claim that PNG landscape production currently uses a score cache.
    from ai_cull_assistant import analysis_modes
    original = analysis_modes.assess_asset_mode
    def cache_reported(asset, *args, **kwargs):
        result = original(asset, *args, **kwargs)
        mr.cache_hit(asset, 'SYNTHETIC historical cache dependency stub')
        return result
    monkeypatch.setattr(analysis_modes, 'assess_asset_mode', cache_reported)
    docs = inputs(tmp_path, 1)
    output = tmp_path / 'cache-gap'
    audit = batch.run_batch(*docs, output)
    assert audit['rows'][0]['status'] == 'provenance_insufficient'
    receipt = read(output / 'frozen/measurement-receipt.json')['rows'][0]['capture']
    assert receipt['status'] == 'cache_provenance_missing'
    assert read(output / 'export/predictions.json')['rows'][0]['status'] == 'failed'


def test_input_change_while_preparing_preview_invalidates_capture(tmp_path, monkeypatch):
    docs = inputs(tmp_path, 1)
    original = batch.build_preview
    def change(asset, *args, **kwargs):
        result = original(asset, *args, **kwargs)
        with asset.primary_path.open('ab') as handle:
            handle.write(b'synthetic concurrent source mutation')
        return result
    monkeypatch.setattr(batch, 'build_preview', change)
    output = tmp_path / 'preparation-change'
    with pytest.raises(batch.oe.ContractError, match='listed source changed'):
        batch.run_batch(*docs, output)
    assert not (output / 'complete.json').exists()
    assert read(output / 'export/predictions.json')['rows'][0]['status'] == 'failed'
    capture = read(output / 'frozen/measurement-receipt.json')['rows'][0]['capture']
    assert capture['status'] != 'complete'


def test_default_recorder_has_no_preview_preparation_hook():
    recorder = mr.MeasurementRecorder(synthetic=True)
    assert recorder.prepare_asset is None


def test_cli_nonzero_chinese_path_stderr_strict_utf8(tmp_path):
    import os
    import subprocess
    missing = tmp_path / '不存在 清单.json'
    command = [sys.executable, '-X', 'utf8', str(batch.ROOT / 'tools/real_sample_evaluation.py'),
               '--samples', str(missing), '--paths', str(missing), '--config', str(missing),
               '--output', str(tmp_path / '新结果')]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='strict',
                            timeout=30, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    assert result.returncode == 2
    assert '不存在 清单.json' in result.stderr
    assert 'No such file' in result.stderr or 'cannot find' in result.stderr
    assert not (tmp_path / '新结果').exists()


def test_source_mutation_after_export_prevents_batch_complete(tmp_path, monkeypatch):
    docs = inputs(tmp_path, 1)
    original = batch._write_export
    def write_and_mutate(*args, **kwargs):
        original(*args, **kwargs)
        with Path(docs[1]['bindings']['asset-0']['primary_path']).open('ab') as stream:
            stream.write(b'concurrent mutation after export')
    monkeypatch.setattr(batch, '_write_export', write_and_mutate)
    output = tmp_path / 'post-export-change'
    with pytest.raises(batch.oe.ContractError, match='listed source changed'):
        batch.run_batch(*docs, output)
    assert not (output / 'complete.json').exists()
    assert read(output / 'batch-status.json')['publication_complete'] is False


def test_tool_identity_change_after_export_prevents_complete(tmp_path, monkeypatch):
    # Explicit identity dependency stub, not an on-disk repository modification.
    docs = inputs(tmp_path, 1)
    original = batch._write_export
    changed = deepcopy(batch._LOADED_TOOLS)
    changed['real_sample_evaluation.py']['sha256'] = '0' * 64
    def write_then_change_identity(*args, **kwargs):
        original(*args, **kwargs)
        monkeypatch.setattr(batch, 'tool_identity', lambda: deepcopy(changed))
    monkeypatch.setattr(batch, '_write_export', write_then_change_identity)
    output = tmp_path / 'tool-change-during-run'
    with pytest.raises(batch.oe.ContractError, match='tool source changed during batch'):
        batch.run_batch(*docs, output)
    assert not (output / 'complete.json').exists()
    assert read(output / 'batch-status.json')['publication_complete'] is False


def test_tool_identity_change_since_import_rejected_before_output(tmp_path, monkeypatch):
    docs = inputs(tmp_path, 1)
    changed = deepcopy(batch._LOADED_TOOLS)
    changed['offline_evaluation.py']['sha256'] = '0' * 64
    monkeypatch.setattr(batch, 'tool_identity', lambda: deepcopy(changed))
    output = tmp_path / 'tool-change-before-run'
    with pytest.raises(batch.oe.ContractError, match='tool source changed since import'):
        batch.run_batch(*docs, output)
    assert not output.exists()


def test_formal_synthetic_fixture_is_native_evidence_sized(tmp_path):
    import real_sample_fixture
    directory = real_sample_fixture.generate(tmp_path / 'fixture')
    samples = read(directory / 'samples.json')
    paths = read(directory / 'paths.json')
    config = read(directory / 'config.json')
    audit = batch.run_batch(samples, paths, config, tmp_path / 'fixture-batch')
    assert audit['status'] == 'complete'
    assert len(audit['rows']) == 3
    assert all(row['status'] == 'success' for row in audit['rows'])


def test_small_native_image_keeps_strict_export_boundary_and_denominator(tmp_path):
    """640x480 is intentionally NOT enlarged to make the success fixture pass."""
    samples, paths, config = inputs(tmp_path, 1)
    path = Path(paths['bindings']['asset-0']['primary_path'])
    yy, xx = np.indices((480, 640))
    pixels = np.repeat((((xx // 5 + yy // 5) % 2) * 255).astype(np.uint8)[..., None], 3, axis=2)
    Image.fromarray(pixels).save(path)
    source = samples['rows'][0]['sources'][0]
    source.update(width=640, height=480)
    source['sha256'] = source['original_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    paths['manifest_sha256'] = batch.oe.canonical_hash(samples)
    before = path.read_bytes()
    output = tmp_path / 'small-boundary'
    audit = batch.run_batch(samples, paths, config, output)
    assert audit['status'] == 'partial'
    assert audit['measurement_seconds'] > 0
    assert audit['rows'][0]['status'] == 'provenance_insufficient'
    assert audit['rows'][0]['prediction_status'] == 'failed'
    assert 'insufficient decoded evidence carries automatic decision' in audit['rows'][0]['reasons']
    summary = read(output / 'export/summary.json')
    assert summary['overall']['n'] == 1
    assert summary['overall']['run_status_counts']['failed'] == 1
    assert summary['real_world_accuracy']['status'] == '尚未测量'
    assert read(output / 'export/predictions.json')['rows'][0]['recommendation'] is None
    assert path.read_bytes() == before
