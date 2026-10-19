from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from ai_cull_assistant import primary_quality as quality
from ai_cull_assistant.crop_settings import CropSettings

FACE = [.1, .1, .35, .4]
OTHER = [.6, .1, .3, .4]
BODY = [.05, .05, .5, .9]


@pytest.fixture
def selected(tmp_path):
    path = tmp_path / 'photo.jpg'
    path.write_bytes(b'controlled source identity; no model inference')
    asset = SimpleNamespace(primary_path=path, jpg_path=path, raw_path=None,
                            analysis_mode='portrait')
    settings = CropSettings(photos={CropSettings().key(asset):{'selected_faces':[FACE]}})
    return asset, settings


def confirm(asset, settings):
    value = quality.confirm_selection(asset, settings)
    settings.photos[settings.key(asset)][quality.CONFIRMATION_KEY] = value
    return value


def face(state='clear', box=FACE, participant=1, body=True):
    severe = state == 'severe_blur'
    fine = dict(contrast=.3, laplacian_normalized=.0005 if severe else .01,
                gradient_p90_normalized=.4, edge_curvature=.10 if severe else .3,
                orientation_coherence=.2)
    region = dict(native_size=[150,80], resize_scale=1., fine_to_coarse_ratio=2.,
                  gradient_retention=.7, fine=deepcopy(fine), coarse=deepcopy(fine),
                  native_fine=deepcopy(fine), native_coarse=deepcopy(fine))
    item = dict(evidence_kind='face', normalized_box=box, participant=participant,
                state=state, face_measurement_state=state, landmarks_used=True,
                native_face_width=150., native_eye_distance=80.,
                reasons=['weak_eye_and_face_detail' if severe else 'strong_landmark_aligned_detail'],
                fine=deepcopy(fine), coarse=deepcopy(fine),
                regions=dict(eye_band=deepcopy(region), face_core=deepcopy(region)))
    if body:
        item['body'] = dict(state='clear', reasons=['body_not_visible_headshot'], regions=[])
    return item


def measured(asset, settings, subjects, bodies=None):
    value = dict(subjects=subjects, measurement_confirmation_digest=confirm(asset,settings)['digest'])
    if 'selected_bodies' in settings.photos.get(settings.key(asset), {}):
        value['measurement_inputs'] = quality.measurement_inputs(asset, settings, ['face', 'body'])
    if bodies is not None:
        value['body_results'] = bodies
    return value


@pytest.mark.parametrize('entry', [{},{'primary_face_index':0},
    {'selected_faces':[], 'manual_face':FACE}, {'selected_faces':[FACE],'hidden':True},
    {'selected_faces':[FACE],'subjects_disabled':True}])
def test_automatic_empty_and_hidden_are_not_confirmation(entry):
    assert quality.selection_regions(entry) == []


@pytest.mark.parametrize('bad', [[True,.1,.2,.2], [float('nan'),.1,.2,.2],
    [.1,.1,float('inf'),.2], [-.1,.1,.2,.2], [.1,.1,0,.2], [.9,.1,.2,.2]])
def test_one_bad_roi_invalidates_whole_collection(bad):
    with pytest.raises(ValueError):
        quality.selection_regions({'selected_faces':[FACE,bad]})


def test_exact_duplicates_deduplicate_but_near_identifier_collision_rejected():
    assert len(quality.selection_regions({'selected_faces':[FACE,FACE]})) == 1
    near = list(FACE); near[0] += 1e-8
    with pytest.raises(ValueError):
        quality.selection_regions({'selected_faces':[FACE,near]})


def test_confirmation_survives_json_but_source_and_roi_changes_invalidate(selected):
    asset, settings = selected
    current = confirm(asset,settings)
    settings = replace(settings, photos=json.loads(json.dumps(settings.photos)))
    assert quality.confirmed_selection(asset,settings) == current
    settings.photos[settings.key(asset)]['face_crops'] = {'new':{'scale_factor':1.2}}
    assert quality.confirmed_selection(asset,settings) is None
    confirm(asset,settings)
    asset.primary_path.write_bytes(b'changed original source')
    assert quality.confirmed_selection(asset,settings) is None


def test_missing_or_old_measurement_stamp_cannot_reuse_evidence(selected):
    asset,settings=selected
    evidence=measured(asset,settings,[face()])
    evidence.pop('measurement_confirmation_digest')
    assert quality.evaluate_local_evidence(asset,settings,evidence)['recommendation']=='review'
    evidence['measurement_confirmation_digest']='old'
    assert quality.evaluate_local_evidence(asset,settings,evidence)['recommendation']=='review'


def test_all_required_supported_parts_clear_keep(selected):
    asset,settings=selected
    result=quality.evaluate_local_evidence(asset,settings,measured(asset,settings,[face()]))
    assert result['recommendation']=='keep'
    assert result['subjects'][0]['parts'][1]['state']=='not_visible'


@pytest.mark.parametrize('second', ['clear','uncertain','missing'])
def test_severe_confirmed_person_rejects_without_rewriting_other_person(selected,second):
    asset,settings=selected
    settings.photos[settings.key(asset)]['selected_faces'].append(OTHER)
    subjects=[face('severe_blur')]
    if second!='missing':
        subjects.append(face(second,OTHER,2))
    evidence=measured(asset,settings,subjects)
    before=deepcopy(evidence)
    result=quality.evaluate_local_evidence(asset,settings,evidence)
    assert result['recommendation']=='reject'
    assert result['subjects'][1]['parts'][0]['state'] == ('unassessed' if second=='missing' else second)
    assert evidence==before


