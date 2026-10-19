"""Synthetic contract tests; these are not real-image visibility labels."""
from copy import deepcopy
import numpy as np
from ai_cull_assistant.focus_metrics import focus_metrics
from ai_cull_assistant import primary_quality as quality
from test_primary_quality import face


def test_supported_low_detail_retains_severe_without_visibility_claim():
    x = np.arange(512)
    plane = np.tile((127.5 + 120*np.sin(x*2*np.pi*8/512)).astype(np.uint8), (512, 1))
    result = focus_metrics(np.repeat(plane[:, :, None], 3, axis=2),
        landmarks=((170,205),(342,205),(256,280),(205,365),(307,365)))
    assert result['metric_state'] == 'severe_blur'
    assert result['state'] == 'severe_blur'
    assert all(r['effective_area']['source_supported'] for r in result['regions'].values())
    assert result['visibility'] == {'state':'unknown', 'basis':'not_measured'}
    assert 'weak_eye_and_face_detail' in result['reasons']
    assert 'face_visibility_unverified' not in result['reasons']


def test_supported_severe_face_remains_severe_without_trusting_visibility_flags():
    for visibility in (None, {'state':'visible', 'basis':'landmarks'}):
        item = face('severe_blur')
        item['visibility'] = visibility
        result = quality._face_part(item)
        assert result['state'] == 'severe_blur'
        assert result['reasons'] == ['weak_eye_and_face_detail']


def test_out_of_source_alignment_does_not_supply_observed_detail():
    image = np.random.default_rng(8).integers(0,256,(256,256,3),dtype=np.uint8)
    result = focus_metrics(image, landmarks=((3,20),(100,75),(53,90),(15,110),(85,140)))
    assert result['state'] == 'uncertain'
    assert any(not r['effective_area']['source_supported'] for r in result['regions'].values())
    assert 'face_region_outside_source' in result['reasons']


def test_source_support_veto_cannot_be_bypassed_with_clear_state():
    for state in ('clear', 'severe_blur'):
        item = face(state)
        item['regions']['eye_band']['effective_area'] = {'source_supported': False}
        assert quality._face_part(item)['reasons'] == ['face_region_outside_source']


def test_face_reason_separation_preserves_legacy_record_and_specific_blocker():
    item = face('uncertain')
    item['reasons'] = ['missing_reliable_landmarks', 'body_pose_unreliable', 'body_confidence_not_supported']
    item['body'] = {'reasons':['body_pose_unreliable']}
    original = deepcopy(item)
    result = quality._face_part(item)
    assert result['reasons'] == ['missing_reliable_landmarks']
    assert item == original


def test_body_failure_retains_original_cause():
    result = quality._body_part({'state':'uncertain', 'reasons':['body_pose_unreliable']}, linked=True)
    assert result['reasons'] == ['body_pose_unreliable', 'body_confidence_not_supported']


def test_structured_reason_summary_keeps_parts_separate():
    result = quality._policy_payload(None, [], [
        {'part':'selection','reason':'primary_selection_unconfirmed'},
        {'part':'face','reasons':['face_visibility_unverified']},
        {'part':'body','reasons':['body_pose_unreliable']},
    ], [])
    assert result['reasons_by_part'] == {'selection':['primary_selection_unconfirmed'],
        'face':['face_visibility_unverified'], 'body':['body_pose_unreliable'], 'measurement':[]}


def test_coordinate_round_trip_rejects_inconsistent_measurement_frame():
    region = {'version':'body-focus-v2', 'coordinate_space':'exif_transposed_image_pixels_xyxy',
        'coordinate_image_size':[1000,800], 'measurement_image_size':[400,300],
        'measurement_origin_px':[100,200], 'measurement_box':[20,30,80,100],
        'box':[120,230,180,300]}
    assert quality._body_region_coordinates_supported(region)
    region['box'][2] += 1
    assert not quality._body_region_coordinates_supported(region)
    region['box'][2] -= 1
    region['measurement_image_size'][1] = 50
    assert not quality._body_region_coordinates_supported(region)


def test_decode_timing_distinguishes_unrecorded_from_short_operation():
    from ai_cull_assistant.scan_timing import timing_summary
    assert 'RAW/原图解码 未记录' in timing_summary({})
    assert 'RAW/原图解码 0.012 秒' in timing_summary({'decode': .0123})
    assert 'RAW/原图解码 <0.001 秒' in timing_summary({'decode': .0001})


from test_automatic_primary_quality import automatic, detector_evidence


def test_rescued_face_cannot_inherit_initial_single_candidate(automatic):
    asset, settings = automatic
    evidence = detector_evidence(settings)
    box = evidence['automatic_subject']['box']
    provenance = {'diagnostic_version':'subject-candidates-v1',
        'coordinate_space':'normalized_exif_oriented',
        'initial_candidates':[{'box':box, 'landmarks_available':True}],
        'chosen_initial_index':0, 'chosen_source':'initial_yunet'}
    evidence['automatic_subject']['candidate_provenance'] = provenance
    inputs = quality.measurement_inputs(asset, settings, ['face'])
    selection = quality.automatic_selection(asset, settings, evidence, inputs)
    assert selection and selection['human_confirmed'] is False
    provenance.update(chosen_initial_index=None, chosen_source='head_face_retry')
    assert quality.automatic_selection(asset, settings, evidence, inputs) is None
    provenance.update(chosen_initial_index=0, chosen_source='initial_yunet')
    provenance['initial_candidates'][0]['box'] = [.5,.1,.2,.2]
    assert quality.automatic_selection(asset, settings, evidence, inputs) is None


def test_malformed_legacy_body_reason_does_not_crash_face_reason_reader():
    item = face()
    item['body'] = {'reasons':[{'invalid':'nonstring'}]}
    item['body_evidence'] = [{'reasons':None}]
    assert quality._face_part(item)['state'] == 'clear'
    assert quality._body_part(item['body'])['state'] == 'uncertain'
