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


def test_api_clear_conflicting_with_native_smear_stays_pending(tmp_path):
    p=tmp_path/'a.jpg'
    asset=PhotoAsset('a',p,p,None,p,datetime.now(),'.jpg',clarity_version='clarity-v2')
    screened=ScreeningResult(False,'face_focus_uncertain',True,
                            focus_evidence={'state':'uncertain','motion_suspect':True})
    response=dict(status='clear',reason='眼睛看起来清楚',source='api')
    _apply_focus_review(asset,screened,response)
    assert asset.ai_focus_result['status']=='uncertain'
    assert asset.ai_focus_result['api_status']=='clear'
    assert not asset.auto_rejected
    assert response['status']=='clear'