def test_unselected_blurred_bystander_not_counted(selected):
    asset,settings=selected
    result=quality.evaluate_local_evidence(asset,settings,
        measured(asset,settings,[face(),face('severe_blur',OTHER,2)]))
    assert result['recommendation']=='keep'
    assert len(result['subjects'])==1


@pytest.mark.parametrize('change', ['small','landmarks','nan','inf','unsupported'])
def test_unsupported_severe_claim_never_rejects(selected,change):
    asset,settings=selected
    item=face('severe_blur')
    if change=='small': item['native_face_width']=50
    elif change=='landmarks': item['landmarks_used']=False
    elif change in {'nan','inf'}: item['regions']['face_core']['fine']['contrast']=float(change)
    else: item={'state':'severe_blur','reliability':'adequate','normalized_box':FACE,'evidence_kind':'face'}
    assert quality.evaluate_local_evidence(asset,settings,measured(asset,settings,[item]))['recommendation']=='review'


def test_clear_face_does_not_invent_unmeasured_limbs(selected):
    asset,settings=selected
    result=quality.evaluate_local_evidence(asset,settings,measured(asset,settings,[face(body=False)]))
    assert result['recommendation']=='review'
    assert result['subjects'][0]['parts'][1]['state']=='unassessed'


def body(severe_names):
    from ai_cull_assistant.body_focus import _aggregate_regions
    regions=[]
    for name,family in [('torso','torso'),('left_upper_arm','left_arm'),('right_upper_arm','right_arm')]:
        severe=name in severe_names
        regions.append(dict(name=name,family=family,box=[1,2,100,150],evidence=dict(
            state='severe_blur' if severe else 'clear',
            reason='weak_detail_at_both_scales' if severe else 'strong_consistent_detail',
            contrast=.3,texture_fraction=.4,laplacian_normalized=.0001 if severe else .01,
            coarse_laplacian_normalized=.0001 if severe else .002,
            edge_curvature=.05 if severe else .3,edge_anisotropy=.2)))
    state,reasons=_aggregate_regions(regions)
    return dict(box=BODY,confidence=.9,associated_face=1,association='pose_head_overlap',
                state=state,reasons=reasons,regions=regions,evidence_kind='body')


@pytest.mark.parametrize('severe_names, expected', [(['left_upper_arm'],'review'),
    (['torso','left_upper_arm'],'reject'), ([], 'keep')])
def test_existing_body_consensus_and_unique_same_person_binding(selected,severe_names,expected):
    asset,settings=selected
    settings.photos[settings.key(asset)]['selected_bodies']=[BODY]
    result=quality.evaluate_local_evidence(asset,settings,
        measured(asset,settings,[face()], [body(severe_names)]))
    assert result['recommendation']==expected
    assert len(result['subjects'])==1
    assert len(result['subjects'][0]['region_ids'])==2
    assert result['subjects'][0]['parts'][1]['cause']=='unassessed'


def test_ambiguous_body_cannot_supply_severe_evidence(selected):
    asset,settings=selected
    item=body(['torso','left_upper_arm']); item['association']='ambiguous'
    result=quality.evaluate_local_evidence(asset,settings,
        measured(asset,settings,[face(body=False)], [item]))
    assert result['recommendation']=='review'


@pytest.mark.parametrize('field,value',[('association',[]),('associated_face',True),('region_name',[])])
def test_malformed_body_fields_are_review_not_exception(selected,field,value):
    asset,settings=selected
    settings.photos[settings.key(asset)]['selected_faces']=[]
    settings.photos[settings.key(asset)]['selected_bodies']=[BODY]
    item=body(['torso','left_upper_arm'])
    if field=='region_name': item['regions'][0]['name']=value
    else: item[field]=value
    result=quality.evaluate_local_evidence(asset,settings,measured(asset,settings,[],[item]))
    assert result['recommendation']=='review'


def test_first_confirmed_rescan_ignores_old_unconfirmed_policy(selected,monkeypatch):
    from ai_cull_assistant import analysis_modes, face_focus
    from ai_cull_assistant.screening import ScreeningResult
    from ai_cull_assistant.participant_review import needs_person_review
    asset,settings=selected
    asset.clarity_evidence={'primary_quality':{'confirmation_digest':None}}
    confirm(asset,settings)
    def assess(asset,**kwargs):
        assert not needs_person_review(asset,settings.photos[settings.key(asset)])
        return ScreeningResult(False,'subject_not_obviously_blurred',True,
                               focus_evidence=face(),source_size=(600,600))
    monkeypatch.setattr(face_focus,'assess_asset_focus',assess)
    result=analysis_modes.assess_asset_mode(asset,crop_settings=settings)
    assert result.focus_evidence['primary_quality']['recommendation']=='keep'
    assert not asset.person_review_pending
    from ai_cull_assistant.lightroom_results import local_recommendation
    asset.clarity_evidence=result.focus_evidence
    assert local_recommendation(asset)=='keep'
    asset.primary_path.write_bytes(b'new source despite old in-memory evidence')
    assert local_recommendation(asset)=='review'
