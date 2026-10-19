"""Explicit body scope cannot resurrect automatic linked-body evidence."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import pytest
from PIL import Image
from ai_cull_assistant import analysis_modes as modes, primary_quality as quality, body_pipeline, subject
from ai_cull_assistant.crop_settings import CropSettings
from ai_cull_assistant.models import PhotoAsset
from ai_cull_assistant.screening import ScreeningResult
from test_primary_quality import FACE, BODY, face
from ai_cull_assistant import body_focus as body

# Self-contained synthetic evidence for retained body v2; no Round 4 code imported.
def supported_record(state='severe_blur'):
    severe = state == 'severe_blur'
    regions = []
    for name, family, box in [('torso', 'torso', [70, 100, 170, 220]),
                             ('left_upper_arm', 'left_arm', [40, 130, 90, 220])]:
        regions.append(dict(name=name, family=family, box=box, version=body.VERSION,
            coordinate_space='exif_transposed_image_pixels_xyxy', coordinate_image_size=[400, 500],
            measurement_image_size=[400, 500], measurement_origin_px=[0, 0], measurement_box=list(box),
            ownership={'status':'geometrically_supported', 'basis':'geometric_association',
                       'semantic_ownership_verified':False, 'mask_kind':'binary_foreground',
                       'selected_envelope':[-120, 20, 340, 480], 'competing_envelopes':[]},
            evidence=dict(state=state, reason='weak_detail_at_both_scales' if severe else 'strong_consistent_detail',
                          contrast=.3, texture_fraction=.4, laplacian_normalized=.0001 if severe else .01,
                          coarse_laplacian_normalized=.0001 if severe else .002,
                          edge_curvature=.05 if severe else .3, edge_anisotropy=.2)))
    return dict(version=body.VERSION, state=state, confidence=.99, source_size=[400, 500], regions=regions,
                reasons=['multiple_major_body_regions_severely_blurred' if severe else 'multiple_major_body_regions_clear'],
                diagnostics={'person_confidence':.99, 'pose_confidence':.99,
                             'source_size':[400,500], 'face_box_px':[80,40,130,100]})

@pytest.fixture
def scoped(tmp_path):
 p=tmp_path/'人物.png';Image.new('RGB',(400,500),'gray').save(p)
 a=PhotoAsset(p.stem,p,p,None,p,datetime.now(),'.png',preview_path=p)
 s=CropSettings();s.photos[s.key(a)]={'selected_faces':[FACE]}
 return a,s,tmp_path

def confirm(a,s):s.photos[s.key(a)][quality.CONFIRMATION_KEY]=quality.confirm_selection(a,s)
def measured_face(*args,**kwargs):return ScreeningResult(False,'face_test',True,focus_evidence=face(body=False),source_size=(400,500))

@pytest.mark.parametrize('selected',[[],[BODY]])
def test_direct_legacy_body_entry_respects_explicit_scope(scoped,monkeypatch,selected):
 a,s,p=scoped;s.photos[s.key(a)]['selected_bodies']=selected
 original=measured_face();before=deepcopy(original.focus_evidence)
 monkeypatch.setattr(subject,'detail_features_list',lambda *a,**k:pytest.fail('automatic subject must not run'))
 result=body_pipeline.apply_body_check(a,original,s,p/'cache')
 assert result is original and result.focus_evidence==before and not (p/'cache').exists()

@pytest.mark.parametrize('selected',[[],[BODY]])
def test_end_to_end_override_cannot_reuse_prior_automatic_reject(scoped,monkeypatch,selected):
 a,s,p=scoped;calls=[]
 monkeypatch.setattr('ai_cull_assistant.face_focus.assess_asset_focus',measured_face)
 monkeypatch.setattr(subject,'detail_features_list',lambda *a,**k:[SimpleNamespace(face=FACE)])
 record=supported_record('severe_blur');x,y,w,h=FACE
 record['diagnostics']['face_box_px']=[x*400,y*500,(x+w)*400,(y+h)*500]
 record['review_kind']='motion_suspected'
 def automatic(*args):calls.append(1);return deepcopy(record)
 monkeypatch.setattr('ai_cull_assistant.body_focus.assess_body_focus',automatic)
 confirm(a,s);first=modes.assess_asset_mode(a,crop_settings=s,cache_dir=p/'cache',body_check=True)
 assert first.rejected and len(calls)==1
 s.photos[s.key(a)]['selected_bodies']=selected;confirm(a,s)
 saved_confirmation=deepcopy(s.photos[s.key(a)][quality.CONFIRMATION_KEY])
 manual=dict(box=BODY,source='manual',evidence_kind='body',state='uncertain',confidence=None,regions=[],eye_evidence=False,reasons=['manual_body_without_reliable_pose'])
 monkeypatch.setattr('ai_cull_assistant.body_focus.assess_body_region',lambda *a:deepcopy(manual))
 result=modes.assess_asset_mode(a,crop_settings=s,cache_dir=p/'cache',body_check=True)
 assert not result.rejected and len(calls)==1
 assert s.photos[s.key(a)][quality.CONFIRMATION_KEY]==saved_confirmation
 assert quality.confirmed_selection(a,s)==saved_confirmation
 assert result.focus_evidence['enabled_parts']==(['face','body'] if selected else ['face'])
 assert len(result.focus_evidence['body_results'])==len(selected)
 assert all(x.get('source')=='manual' for x in result.focus_evidence['body_results'])
 assert all('body' not in f for f in result.focus_evidence['subjects'] if f.get('evidence_kind')=='face')
 if selected:assert result.focus_evidence['recommendation']=='review'
 else:assert all(part['part']=='face' for person in result.focus_evidence['primary_quality']['subjects'] for part in person['parts'])

@pytest.mark.parametrize('selected',[[],[BODY]])
def test_old_scope_measurement_invalidated_without_invalidating_confirmation(scoped,selected):
 a,s,p=scoped;s.photos[s.key(a)]['selected_bodies']=selected;confirm(a,s)
 confirmation=deepcopy(quality.confirmed_selection(a,s));parts=['face','body']
 inputs=quality.measurement_inputs(a,s,parts)
 # Reconstruct the exact previous input digest, with no explicit-scope contract.
 from ai_cull_assistant.body_focus import VERSION as body_version
 from ai_cull_assistant.face_focus import VERSION as face_version
 from ai_cull_assistant.roi_contract import SCHEMA_VERSION
 params={'global':{k:getattr(s,k) for k in quality._CROP_KEYS},'photo':{k:s.photos[s.key(a)][k] for k in quality._ENTRY_KEYS if k in s.photos[s.key(a)]},'confirmation':s.photos[s.key(a)].get(quality.CONFIRMATION_KEY),'versions':[SCHEMA_VERSION,subject.DETECTION_VERSION,face_version,body_version],'enabled_parts':parts}
 old=dict(inputs,parameters_digest=quality._digest(params));assert old!=inputs
 assert quality.current_selection(a,s,{'measurement_inputs':old}) is None
 assert quality.confirmed_selection(a,s)==confirmation
 assert quality.current_selection(a,s,{'measurement_inputs':inputs}) is not None

def test_automatic_measurement_digest_unchanged(scoped):
 a,s,p=scoped
 inputs=quality.measurement_inputs(a,s,['face','body'])
 from ai_cull_assistant.body_focus import VERSION as bv
 from ai_cull_assistant.face_focus import VERSION as fv
 from ai_cull_assistant.roi_contract import SCHEMA_VERSION
 entry=s.photos[s.key(a)]
 params={'global':{k:getattr(s,k) for k in quality._CROP_KEYS},'photo':{k:entry[k] for k in quality._ENTRY_KEYS if k in entry},'confirmation':entry.get(quality.CONFIRMATION_KEY),'versions':[SCHEMA_VERSION,subject.DETECTION_VERSION,fv,bv],'enabled_parts':['face','body']}
 assert inputs['parameters_digest']==quality._digest(params)

@pytest.mark.parametrize('selected',[[],[BODY]])
def test_missing_measurement_inputs_cannot_replay_old_manual_body(scoped,selected):
 a,s,p=scoped;s.photos[s.key(a)]['selected_bodies']=selected;confirm(a,s)
 confirmation=deepcopy(quality.confirmed_selection(a,s))
 historical={'subjects':[face(body=True)],'body_results':[],'measurement_confirmation_digest':confirmation['digest']}
 assert quality.current_selection(a,s,historical)['human_confirmed'] is True
 assert not quality.manual_body_measurement_current(a,s,historical)
 assert quality.evaluate_local_evidence(a,s,historical)['recommendation']=='review'
 assert quality.confirmed_selection(a,s)==confirmation

def test_absent_body_override_preserves_legacy_confirmation_lookup(scoped):
 a,s,p=scoped;confirm(a,s)
 saved=quality.confirmed_selection(a,s)
 assert quality.current_selection(a,s,{'measurement_confirmation_digest':saved['digest']})==saved

@pytest.mark.parametrize('selected',[[],[BODY]])
def test_confirmed_unmeasured_scope_can_request_ai_but_not_trust_old_local_reject(scoped,monkeypatch,selected):
 from ai_cull_assistant import focus_protocol,lightroom_results
 from ai_cull_assistant.processing_job import _apply_focus_review
 import json
 a,s,p=scoped;s.photos[s.key(a)]['selected_bodies']=selected;confirm(a,s)
 c=quality.confirmed_selection(a,s);face_id=next(r['id'] for r in c['regions'] if r['kind']=='face')
 old_policy={'version':quality.POLICY_VERSION,'selection_origin':'human','selection_digest':c['digest'],'confirmation_digest':c['digest'],'enabled_parts':['face','body'] if selected else ['face'],'recommendation':'reject','subjects':[{'id':face_id,'region_ids':[r['id'] for r in c['regions']],'parts':[],'original_evidence':{},'original_body_evidence':[{'box':BODY,'regions':[]}]}]}
 a.clarity_evidence={'measurement_confirmation_digest':c['digest'],'primary_quality':old_policy,'body_results':[]}
 scope=focus_protocol.make_scope(a,s)
 assert len(scope['subjects'])==1+len(selected)  # old association cannot merge the manual ROIs
 assert scope['enabled_parts']==(['face','body'] if selected else ['face'])
 assert quality.evaluate_local_evidence(a,s,a.clarity_evidence)['recommendation']=='review'
 a.focus_scope=scope
 monkeypatch.setattr(lightroom_results,'_live_scope_matches',lambda *a:True)
 assert lightroom_results.local_recommendation(a)=='review'
 responses=[]
 for person in scope['subjects']:
  ctx=focus_protocol.make_context(scope,person['id'],evidence_parts=tuple(scope['enabled_parts']))
  reply=focus_protocol.response_template(ctx);reply['reason']='Uncertain test observation'
  for part in reply['parts']:part.update(reason='Unknown test evidence',location=part['part'])
  responses.append(focus_protocol.parse_response(json.dumps(reply),ctx))
 reviewed=focus_protocol.assemble_result(scope,responses)
 screened=measured_face()
 screened.focus_evidence=deepcopy(a.clarity_evidence)
 _apply_focus_review(a,screened,reviewed,s)
 assert not a.auto_rejected and not screened.rejected
 assert quality.confirmed_selection(a,s)==c


def test_empty_body_scope_cannot_claim_face_and_body_measurement_parts(scoped):
 a,s,p=scoped;s.photos[s.key(a)]['selected_bodies']=[];confirm(a,s)
 mismatched={'measurement_inputs':quality.measurement_inputs(a,s,['face','body'])}
 assert not quality.manual_body_measurement_current(a,s,mismatched)
 valid={'measurement_inputs':quality.measurement_inputs(a,s,['face'])}
 assert quality.manual_body_measurement_current(a,s,valid)
