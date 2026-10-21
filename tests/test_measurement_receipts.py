"""Synthetic images through production capture paths, no model/RAW accuracy claim."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from ai_cull_assistant import measurement_receipts as mr, analysis_modes
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.screening import screen_assets
from ai_cull_assistant.workflow import ScanResult

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import frozen_prediction_export as fe


def asset(tmp_path):
    path = tmp_path / '合成 照片.png'
    yy, xx = np.indices((640, 640))
    pixels = np.repeat((((xx // 5 + yy // 5) % 2) * 255).astype(np.uint8)[..., None], 3, axis=2)
    Image.fromarray(pixels).save(path)
    return PhotoAsset(path.stem, path, path, None, path, datetime(2026, 1, 1), '.png', group_id=7, preview_path=path)


def docs(a):
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip()
    samples = fe.oe.synthetic_documents(fe.oe.make_catalog(ROOT, commit))['samples']
    samples['rows'] = samples['rows'][:1]
    row = samples['rows'][0]
    row['sources'] = [{'source_id': 'source-png', 'format': 'PNG', 'sha256': hashlib.sha256(a.primary_path.read_bytes()).hexdigest()}]
    row['evaluation_source_id'] = 'source-png'
    row['scene_type'] = 'landscape'
    bindings = {row['asset_id']: {'primary_path': str(a.primary_path), 'sources': {'source-png': str(a.primary_path)}}}
    return samples, bindings


def result(a, tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir(exist_ok=True)
    return ScanResult([a], workspace / 'previews', workspace / 'contacts', workspace, workspace / 'groups.json', input_dir=tmp_path)


def verified_identity(monkeypatch):
    # Explicit identity dependency stub for dirty development test trees only.
    # The separately replayed demonstration uses real code_identity on clean HEAD.
    current = mr.code_identity()
    current.update(verified=True, dirty=False, test_stub='SYNTHETIC identity provider')
    monkeypatch.setattr(mr, 'code_identity', lambda: deepcopy(current))


def capture(tmp_path, monkeypatch=None):
    if monkeypatch:
        verified_identity(monkeypatch)
    a = asset(tmp_path)
    recorder = mr.MeasurementRecorder(synthetic=True)
    screen_assets([a], analysis_mode='landscape', evaluation_recorder=recorder)
    return a, recorder


def frozen(tmp_path, monkeypatch):
    a, recorder = capture(tmp_path, monkeypatch)
    samples, bindings = docs(a)
    path = recorder.freeze(result(a, tmp_path), samples, bindings, tmp_path / '冻结 输出')
    return a, recorder, path


def test_real_capture_export_evaluation(tmp_path, monkeypatch):
    a, recorder, path = frozen(tmp_path, monkeypatch)
    read = lambda n: json.loads((path / n).read_text('utf-8'))
    receipt = read('measurement-receipt.json')
    stage = receipt['rows'][0]['capture']['stages'][0]
    assert stage['name'] == 'landscape'
    assert stage['source_member'] == str(a.primary_path)
    assert stage['input_sha256'] == hashlib.sha256(a.primary_path.read_bytes()).hexdigest()
    exported = fe.load_completed_set(path)
    assert exported['predictions']['rows'][0]['status'] == 'ok'
    assert exported['predictions']['rows'][0]['recommendation'] == a.local_recommendation
    assert exported['summary']['real_world_accuracy']['status'] == '尚未测量'
    assert exported['annotations']['rows'] == []
    assert recorder.cost['extra_decodes'] == 0
    assert recorder.cost['hash_read_count'] == 3


def test_off_switch_has_no_hash_or_identity_work(tmp_path, monkeypatch):
    a = asset(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('recording ran while disabled')
    monkeypatch.setattr(mr, 'code_identity', forbidden)
    monkeypatch.setattr(mr.MeasurementRecorder, '_hashes', forbidden)
    screen_assets([a], analysis_mode='landscape')
    assert a.group_id == 7


def test_switch_equivalence(tmp_path):
    a = asset(tmp_path)
    b = deepcopy(a)
    baseline = screen_assets([a], analysis_mode='landscape')
    recorder = mr.MeasurementRecorder(synthetic=True)
    observed = screen_assets([b], analysis_mode='landscape', evaluation_recorder=recorder)
    assert asdict(a) == asdict(b)
    assert baseline == observed


@pytest.mark.parametrize('change', ['pixels', 'settings', 'confirmation'])
def test_changes_during_measurement_not_valid(tmp_path, monkeypatch, change):
    a = asset(tmp_path)
    settings = CropSettings(photos={CropSettings().key(a): {}})
    original = analysis_modes.landscape_evidence
    def change_after(image):
        value = original(image)
        if change == 'pixels':
            a.primary_path.write_bytes(a.primary_path.read_bytes() + b'changed')
        elif change == 'settings':
            settings.photos[settings.key(a)]['scale_factor'] = 1.3
        else:
            settings.photos[settings.key(a)]['primary_subject_confirmation'] = {'changed': True}
        return value
    monkeypatch.setattr(analysis_modes, 'landscape_evidence', change_after)
    recorder = mr.MeasurementRecorder(synthetic=True)
    screen_assets([a], analysis_mode='landscape', crop_settings=settings, evaluation_recorder=recorder)
    assert recorder.rows[str(a.primary_path)]['status'] == 'changed'


def test_cancel_records_not_completed(tmp_path, monkeypatch):
    a = asset(tmp_path)
    def cancel(image):
        raise KeyboardInterrupt()
    monkeypatch.setattr(analysis_modes, 'landscape_evidence', cancel)
    recorder = mr.MeasurementRecorder(synthetic=True)
    with pytest.raises(KeyboardInterrupt):
        screen_assets([a], analysis_mode='landscape', evaluation_recorder=recorder)
    assert recorder.rows[str(a.primary_path)]['status'] == 'cancelled'
    assert recorder.rows[str(a.primary_path)]['stages'][0]['status'] == 'failed'


def test_unreadable_fails_recording_not_photo_decisions(tmp_path):
    a = asset(tmp_path)
    a.primary_path.unlink()
    recorder = mr.MeasurementRecorder(synthetic=True)
    screen_assets([a], analysis_mode='landscape', evaluation_recorder=recorder)
    assert a.local_recommendation == 'review'
    assert recorder.rows[str(a.primary_path)]['status'] == 'unverifiable'


def test_quality_cache_is_not_a_new_measurement(tmp_path, monkeypatch):
    a = asset(tmp_path)
    original = analysis_modes._assess_asset_mode
    def cached(asset, **kwargs):
        mr.cache_hit(asset, 'face')
        return original(asset, **kwargs)
    monkeypatch.setattr(analysis_modes, '_assess_asset_mode', cached)
    recorder = mr.MeasurementRecorder(synthetic=True)
    screen_assets([a], analysis_mode='landscape', evaluation_recorder=recorder)
    c = recorder.rows[str(a.primary_path)]
    assert c['status'] == 'cache_provenance_missing'
    assert c['kind'] == 'partial_recomputation'


def test_old_session_never_gets_fresh_receipt(tmp_path):
    a = asset(tmp_path)
    screen_assets([a], analysis_mode='landscape')
    samples, bindings = docs(a)
    recorder = mr.MeasurementRecorder(synthetic=True)
    path = recorder.freeze(result(a, tmp_path), samples, bindings, tmp_path / 'old')
    c = json.loads((path / 'measurement-receipt.json').read_text())['rows'][0]['capture']
    assert c['status'] != 'complete' and c['kind'] == 'cache_reuse'
    assert c['stages'] == []


@pytest.mark.parametrize('change', ['source', 'evidence', 'scope', 'settings'])
def test_changed_before_freeze(tmp_path, change):
    a, recorder = capture(tmp_path)
    samples, bindings = docs(a)
    if change == 'source':
        a.primary_path.write_bytes(a.primary_path.read_bytes() + b'change')
    elif change == 'evidence':
        a.clarity_evidence['recommendation'] = 'changed'
    elif change == 'scope':
        a.focus_scope = {'changed': True}
    else:
        state = recorder._assets[str(a.primary_path)]
        state[1].photos[state[1].key(a)] = {'scale_factor': 1.6}
    path = recorder.freeze(result(a, tmp_path), samples, bindings, tmp_path / 'changed')
    c = json.loads((path / 'measurement-receipt.json').read_text())['rows'][0]['capture']
    assert c['status'] != 'complete'


def test_write_failure_keeps_live_decisions(tmp_path, monkeypatch):
    from ai_cull_assistant import ai_project
    a, recorder = capture(tmp_path)
    scan = result(a, tmp_path)
    live = scan.workspace_dir / 'scan-session.json'
    live.write_bytes(b'USER DECISIONS untouched')
    before = asdict(a)
    samples, bindings = docs(a)
    original = ai_project.atomic_json
    def failed(path, value):
        if Path(path).name == 'measurement-receipt.json':
            raise OSError('simulated disk full')
        return original(path, value)
    monkeypatch.setattr(ai_project, 'atomic_json', failed)
    with pytest.raises(OSError, match='disk full'):
        recorder.freeze(scan, samples, bindings, tmp_path / 'failed')
    assert live.read_bytes() == b'USER DECISIONS untouched'
    assert asdict(a) == before
    assert not (tmp_path / 'failed').exists()
    assert not list(tmp_path.glob('*.staging-*'))


def test_existing_output_not_overwritten(tmp_path, monkeypatch):
    a, recorder, output = frozen(tmp_path, monkeypatch)
    prior = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(ValueError):
        recorder.freeze(result(a, tmp_path), *docs(a), output)
    assert prior == {p.name: p.read_bytes() for p in output.iterdir()}


def test_private_settings_not_captured(tmp_path):
    a = asset(tmp_path)
    settings = CropSettings(photos={CropSettings().key(a): {'api_key': 'DO_NOT_CAPTURE', 'prompt': 'SECRET'}})
    recorder = mr.MeasurementRecorder(synthetic=True)
    screen_assets([a], analysis_mode='landscape', crop_settings=settings, evaluation_recorder=recorder)
    assert 'DO_NOT_CAPTURE' not in mr.canonical(recorder.rows)
    assert 'SECRET' not in mr.canonical(recorder.rows)


def test_actual_checkout_identity_reports_dirty_truthfully():
    identity = mr.code_identity()
    actual = subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain=v1', '--untracked-files=all'])
    assert identity['dirty'] == bool(actual)
    assert identity['verified'] == (not bool(actual))
    assert identity['module_hashes']['measurement_receipts.py'] == hashlib.sha256(Path(mr.__file__).read_bytes()).hexdigest()


def test_dependency_snapshot_is_not_lock():
    value = mr.dependencies()
    assert value['production_dependency_lock'] is False
    assert value['installed']['Pillow']
    assert value['loaded']['numpy']['version'] == np.__version__


def test_source_change_then_restore_is_not_signed(tmp_path, monkeypatch):
    a = asset(tmp_path)
    original = analysis_modes.landscape_evidence
    def aba(image):
        data = a.primary_path.read_bytes()
        a.primary_path.write_bytes(data + b'changed while scoring')
        result = original(image)
        a.primary_path.write_bytes(data)
        return result
    monkeypatch.setattr(analysis_modes, 'landscape_evidence', aba)
    recorder = mr.MeasurementRecorder(synthetic=True)
    screen_assets([a], analysis_mode='landscape', evaluation_recorder=recorder)
    assert recorder.rows[str(a.primary_path)]['status'] != 'complete'


def test_distinct_preview_is_auxiliary_not_exposure(tmp_path, monkeypatch):
    verified_identity(monkeypatch)
    a = asset(tmp_path)
    a.preview_path = tmp_path / 'preview.png'
    Image.open(a.primary_path).resize((256, 256)).save(a.preview_path)
    recorder = mr.MeasurementRecorder(synthetic=True)
    screen_assets([a], analysis_mode='landscape', evaluation_recorder=recorder)
    state = recorder.rows[str(a.primary_path)]['state_before']
    assert set(state['source_hashes']) == {str(a.primary_path)}
    assert set(state['auxiliary_hashes']) == {str(a.preview_path)}
    output = recorder.freeze(result(a, tmp_path), *docs(a), tmp_path / 'aux')
    assert fe.load_completed_set(output)['predictions']['rows'][0]['status'] == 'ok'


def test_live_session_reopens_and_old_format_stays_compatible(tmp_path, monkeypatch):
    from ai_cull_assistant.session_store import save_session, load_session
    a, recorder = capture(tmp_path, monkeypatch)
    a.notes = ["人工决定保留"]
    scan = result(a, tmp_path)
    save_session(scan)
    before = (scan.workspace_dir / 'scan-session.json').read_bytes()
    recorder.freeze(scan, *docs(a), tmp_path / 'reopened')
    reopened = load_session(scan.workspace_dir, scan.input_dir)
    assert reopened is not None
    assert asdict(reopened.assets[0]) == asdict(a)
    assert (scan.workspace_dir / 'scan-session.json').read_bytes() == before
    assert json.loads(before)['version'] == 1


def test_loaded_monkeypatch_cannot_be_verified_code(monkeypatch):
    monkeypatch.setattr(analysis_modes, 'landscape_evidence', lambda image: {})
    assert mr.loaded_code_matches(ROOT) is False
