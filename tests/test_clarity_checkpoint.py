from datetime import datetime
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.processing_job import _asset_to_dict, _asset_from_dict, _apply_focus_review
from ai_cull_assistant.screening import ScreeningResult


def test_legacy_checkpoint_defaults_and_new_evidence_round_trip(tmp_path):
    p=tmp_path/'a.jpg';p.write_bytes(b'test')
    asset=PhotoAsset('a',p,p,None,p,datetime.now(),'.jpg')
    old=_asset_to_dict(asset);old.pop('clarity_version');old.pop('clarity_evidence')
    assert _asset_from_dict(old).clarity_version is None
    asset.clarity_version='clarity-v2'
    asset.clarity_evidence=dict(state='uncertain',reasons=['possible_directional_smear'])
    saved=_asset_from_dict(_asset_to_dict(asset))
    assert saved.clarity_evidence==asset.clarity_evidence
    assert saved.clarity_version=='clarity-v2'


def test_api_clear_conflict_is_advisory_and_keeps_valid_ai_priority(tmp_path):
    p=tmp_path/'a.jpg'
    p.write_bytes(b'controlled source identity for application-unit test')
    asset=PhotoAsset('a',p,p,None,p,datetime.now(),'.jpg',clarity_version='clarity-v2')
    screened=ScreeningResult(False,'face_focus_uncertain',True,
                            focus_evidence={'state':'uncertain','motion_suspect':True})
    from quality_cases import technical_result
    from ai_cull_assistant.crop_settings import CropSettings
    crops=CropSettings()
    response=technical_result(asset,crops,'clear','当前原生细节证据清楚')
    _apply_focus_review(asset,screened,response,crops)
    assert asset.ai_focus_result['status']=='clear'
    assert asset.ai_focus_result['local_conflict'] is True
    assert screened.focus_evidence['motion_suspect'] is True
    assert not asset.auto_rejected
    assert response['status']=='clear'


def test_legacy_normalization_is_idempotent_and_never_rejects(tmp_path):
    p=tmp_path/'legacy.jpg';p.write_bytes(b'controlled legacy source')
    asset=PhotoAsset('legacy',p,p,None,p,datetime.now(),'.jpg')
    screened=ScreeningResult(False,'unknown',False)
    old={'status':'blur','reason':'old ordinary blur'}
    _apply_focus_review(asset,screened,old)
    first=asset.ai_focus_result
    _apply_focus_review(asset,screened,first)
    assert asset.ai_focus_result==first
    assert asset.ai_focus_result['legacy_result']==old
    assert not asset.auto_rejected
