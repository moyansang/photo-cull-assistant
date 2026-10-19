"""Automatic scope is source-bound evidence, never human confirmation."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json

import pytest
from PIL import Image

from ai_cull_assistant import ai_focus, focus_protocol as protocol, primary_quality as quality
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset


@pytest.fixture
def automatic(tmp_path):
    path = tmp_path / 'automatic.png'
    Image.new('RGB', (600, 600), 'gray').save(path)
    asset = PhotoAsset(path.stem, path, path, None, path, datetime.now(), '.png', preview_path=path)
    settings = CropSettings()
    box = [.1, .1, .4, .4]
    evidence = dict(subjects=[dict(evidence_kind='face', normalized_box=box)],
        automatic_subject=dict(version='single-detected-face-v1', candidate_count=1,
            detection_threshold=settings.detection_confidence, landmarks_available=True, box=box),
        measurement_inputs=quality.measurement_inputs(asset, settings, ['face']))
    selection = quality.automatic_selection(asset, settings, evidence, evidence['measurement_inputs'])
    assert selection is not None
    evidence.update(automatic_selection=selection, measurement_selection_digest=selection['digest'])
    asset.clarity_evidence = evidence
    return asset, settings


def test_automatic_scope_never_forges_confirmation(automatic):
    asset, settings = automatic
    scope = protocol.make_scope(asset, settings)
    assert scope['confirmation_digest'] is None
    assert scope['selection_origin'] == 'automatic'
    assert quality.confirmed_selection(asset, settings) is None
    assert not settings.photos
    context = protocol.make_context(scope, scope['subjects'][0]['id'], evidence_parts=['face'])
    assert context['required_parts'] == ['face']
    value = protocol.response_template(context)
    value['parts'][0].update(visibility='visible', severity='clear', evidence_sufficient=True)
    result = protocol.assemble_result(scope, [protocol.parse_response(json.dumps(value), context)])
    assert result['recommendation'] == 'keep'
    assert protocol.current_result(result, asset, settings) == result
    asset.clarity_evidence['measurement_selection_digest'] = 'stale'
    assert protocol.current_result(result, asset, settings) is None


@pytest.mark.parametrize('change', ['confirmation', 'origin', 'partial', 'parts', 'scope'])
def test_invalid_automatic_scope_rejected(automatic, change):
    scope = protocol.make_scope(*automatic)
    if change == 'confirmation': scope['confirmation_digest'] = scope['selection_digest']
    if change == 'origin': scope['selection_origin'] = 'human'
    if change == 'partial': scope.pop('selection_digest')
    if change == 'parts': scope['enabled_parts'] = ['body']
    if change == 'scope': scope['mode'] = 'landscape'
    scope['digest'] = protocol._digest({k:v for k,v in scope.items() if k != 'digest'})
    with pytest.raises(protocol.FocusProtocolError):
        protocol._validate_scope(scope)


def test_body_cannot_leak_into_face_only_context(automatic):
    scope = protocol.make_scope(*automatic)
    with pytest.raises(protocol.FocusProtocolError):
        protocol.make_context(scope, scope['subjects'][0]['id'], evidence_parts=['face','body'])


def test_automatic_review_uses_face_only_native_inputs_and_cache(automatic, tmp_path, monkeypatch):
    asset, settings = automatic
    calls = []
    monkeypatch.setattr(ai_focus, 'detail_features', lambda *a: None)
    def model(_profile, prompt, images):
        calls.append([p.name for p in images])
        value = json.loads(prompt.split('返回格式：')[1])
        assert value['confirmation_digest'] is None
        assert [p['part'] for p in value['parts']] == ['face']
        value['parts'][0].update(visibility='visible', severity='clear', evidence_sufficient=True)
        return {'text': json.dumps(value)}
    monkeypatch.setattr(ai_focus, 'call_model', model)
    result = ai_focus.review_focus(asset, settings, {}, tmp_path / 'cache')
    assert result['status'] == 'clear'
    assert ai_focus.review_focus(asset, settings, {}, tmp_path / 'cache') == result
    assert len(calls) == 1
    assert not any(name.startswith('body_') for name in calls[0])
    assert quality.confirmed_selection(asset, settings) is None


def test_source_settings_and_scope_invalidate_automatic_result(automatic):
    asset, settings = automatic
    scope = protocol.make_scope(asset, settings)
    asset.clarity_evidence['automatic_subject']['candidate_count'] = 2
    with pytest.raises(protocol.FocusProtocolError):
        protocol.make_scope(asset, settings)
    asset.clarity_evidence['automatic_subject']['candidate_count'] = 1
    settings = replace(settings, detection_confidence=settings.detection_confidence + .01)
    with pytest.raises(protocol.FocusProtocolError):
        protocol.make_scope(asset, settings)


def test_automatic_policy_requires_origin_digest_and_measurement(automatic):
    asset, settings = automatic
    scope = protocol.make_scope(asset, settings)
    policy = dict(version=quality.POLICY_VERSION, confirmation_digest=None,
                  **{k:scope[k] for k in ('selection_origin','selection_digest','enabled_parts')})
    assert protocol.policy_matches_scope(policy, asset.clarity_evidence, scope)
    for field in ('selection_origin', 'selection_digest', 'enabled_parts'):
        changed = deepcopy(policy)
        changed.pop(field)
        assert not protocol.policy_matches_scope(changed, asset.clarity_evidence, scope)


def test_manual_scope_preserves_confirmation_and_versions_face_resolution(automatic):
    asset, settings = automatic
    box = asset.clarity_evidence['automatic_subject']['box']
    settings.photos[settings.key(asset)] = {'selected_faces': [box]}
    confirmation = quality.confirm_selection(asset, settings)
    settings.photos[settings.key(asset)][quality.CONFIRMATION_KEY] = confirmation
    asset.clarity_evidence = {}
    scope = protocol.make_scope(asset, settings)
    assert set(scope) == protocol._SCOPE_KEYS | protocol._MANUAL_FACE_SCOPE_KEYS
    assert scope['confirmation_digest'] == confirmation['digest']
    ctx = protocol.make_context(scope, scope['subjects'][0]['id'], evidence_parts=['face','body'])
    value = protocol.response_template(ctx)
    for part in value['parts']:
        part.update(visibility='visible', severity='clear', evidence_sufficient=True)
    result = protocol.assemble_result(scope, [protocol.parse_response(json.dumps(value), ctx)])
    assert result['reason'] == '已确认主体的可见必需部位均有充分清楚证据。'
    assert protocol.validate_result(result) == result
    asset.clarity_evidence['measurement_inputs'] = quality.measurement_inputs(asset, settings, ['face'])
    limited = protocol.make_scope(asset, settings)
    assert limited['selection_origin'] == 'human'
    assert limited['selection_digest'] == confirmation['digest']
    assert limited['confirmation_digest'] == confirmation['digest']
    assert protocol.make_context(limited, limited['subjects'][0]['id'])['required_parts'] == ['face']
    assert protocol.current_result(result, asset, settings) is None


def test_automatic_source_change_invalidates_scope(automatic):
    asset, settings = automatic
    protocol.make_scope(asset, settings)
    Image.new('RGB', (700, 700), 'white').save(asset.primary_path)
    with pytest.raises(protocol.FocusProtocolError):
        protocol.make_scope(asset, settings)


def test_automatic_review_rejects_selection_change_during_model_call(automatic, tmp_path, monkeypatch):
    asset, settings = automatic
    monkeypatch.setattr(ai_focus, 'detail_features', lambda *a: None)
    def model(_profile, prompt, images):
        value = json.loads(prompt.split('返回格式：')[1])
        value['parts'][0].update(visibility='visible', severity='clear', evidence_sufficient=True)
        asset.clarity_evidence['measurement_selection_digest'] = 'stale'
        return {'text': json.dumps(value)}
    monkeypatch.setattr(ai_focus, 'call_model', model)
    with pytest.raises(protocol.FocusProtocolError):
        ai_focus.review_focus(asset, settings, {}, tmp_path / 'cache')
    assert not list((tmp_path / 'cache' / protocol.PROMPT_VERSION).glob('*.json'))


def test_fresh_manual_measurement_merges_body_and_supplies_native_proof(automatic, tmp_path, monkeypatch):
    from ai_cull_assistant import analysis_modes, face_focus, subject, body_focus, body_pipeline
    from ai_cull_assistant.screening import ScreeningResult
    from test_primary_quality import face as face_measurement, body as body_measurement, FACE, BODY
    asset, settings = automatic
    settings.photos[settings.key(asset)] = {'selected_faces': [FACE], 'selected_bodies': [BODY]}
    confirmation = quality.confirm_selection(asset, settings)
    settings.photos[settings.key(asset)][quality.CONFIRMATION_KEY] = confirmation
    monkeypatch.setattr(face_focus, 'assess_asset_focus', lambda *a, **kw:
        ScreeningResult(False, 'subject_not_obviously_blurred', True,
                        focus_evidence=face_measurement(body=False), source_size=(600,600)))
    monkeypatch.setattr(subject, 'body_candidates', lambda *a: [dict(box=BODY)])
    monkeypatch.setattr(body_focus, 'assess_body_region', lambda *a: dict(body_measurement([]), head_box=FACE))
    monkeypatch.setattr(body_pipeline, 'apply_body_check', lambda asset, face, *a: face)
    measured = analysis_modes.assess_asset_mode(asset, crop_settings=settings, body_check=True)
    asset.clarity_evidence = measured.focus_evidence
    assert 'measurement_selection_digest' not in measured.focus_evidence
    assert measured.focus_evidence['measurement_confirmation_digest'] == confirmation['digest']
    scope = protocol.make_scope(asset, settings)
    assert scope['selection_origin'] == 'human'
    assert len(scope['subjects']) == 1
    assert protocol.policy_matches_scope(measured.focus_evidence['primary_quality'], measured.focus_evidence, scope)
    monkeypatch.setattr(ai_focus, 'detail_features', lambda *a: None)
    calls = []
    def model(_profile, prompt, images):
        calls.append([p.name for p in images])
        value = json.loads(prompt.split('返回格式：')[1])
        assert [part['part'] for part in value['parts']] == ['face', 'body']
        for part in value['parts']:
            part.update(visibility='visible', severity='clear', evidence_sufficient=True)
        return {'text': json.dumps(value)}
    monkeypatch.setattr(ai_focus, 'call_model', model)
    result = ai_focus.review_focus(asset, settings, {}, tmp_path / 'cache')
    assert result['recommendation'] == 'keep'
    assert len(calls) == 1
    assert any(name.startswith('body_native_associated_') for name in calls[0])


def test_fresh_manual_body_disabled_requests_face_only(automatic, tmp_path, monkeypatch):
    from ai_cull_assistant import analysis_modes, face_focus
    from ai_cull_assistant.screening import ScreeningResult
    from test_primary_quality import face as face_measurement, FACE
    asset, settings = automatic
    settings.photos[settings.key(asset)] = {'selected_faces': [FACE]}
    settings.photos[settings.key(asset)][quality.CONFIRMATION_KEY] = quality.confirm_selection(asset, settings)
    monkeypatch.setattr(face_focus, 'assess_asset_focus', lambda *a, **kw:
        ScreeningResult(False, 'subject_not_obviously_blurred', True,
                        focus_evidence=face_measurement(body=False), source_size=(600,600)))
    measured = analysis_modes.assess_asset_mode(asset, crop_settings=settings, body_check=False)
    asset.clarity_evidence = measured.focus_evidence
    scope = protocol.make_scope(asset, settings)
    assert scope['enabled_parts'] == ['face']
    assert protocol.policy_matches_scope(measured.focus_evidence['primary_quality'], measured.focus_evidence, scope)
    monkeypatch.setattr(ai_focus, 'detail_features', lambda *a: None)
    def model(_profile, prompt, images):
        ranges = json.loads(prompt.split('请求范围：')[1].split('\n返回格式：')[0])
        assert ranges == dict(required_parts=['face'], evidence_parts=['face'], not_visible_parts=[])
        value = json.loads(prompt.split('返回格式：')[1])
        value['parts'][0].update(visibility='visible', severity='clear', evidence_sufficient=True)
        return {'text': json.dumps(value)}
    monkeypatch.setattr(ai_focus, 'call_model', model)
    assert ai_focus.review_focus(asset, settings, {}, tmp_path / 'cache')['recommendation'] == 'keep'


@pytest.mark.parametrize('manual', [False, True], ids=['automatic', 'manual'])
def test_actual_body_check_rescan_invalidates_previous_ai_result(automatic, tmp_path, monkeypatch, manual):
    from ai_cull_assistant import analysis_modes, face_focus, body_pipeline
    from ai_cull_assistant.screening import ScreeningResult
    from test_primary_quality import face as face_measurement, FACE
    asset, settings = automatic
    if manual:
        settings.photos[settings.key(asset)] = {'selected_faces': [FACE]}
        settings.photos[settings.key(asset)][quality.CONFIRMATION_KEY] = quality.confirm_selection(asset, settings)
    def measure(*a, **kw):
        evidence = face_measurement(body=False)
        if not manual:
            evidence['automatic_subject'] = dict(version='single-detected-face-v1', candidate_count=1,
                detection_threshold=settings.detection_confidence, landmarks_available=True, box=FACE)
        return ScreeningResult(False, 'subject_not_obviously_blurred', True,
                               focus_evidence=evidence, source_size=(600,600))
    monkeypatch.setattr(face_focus, 'assess_asset_focus', measure)
    monkeypatch.setattr(body_pipeline, 'apply_body_check', lambda asset, face, *a: face)
    monkeypatch.setattr(ai_focus, 'detail_features', lambda *a: None)
    def model(_profile, prompt, images):
        value = json.loads(prompt.split('返回格式：')[1])
        assert len(value['parts']) == 1
        value['parts'][0].update(visibility='visible', severity='clear', evidence_sufficient=True)
        return {'text': json.dumps(value)}
    monkeypatch.setattr(ai_focus, 'call_model', model)
    first = analysis_modes.assess_asset_mode(asset, crop_settings=settings, body_check=False)
    asset.clarity_evidence = first.focus_evidence
    result = ai_focus.review_focus(asset, settings, {}, tmp_path / 'cache')
    assert protocol.current_result(result, asset, settings) is not None
    previous_scope = result['scope']
    second = analysis_modes.assess_asset_mode(asset, crop_settings=settings, body_check=True)
    asset.clarity_evidence = second.focus_evidence
    current_scope = protocol.make_scope(asset, settings)
    assert current_scope['enabled_parts'] == ['face', 'body']
    assert current_scope['selection_origin'] == ('human' if manual else 'automatic')
    assert current_scope != previous_scope
    assert protocol.current_result(result, asset, settings) is None
    assert quality.current_selection(asset, settings) is not None


@pytest.mark.parametrize('manual', [False, True], ids=['automatic', 'manual'])
def test_cache_hit_source_change_rejected_by_outer_scope_check(automatic, tmp_path, monkeypatch, manual):
    from pathlib import Path
    asset, settings = automatic
    if manual:
        box = asset.clarity_evidence['automatic_subject']['box']
        settings.photos[settings.key(asset)] = {'selected_faces': [box]}
        settings.photos[settings.key(asset)][quality.CONFIRMATION_KEY] = quality.confirm_selection(asset, settings)
        asset.clarity_evidence = {'measurement_inputs': quality.measurement_inputs(asset, settings, ['face'])}
    monkeypatch.setattr(ai_focus, 'detail_features', lambda *a: None)
    calls = []
    def model(_profile, prompt, images):
        calls.append(1)
        value = json.loads(prompt.split('返回格式：')[1])
        value['parts'][0].update(visibility='visible', severity='clear', evidence_sufficient=True)
        return {'text': json.dumps(value)}
    monkeypatch.setattr(ai_focus, 'call_model', model)
    root = tmp_path / 'cache'
    ai_focus.review_focus(asset, settings, {}, root)
    cached_result_paths = set((root / protocol.PROMPT_VERSION).glob('*.json'))
    assert len(cached_result_paths) == 1
    original_read = Path.read_text
    cache_hits = []
    def read_then_change_source(path, *args, **kwargs):
        value = original_read(path, *args, **kwargs)
        if path in cached_result_paths:
            cache_hits.append(path)
            Image.new('RGB', (700,700), 'white').save(asset.primary_path)
        return value
    monkeypatch.setattr(Path, 'read_text', read_then_change_source)
    with pytest.raises(protocol.FocusProtocolError):
        ai_focus.review_focus(asset, settings, {}, root)
    assert len(cache_hits) == 1
    assert len(calls) == 1  # The stale cached result was read without another model call.
