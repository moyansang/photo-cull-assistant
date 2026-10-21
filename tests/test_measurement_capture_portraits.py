"""Production portrait capture with declared synthetic detector/RAW dependency stubs.

No real RAW decoding, model inference, or person-accuracy acceptance is claimed.
Receipts are only produced by screen_assets + MeasurementRecorder.freeze.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from ai_cull_assistant import (body_focus, face_focus, measurement_receipts as mr,
                               primary_quality as quality, shared_decode, subject)
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.screening import screen_assets
from test_measurement_receipts import docs, result, verified_identity, fe
from test_primary_quality import FACE, BODY, face


def setup_portrait(tmp_path, monkeypatch, *, body=False, paired=False):
    jpg = tmp_path / 'synthetic-person.jpg'
    yy, xx = np.indices((500, 400))
    data = np.repeat((((xx // 5 + yy // 5) % 2) * 255).astype(np.uint8)[..., None], 3, axis=2)
    Image.fromarray(data).save(jpg)
    raw = tmp_path / 'synthetic-person.CR3' if paired else None
    if raw:
        raw.write_bytes(b'EXPLICIT RAW DECODER STUB: not a real camera file')
    primary = raw or jpg
    a = PhotoAsset(primary.stem, primary, primary, raw, jpg, datetime(2026, 1, 1),
                   primary.suffix, group_id=17, preview_path=jpg)
    settings = CropSettings()
    entry = {'selected_faces': [FACE]}
    if body:
        entry['selected_bodies'] = [BODY]
    settings.photos[settings.key(a)] = entry
    entry[quality.CONFIRMATION_KEY] = quality.confirm_selection(a, settings)
    detector = SimpleNamespace(face=FACE, landmarks=((.18,.2),(.36,.2),(.27,.28),(.2,.37),(.34,.37)), head_source='manual')
    monkeypatch.setattr(face_focus, 'detail_features_list', lambda *a, **k: [detector])
    monkeypatch.setattr(face_focus, 'detail_features', lambda *a, **k: detector)
    monkeypatch.setattr(subject, 'body_candidates', lambda *a, **k: [dict(box=BODY, source='manual', evidence_kind='body')] if body else [])
    # Stub only external metric/model dependencies. Real participant crop,
    # policy, scope, cache, observer and freeze/export paths execute unchanged.
    calls = {'face': 0, 'body': 0, 'decode': []}
    def metrics(*args, **kwargs):
        calls['face'] += 1
        return face(body=False)
    monkeypatch.setattr(face_focus, 'detail_metrics', metrics)
    def body_metric(image, box):
        calls['body'] += 1
        return dict(box=list(box), source='manual', evidence_kind='body',
                    state='uncertain', confidence=None, regions=[], eye_evidence=False,
                    reasons=['manual_body_without_reliable_pose'])
    monkeypatch.setattr(body_focus, 'assess_body_region', body_metric)
    monkeypatch.setattr('ai_cull_assistant.focus_image_cache.warm_focus_images', lambda *a, **k: None)
    @contextmanager
    def decoder(asset, **options):
        is_body = options.get('min_region_pixels') == 96
        member = raw if paired and (is_body or options.get('insufficient_evidence')) else jpg
        kind = 'raw_full_decode' if member == raw else 'paired_jpeg' if paired else 'standard_image'
        calls['decode'].append((str(member), dict(options)))
        with Image.open(jpg) as original:
            image = original.convert('RGB')
        image.info['decode_evidence'] = dict(version='source-first-v1', source_path=str(member),
            source_kind=kind, source_size=list(image.size), full_resolution=True,
            color_space='sRGB', warnings=[], evidence_sufficient=True,
            test_stub='synthetic decode provider; RAW bytes are not decoded')
        configuration = dict(kind='actual_decoder_call',
            test_stub='synthetic image provider; no real RAW decode',
            source_kind='standard_image' if kind == 'paired_jpeg' else kind,
            backend='synthetic', backend_version='fixture-v1', warnings=[])
        if kind == 'raw_full_decode':
            configuration.update(libraw_version=[], injected_backend=True,
                postprocess=dict(use_camera_wb=True, half_size=False,
                                 no_auto_bright=True, output_bps=8),
                unpack=True, orientation='LibRaw-postprocess-only')
        else:
            configuration.update(orientation='exif_transpose',
                normalization='ICC-to-sRGB-or-warning;white-alpha;RGB8')
        mr.observe_decode(member, configuration)
        try:
            yield image
        finally:
            image.close()
    monkeypatch.setattr(face_focus, 'full_image', decoder)
    monkeypatch.setattr(shared_decode, 'full_image', decoder)
    verified_identity(monkeypatch)
    return a, settings, calls


def capture(a, settings, cache=None):
    recorder = mr.MeasurementRecorder(synthetic=True)
    values = screen_assets([a], analysis_mode='portrait', crop_settings=settings,
                           cache_dir=cache, evaluation_recorder=recorder)
    return recorder, values


def freeze_and_export(a, recorder, tmp_path, name='frozen', selected='jpg'):
    samples, bindings = docs(a)
    row = samples['rows'][0]
    row['scene_type'] = 'portrait'
    paths = [('jpg', 'JPG', a.jpg_path)]
    if a.raw_path:
        paths.append(('raw', 'RAW', a.raw_path))
    row['sources'] = [dict(source_id=sid, format=fmt, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                      for sid, fmt, path in paths]
    row['evaluation_source_id'] = selected
    bindings[row['asset_id']]['sources'] = {sid: str(path) for sid, _, path in paths}
    output = recorder.freeze(result(a, tmp_path), samples, bindings, tmp_path / name)
    read = lambda n: json.loads((output / n).read_text('utf-8'))
    receipt = read('measurement-receipt.json')
    exported = fe.load_completed_set(output)
    return receipt, exported


@pytest.mark.parametrize('body', [False, True])
def test_confirmed_manual_range_capture_and_export(tmp_path, monkeypatch, body):
    a, settings, calls = setup_portrait(tmp_path, monkeypatch, body=body)
    confirmation = deepcopy(quality.confirmed_selection(a, settings))
    recorder, _ = capture(a, settings)
    receipt, exported = freeze_and_export(a, recorder, tmp_path)
    c = receipt['rows'][0]['capture']
    assert c['status'] == 'complete'
    assert c['confirmation_snapshot'] == confirmation
    assert quality.confirmed_selection(a, settings) == confirmation
    assert calls['face'] == 1 and calls['body'] == int(body)
    assert {s['name'] for s in c['stages']} == ({'face', 'body'} if body else {'face'})
    row = exported['predictions']['rows'][0]
    assert row['status'] == 'ok', row
    assert row['recommendation'] == a.local_recommendation


def test_face_jpg_and_body_raw_are_independently_bound(tmp_path, monkeypatch):
    a, settings, calls = setup_portrait(tmp_path, monkeypatch, body=True, paired=True)
    recorder, _ = capture(a, settings)
    receipt, exported = freeze_and_export(a, recorder, tmp_path)
    stages = {s['name']: s for s in receipt['rows'][0]['capture']['stages']}
    assert stages['face']['source_member'] == str(a.jpg_path)
    assert stages['body']['source_member'] == str(a.raw_path)
    assert stages['face']['input_sha256'] == hashlib.sha256(a.jpg_path.read_bytes()).hexdigest()
    assert stages['body']['input_sha256'] == hashlib.sha256(a.raw_path.read_bytes()).hexdigest()
    assert stages['face']['input_sha256'] != stages['body']['input_sha256']
    assert calls['face'] == calls['body'] == 1
    assert recorder.cost['extra_decodes'] == 0
    row = exported['predictions']['rows'][0]
    assert row['status'] == 'ok', row


@pytest.mark.parametrize('body', [False, True])
def test_real_quality_cache_hit_never_reissued_as_new(tmp_path, monkeypatch, body):
    a, settings, calls = setup_portrait(tmp_path, monkeypatch, body=body)
    cache = tmp_path / 'quality-cache'
    first, _ = capture(a, settings, cache)
    assert calls['face'] == 1
    assert list(cache.rglob('*.json'))
    second, _ = capture(a, settings, cache)
    assert calls['face'] == 1  # Real assess_asset_focus cache returned before scorer.
    assert calls['body'] == 2 * int(body)
    c = second.rows[str(a.primary_path)]
    assert c['status'] == 'cache_provenance_missing'
    assert c['kind'] == ('partial_recomputation' if body else 'cache_reuse')
    assert c['cache_reuse'][0]['name'] == 'face'
    assert {s['name'] for s in c['stages']} == ({'body'} if body else set())
    receipt, exported = freeze_and_export(a, second, tmp_path)
    assert receipt['recording_status'] == 'partial'
    assert exported['predictions']['rows'][0]['status'] != 'ok'


def test_portrait_switch_preserves_decisions_groups_and_recommendations(tmp_path, monkeypatch):
    a, settings, calls = setup_portrait(tmp_path, monkeypatch, body=True, paired=True)
    b = deepcopy(a)
    before = deepcopy(settings.photos)
    plain = screen_assets([a], analysis_mode='portrait', crop_settings=settings)
    recorder, observed = capture(b, settings)
    assert plain == observed
    assert asdict(a) == asdict(b)
    assert settings.photos == before
    assert a.group_id == 17
    assert recorder.rows[str(b.primary_path)]['status'] == 'complete'


def test_transient_roi_change_cannot_export_original_confirmation(tmp_path, monkeypatch):
    """A detector observes changed ROI; scorer restores it before final checks.

    Before/after settings alone cannot establish the region actually measured.
    The real capture stage and its evidence must still match the confirmation.
    """
    a, settings, calls = setup_portrait(tmp_path, monkeypatch)
    original = deepcopy(settings.photos[settings.key(a)])
    other = [.55, .1, .35, .4]
    def transient_roi(*args, **kwargs):
        settings.photos[settings.key(a)]['selected_faces'] = [other]
        return [SimpleNamespace(face=other, landmarks=((.6,.2),(.8,.2),(.7,.28),(.63,.37),(.77,.37)), head_source='manual')]
    def restore_during_score(*args, **kwargs):
        settings.photos[settings.key(a)] = deepcopy(original)
        return face(body=False, box=other)
    monkeypatch.setattr(face_focus, 'detail_features_list', transient_roi)
    monkeypatch.setattr(face_focus, 'detail_metrics', restore_during_score)
    recorder, _ = capture(a, settings)
    assert settings.photos[settings.key(a)] == original
    assert recorder.rows[str(a.primary_path)]['status'] != 'complete'
    receipt, exported = freeze_and_export(a, recorder, tmp_path)
    row = exported['predictions']['rows'][0]
    assert row['status'] != 'ok', row


def test_confirmed_body_only_has_no_fabricated_face_source(tmp_path, monkeypatch):
    a, settings, calls = setup_portrait(tmp_path, monkeypatch, body=True)
    entry = settings.photos[settings.key(a)]
    del entry['selected_faces']
    entry[quality.CONFIRMATION_KEY] = quality.confirm_selection(a, settings)
    confirmation = deepcopy(entry[quality.CONFIRMATION_KEY])
    recorder, _ = capture(a, settings)
    receipt, exported = freeze_and_export(a, recorder, tmp_path)
    c = receipt['rows'][0]['capture']
    assert c['status'] == 'complete'
    assert c['confirmation_snapshot'] == confirmation
    assert calls['face'] == 0 and calls['body'] == 1
    assert {s['name'] for s in c['stages']} == {'body'}
    assert c['stages'][0]['source_member'] == str(a.jpg_path)
    row = exported['predictions']['rows'][0]
    assert row['status'] == 'ok', row
    assert row['recommendation'] == a.local_recommendation == 'review'


def test_actual_face_retry_records_jpg_then_raw_upgrade(tmp_path, monkeypatch):
    a, settings, calls = setup_portrait(tmp_path, monkeypatch, paired=True)
    def insufficient_then_clear(*args, **kwargs):
        calls['face'] += 1
        value = face(body=False)
        if calls['face'] == 1:
            value.update(state='uncertain', reasons=['insufficient_native_eye_pixels'])
        return value
    monkeypatch.setattr(face_focus, 'detail_metrics', insufficient_then_clear)
    recorder, _ = capture(a, settings)
    receipt, exported = freeze_and_export(a, recorder, tmp_path, selected='raw')
    stages = receipt['rows'][0]['capture']['stages']
    assert calls['face'] == 2
    assert [s['name'] for s in stages] == ['face', 'face']
    assert [s['operation'] for s in stages] == ['face', 'face_raw_upgrade']
    assert [s['source_member'] for s in stages] == [str(a.jpg_path), str(a.raw_path)]
    assert stages[-1]['options']['insufficient_evidence'] is True
    assert receipt['rows'][0]['source_member'] == str(a.raw_path)
    row = exported['predictions']['rows'][0]
    assert row['status'] == 'ok', row
    assert row['recommendation'] == a.local_recommendation
