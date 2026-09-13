from datetime import datetime
from types import SimpleNamespace

import pytest
from PIL import Image

from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.screening import ScreeningResult, screen_assets
from ai_cull_assistant.processing_job import _apply_focus_review, _normalise_options


def asset_at(tmp_path):
    path = tmp_path / 'photo.png'
    Image.new('RGB', (100, 150)).save(path)
    return PhotoAsset('photo', path, path, None, path, datetime.now(), '.png',
                      preview_path=path, clarity_version='clarity-v2')


def test_experiment_disabled_does_not_run_body_analysis(tmp_path, monkeypatch):
    assert _normalise_options({})['body_screening'] is False
    monkeypatch.setattr('ai_cull_assistant.face_focus.assess_asset_focus',
                        lambda *a, **kw: ScreeningResult(False, 'subject_not_obviously_blurred', True,
                                                         focus_evidence={'state': 'clear'}))
    def unexpected(*args):
        raise AssertionError('body models must not run when disabled')
    monkeypatch.setattr('ai_cull_assistant.body_pipeline.apply_body_check', unexpected)
    asset = asset_at(tmp_path)
    screen_assets([asset])
    assert asset.clarity_evidence == {'state': 'clear'}


def test_body_pending_keeps_face_evidence_and_blocks_selection(tmp_path, monkeypatch):
    from ai_cull_assistant.body_pipeline import apply_body_check
    from ai_cull_assistant.body_focus import VERSION
    from ai_cull_assistant.ai_project import ReviewProject
    monkeypatch.setattr('ai_cull_assistant.subject.detail_features',
                        lambda *a: SimpleNamespace(face=(.2, .1, .3, .3)))
    monkeypatch.setattr('ai_cull_assistant.body_focus.assess_body_focus',
                        lambda *a: dict(version=VERSION, state='uncertain', review_kind='unsupported',
                                        reasons=['body_pose_unreliable'], regions=[]))
    asset = asset_at(tmp_path)
    screened = ScreeningResult(False, 'subject_not_obviously_blurred', True,
                               focus_evidence={'state': 'clear', 'regions': {'eye': 'evidence'}})
    result = apply_body_check(asset, screened, CropSettings(), tmp_path/'cache')
    assert result.focus_evidence['face_state'] == 'clear'
    assert result.focus_evidence['regions'] == {'eye': 'evidence'}
    assert result.focus_evidence['state'] == 'uncertain'
    asset.clarity_evidence = result.focus_evidence
    asset.screening_reason = result.reason
    assert not ReviewProject._asset_is_admitted(asset)


@pytest.mark.parametrize('kind,body_observation,expected', [
    ('unsupported', 'absent', 'uncertain'),
    ('motion_suspected', None, 'uncertain'),
    ('motion_suspected', 'uncertain', 'uncertain'),
    ('motion_suspected', 'absent', 'clear'),
])
def test_api_face_clear_does_not_override_unresolved_body(tmp_path, kind, body_observation, expected):
    asset = asset_at(tmp_path)
    evidence = dict(state='uncertain', face_state='clear', body=dict(state='uncertain', review_kind=kind))
    screened = ScreeningResult(False, 'body_focus_uncertain', True, focus_evidence=evidence)
    response = dict(status='clear', reason='脸清楚', observations={})
    if body_observation is not None:
        response['observations']['body_motion'] = body_observation
    _apply_focus_review(asset, screened, response)
    assert asset.ai_focus_result['status'] == expected
    assert not asset.auto_rejected


def test_api_body_blur_is_rejected(tmp_path):
    asset = asset_at(tmp_path)
    screened = ScreeningResult(False, 'body_focus_uncertain', True)
    _apply_focus_review(asset, screened, dict(status='blur', reason='手臂存在明确重影'))
    assert asset.auto_rejected
    assert screened.reason == 'ai_focus_blur'


def test_unsupported_body_skips_paid_request_and_finishes_pending(tmp_path, monkeypatch):
    from threading import Event
    from ai_cull_assistant.processing_job import start_job
    from ai_cull_assistant.lightroom_results import focus_review_status
    source = tmp_path/'photos'
    source.mkdir()
    Image.new('RGB', (100, 150)).save(source/'photo.jpg')
    workspace = tmp_path/'workspace'
    options = dict(technical_screening=False)
    result = start_job(source, workspace, options, CropSettings(), mode='scan').run(
        options, CropSettings(), Event(), None)
    asset = result.assets[0]
    asset.screening_reason = 'body_focus_uncertain'
    asset.clarity_evidence = dict(state='uncertain', face_state='clear',
                                  body=dict(state='uncertain', review_kind='unsupported'))
    def unexpected(*args):
        raise AssertionError('unsupported body must not make a paid call')
    monkeypatch.setattr('ai_cull_assistant.ai_focus.review_focus', unexpected)
    logs = []
    completed = start_job(source, workspace, options, CropSettings(), result=result, mode='focus').run(
        options, CropSettings(), Event(), None, focus_profile={'id': 'test'}, on_log=logs.append)
    assert completed is not None
    assert completed.assets[0].ai_focus_result is None
    assert focus_review_status(completed.assets[0]) is True
    assert any('身体区域无法可靠判断' in line for line in logs)
