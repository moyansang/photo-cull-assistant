"""Manual scan modes and conservative, star-free local recommendations.

Reliability describes whether evidence can support a decision, separately from
technical quality. Body evidence never substitutes for eye evidence. Landscape
analysis measures internal regions without producing user-visible boxes.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

from .crop_settings import CropSettings
from .screening import ScreeningResult

VERSION = 'manual-modes-v1'
LANDSCAPE_VERSION = 'landscape-noise-guard-v1'
# Squared impulse-response energy of body_focus's Gaussian(sigma=.65) followed
# by its 3x3 Laplacian. White input noise variance is multiplied by this gain.
_LANDSCAPE_FINE_NOISE_GAIN = 9.690651
MODES = ('portrait', 'landscape')


def normalize_mode(mode: str | None) -> str:
    value = 'portrait' if mode is None else str(mode).strip().lower()
    if value not in MODES:
        raise ValueError('analysis mode must be portrait or landscape')
    return value


def _reliability(evidence: dict[str, Any]) -> str:
    if evidence.get('reliability') in {'adequate', 'insufficient'}:
        return evidence['reliability']
    if evidence.get('state') in {'clear', 'severe_blur'}:
        return 'adequate'
    # Borderline quality with supported landmarks/texture is a different state
    # from missing landmarks, too few pixels or no usable subject evidence.
    reasons = set(evidence.get('reasons', []))
    supported_concerns = {'borderline_focus_evidence', 'possible_directional_smear',
                          'at_least_one_major_body_region_uncertain', 'isolated_body_blur_evidence'}
    return 'adequate' if reasons and reasons <= supported_concerns else 'insufficient'


def _recommendation(subjects: list[dict[str, Any]]) -> str:
    """Never average away a participant issue or reject a mixed-quality group."""
    if not subjects or any(item.get('reliability') != 'adequate' for item in subjects):
        return 'review'
    states = [item.get('state') for item in subjects]
    if all(state == 'clear' for state in states):
        return 'keep'
    if all(state == 'severe_blur' for state in states):
        return 'reject'
    return 'review'


def _body_face_associations(faces, bodies):
    """Link body evidence only using a uniquely corresponding detected head.

    Mere body-box containment cannot distinguish a foreground person from a
    bystander. Unsupported/ambiguous overlap is retained as an association issue.
    No face identity recognition is implied by this within-image relationship.
    """
    from .crop_settings import face_box_key
    from .subject import _valid_normalized_box
    resolved = []
    for original in bodies:
        body = dict(original, subject_id='body:'+face_box_key(original['box']), evidence_kind='body', eye_evidence=False)
        body['associated_face'] = None
        head = body.get('head_box')
        possible = []
        contained = []
        for face in faces:
            box = face.get('normalized_box')
            if not _valid_normalized_box(box):
                continue
            fx,fy,fw,fh = box
            bx,by,bw,bh = body['box']
            if bx <= fx+fw/2 <= bx+bw and by <= fy+fh/2 <= by+bh:
                contained.append(face['participant'])
            if not _valid_normalized_box(head):
                continue
            hx,hy,hw,hh = head
            intersection=max(0,min(fx+fw,hx+hw)-max(fx,hx))*max(0,min(fy+fh,hy+hh)-max(fy,hy))
            fraction=intersection/max(fw*fh,1e-8)
            iou=intersection/max(fw*fh+hw*hh-intersection,1e-8)
            if fraction >= .35 and hx-.2*hw <= fx+fw/2 <= hx+1.2*hw and hy-.2*hh <= fy+fh/2 <= hy+1.2*hh:
                possible.append((fraction+iou,face['participant']))
        possible.sort(reverse=True)
        if possible and (len(possible)==1 or possible[0][0]-possible[1][0] >= .30):
            body['associated_face'] = possible[0][1]
            body['association'] = 'pose_head_overlap'
        elif possible or contained:
            body['association'] = 'ambiguous' if possible else 'unverified'
            body['state'] = 'uncertain'
            body['reliability'] = 'insufficient'
            body['reasons'] = list(body.get('reasons',[]))+['ambiguous_face_body_association']
        else:
            body['association'] = 'separate_body'
        resolved.append(body)
    # Reverse conflicts are not proof that two detections represent one person:
    # one may belong to an adjacent/occluded participant. Keep both unresolved.
    linked_counts = {}
    for body in resolved:
        if body['associated_face'] is not None:
            linked_counts[body['associated_face']] = linked_counts.get(body['associated_face'],0)+1
    for body in resolved:
        if linked_counts.get(body['associated_face'],0)>1:
            body.update(associated_face=None,association='reverse_conflict',state='uncertain',reliability='insufficient')
            body['reasons']=list(body.get('reasons',[]))+['multiple_bodies_match_same_face']
    return resolved


def _portrait_result(face_result, *, bodies=(), include_face=True):
    from .crop_settings import face_box_key
    evidence = dict(face_result.focus_evidence or {})
    face_subjects = []
    if include_face and face_result.face_found:
        participants = evidence.get('participants') or [evidence]
        for index, participant in enumerate(participants, 1):
            item = dict(participant, participant=index, evidence_kind='face',
                        eye_evidence=bool(participant.get('landmarks_used')))
            item['face_measurement_state'] = participant.get('face_state', participant.get('state'))
            item['face_reasons'] = list(participant.get('face_reasons', participant.get('reasons', [])))
            item['body_reasons'] = list(participant.get('body_reasons', []))
            item['reason_schema_version'] = 'part-reasons-v1'
            legacy_box = item.get('face_box') or (face_result.face_box if len(participants)==1 else None)
            if not item.get('normalized_box') and legacy_box and face_result.source_size:
                px,py,pw,ph=legacy_box
                iw,ih=face_result.source_size
                item['normalized_box']=(px/iw,py/ih,pw/iw,ph/ih)
            item['subject_id']='face:'+face_box_key(item.get('normalized_box',()))
            body = evidence.get('body') or {}
            body_people = body.get('participants') or [body]
            own_body = body_people[index-1] if index <= len(body_people) else {}
            if own_body:
                item['body'] = own_body
            if own_body and own_body.get('state') != 'clear':
                item['face_state'] = item.get('state')
                item['body'] = own_body
                item['state'] = ('severe_blur' if own_body.get('state') == 'severe_blur'
                                 else 'uncertain' if item.get('state') != 'severe_blur' else 'severe_blur')
                item['body_reasons'] = list(own_body.get('reasons', []))
                item['reasons'] = list(item.get('reasons', [])) + list(own_body.get('reasons', []))
            item['reliability'] = _reliability(item)
            face_subjects.append(item)
    bodies = _body_face_associations(face_subjects, bodies)
    for face in face_subjects:
        linked = [body for body in bodies if body['associated_face']==face['participant']]
        if not linked:
            continue
        face['body_evidence']=linked
        face['face_state']=face['state']
        if any(body.get('state')!='clear' for body in linked) and face['state']=='clear':
            face['state']='uncertain'
            face['reasons']=list(face.get('reasons',[]))+['face_body_quality_disagree']
        if any(_reliability(body)!='adequate' for body in linked):
            face['reliability']='insufficient'
    separate=[body for body in bodies if body['associated_face'] is None]
    subjects = face_subjects + [dict(body, participant=len(face_subjects)+index,
                                    reliability=_reliability(body)) for index, body in enumerate(separate, 1)]
    recommendation = _recommendation(subjects)
    state = 'clear' if recommendation == 'keep' else 'severe_blur' if recommendation == 'reject' else 'uncertain'
    evidence.update(mode='portrait', version=VERSION, state=state,
                    reliability='adequate' if subjects and all(s['reliability']=='adequate' for s in subjects) else 'insufficient',
                    recommendation=recommendation, subjects=subjects, body_results=bodies,
                    body_candidates=[{key: body[key] for key in ('box','source','confidence','subject_id','associated_face','association') if key in body} for body in bodies],
                    visible_boxes=[{'kind':'body','box':list(body['box'])} for body in bodies],
                    eye_evidence=any(item.get('eye_evidence') for item in face_subjects))
    if len(subjects) > 1:
        evidence['participants'] = subjects
    else:
        evidence.pop('participants',None)
    if bodies:
        evidence['reasons'] = list(dict.fromkeys(reason for subject in subjects for reason in subject.get('reasons', [])))
    if not subjects:
        evidence['reasons'] = list(evidence.get('reasons', [])) or [face_result.reason or 'no_reliable_subject']
    reason = face_result.reason
    if bodies and not face_subjects:
        reason = {'keep':'body_detail_clear_eyes_unassessed','reject':'obvious_body_blur','review':'body_focus_uncertain'}[recommendation]
    elif bodies and recommendation=='review':
        reason='subject_association_or_quality_needs_review'
    elif len(subjects) > 1 and recommendation == 'review':
        reason = 'participant_quality_needs_review'
    return replace(face_result, rejected=recommendation=='reject', reason=reason,
                   face_found=bool(face_subjects), focus_evidence=evidence, analysis_version=VERSION)


def _landscape_detail_support(bgr: np.ndarray, measured: dict[str, Any]) -> dict[str, Any]:
    """Veto clear tiles whose apparent fine detail is mostly estimated noise.

    A separable second-difference kernel has unit squared energy. Its absolute
    median, divided by the Gaussian MAD constant, estimates grayscale noise
    sigma while ignoring sparse edges. Compare the expected noise contribution
    through the *same* fine-detail filter with the measured fine-detail power.
    Require more residual signal power than noise power (estimated SNR > 1).

    This is a conservative support gate, not a calibrated noise measurement or
    blur diagnosis. Dense stochastic texture may inflate the estimate; spatially
    correlated, compressed or structured noise can evade it. Never use this
    estimate to establish a reject, or to promote an originally non-clear tile.
    """
    if bgr.ndim != 3 or bgr.shape[2] != 3 or min(bgr.shape[:2]) < 32:
        return {'state':'uncertain', 'reason':'noise_support_unavailable'}
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    difference = np.array([1.0, -2.0, 1.0], dtype=np.float32)
    kernel = np.outer(difference, difference) / 6.0
    residual = cv2.filter2D(gray, cv2.CV_32F, kernel)
    # Exclude reflected boundaries from the noise estimate.
    sigma = float(np.median(np.abs(residual[2:-2, 2:-2])) / 0.67448975)
    contrast = float(measured['contrast'])
    fine_power = float(measured['laplacian_normalized']) * max(contrast ** 2, 0.0025)
    if not np.isfinite((sigma, contrast, fine_power)).all() or fine_power <= 0:
        return {'state':'uncertain', 'reason':'noise_support_unavailable'}
    noise_power = _LANDSCAPE_FINE_NOISE_GAIN * (sigma / 255.0) ** 2
    fraction = noise_power / fine_power
    supported = fraction < 0.5
    return {'state':'clear' if supported else 'uncertain',
            'estimated_noise_sigma':sigma, 'estimated_noise_fraction':fraction,
            'reason':'detail_energy_exceeds_noise_floor' if supported else
                     'fine_detail_may_be_noise_dominated'}


def landscape_evidence(image: Image.Image) -> dict[str, Any]:
    """Whole-frame checks with texture gating and no visible selection boxes.

    Low-detail sky/water and intentional defocus cannot establish failure.
    Until calibrated landscape-negative evidence exists, ambiguous softness is
    review-only. Several supported sharp tiles suffice to retain shallow-DoF
    images without requiring every tile to be sharp.
    """
    from .body_focus import _region_metrics
    rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
    height, width = rgb.shape[:2]
    checks = []
    for row in range(4):
        for column in range(4):
            x0, x1 = round(column*width/4), round((column+1)*width/4)
            y0, y1 = round(row*height/4), round((row+1)*height/4)
            tile = rgb[y0:y1, x0:x1]
            if min(tile.shape[:2]) < 32:
                continue
            # Bound working memory and compare at native scale: crop the centre
            # instead of resizing/upscaling a low-resolution region into detail.
            th, tw = tile.shape[:2]
            tile = tile[max(0,(th-384)//2):min(th,(th+384)//2), max(0,(tw-384)//2):min(tw,(tw+384)//2)]
            bgr = cv2.cvtColor(tile, cv2.COLOR_RGB2BGR)
            th, tw = bgr.shape[:2]
            measured = _region_metrics(bgr, np.full((th,tw),255,np.uint8), (0,0,tw,th))
            if measured['state'] == 'clear':
                measured['detail_support'] = _landscape_detail_support(bgr, measured)
            checks.append(dict(measured, tile=[column,row], native_size=[tw,th]))
    clear = [item for item in checks if item['state']=='clear']
    textured = [item for item in checks if item['state']!='insufficient']
    supported_clear = [item for item in clear if item['detail_support']['state'] == 'clear']
    enough = len(supported_clear) >= 2
    reasons = (['supported_detail_in_textured_regions'] if enough else
               ['landscape_detail_may_be_noise_dominated'] if len(clear) >= 2 else
               ['low_texture_scene_is_not_a_quality_failure'] if not textured else
               ['landscape_focus_needs_review'])
    return {'version':LANDSCAPE_VERSION, 'mode':'landscape', 'state':'clear' if enough else 'uncertain',
            'reliability':'adequate' if len(textured) >= 2 else 'insufficient',
            'recommendation':'keep' if enough else 'review', 'reasons':reasons,
            'subjects':[], 'visible_boxes':[], 'eye_evidence':False,
            'regions':checks, 'source_size':[width,height],
            # Keep the original clear count as a diagnostic, distinct from the
            # noise-supported count that now controls the keep recommendation.
            'textured_regions':len(textured), 'clear_regions':len(clear),
            'supported_clear_regions':len(supported_clear)}


def _assess_asset_mode(asset, *, mode='portrait', crop_settings=None, cache_dir=None, body_check=False):
    mode = normalize_mode(mode)
    settings = crop_settings or CropSettings()
    from .shared_decode import full_image
    if mode == 'landscape':
        asset.person_review_pending = False
        try:
            with full_image(asset) as image:
                evidence = landscape_evidence(image)
                decode = image.info.get('decode_evidence')
                if decode:
                    evidence['decode'] = dict(decode)
            return ScreeningResult(False, 'landscape_detail_clear' if evidence['state']=='clear' else 'landscape_needs_review',
                                   False, analysis_version=LANDSCAPE_VERSION, source_size=tuple(evidence['source_size']), focus_evidence=evidence)
        except (OSError, ValueError, cv2.error):
            return ScreeningResult(False, 'source_unreadable_for_analysis', False, analysis_version=LANDSCAPE_VERSION,
                                   focus_evidence={'version':LANDSCAPE_VERSION, 'mode':mode,'state':'uncertain','reliability':'insufficient',
                                                   'recommendation':'review','subjects':[],'visible_boxes':[],
                                                   'reasons':['source_unreadable_for_analysis']})
    from .face_focus import assess_asset_focus
    from .subject import body_candidates
    entry = settings.photos.get(settings.key(asset), {})
    body_only = 'selected_bodies' in entry and 'selected_faces' not in entry and not entry.get('manual_face')
    face = (ScreeningResult(False, 'body_subject_selected', False) if body_only else
            assess_asset_focus(asset, crop_settings=settings, cache_dir=cache_dir))
    # Multi-person selection must remain explicit; no fallback may undo an
    # unresolved participant warning or a deliberately hidden selection.
    if entry.get('hidden'):
        return _portrait_result(face)
    if face.reason == 'person_selection_pending':
        prior = getattr(asset, 'clarity_evidence', None) or {}
        if prior.get('selection_pending') and prior.get('body_candidates'):
            bodies = [item for item in prior.get('subjects', []) if item.get('evidence_kind') == 'body']
            result = _portrait_result(face, bodies=bodies, include_face=False)
            result.rejected = False
            result.focus_evidence.update(state='uncertain',recommendation='review',selection_pending=True)
            return result
        return _portrait_result(face)
    face_evidence = face.focus_evidence or {}
    face_people = face_evidence.get('participants') or [face_evidence]
    reliable_face = face.face_found and all(item.get('landmarks_used') for item in face_people)
    bodies = []
    if not reliable_face or 'selected_bodies' in entry:
        candidates = body_candidates(asset, settings)
        if candidates:
            from .body_focus import assess_body_region, detect_body_candidates
            try:
                body_decode = dict(evidence_regions=[candidate['box'] for candidate in candidates], min_region_pixels=96) if asset.raw_path else {}
                with full_image(asset, **body_decode) as image:
                    if 'selected_bodies' in entry:
                        bodies = [assess_body_region(image, tuple(candidate['box'])) for candidate in candidates]
                    else:
                        bodies = detect_body_candidates(image)
                        if not bodies:
                            bodies = [dict(candidate,state='uncertain',reasons=['body_pose_unreliable_at_analysis_resolution']) for candidate in candidates]
            except (OSError, ValueError, cv2.error):
                bodies = [dict(candidate,state='uncertain',reasons=['body_source_unreadable']) for candidate in candidates]
    if reliable_face and body_check and 'selected_bodies' not in entry:
        from .body_pipeline import apply_body_check
        face = apply_body_check(asset, face, settings, cache_dir)
    # Explicit mixed subjects retain their own face issue; automatic fallback
    # replaces only unavailable facial evidence and identifies that limitation.
    result = _portrait_result(face, bodies=bodies, include_face=face.face_found)
    if bodies and not reliable_face:
        result.focus_evidence['face_evidence'] = face_evidence
        result.focus_evidence['eyes_unassessed'] = not result.focus_evidence.get('eye_evidence', False)
    if len(bodies) > 1 and 'selected_bodies' not in entry:
        result.rejected = False
        result.reason = 'body_subject_selection_pending'
        result.focus_evidence.update(state='uncertain', recommendation='review', selection_pending=True)
        asset.person_review_pending = True
    elif 'selected_bodies' in entry:
        asset.person_review_pending = False
    return result


def assess_asset_mode(asset, *, mode='portrait', crop_settings=None, cache_dir=None, body_check=False):
    """Apply source-bound human/automatic policy to fresh portrait measurements."""
    from .primary_quality import (confirmed_selection, current_selection, evaluate_local_evidence,
                                  measurement_inputs, automatic_selection)
    settings = crop_settings or CropSettings()
    portrait = normalize_mode(mode) == 'portrait'
    before = confirmed_selection(asset, settings) if portrait else None
    entry = settings.photos.get(settings.key(asset), {}) if portrait else {}
    # Explicit body ROIs request body inspection even when the optional
    # face-associated body expansion checkbox is off.
    # Explicit [] disables body inspection; a nonempty manual list replaces
    # automatic expansion even when the global checkbox remains enabled.
    from .primary_quality import explicit_body_parts
    try:
        body_enabled = ('body' in explicit_body_parts(entry)) if 'selected_bodies' in entry else body_check
    except (TypeError, ValueError):
        body_enabled = False  # Invalid selection cannot confirm or become automatic.
    enabled_parts = ['face', 'body'] if body_enabled else ['face']
    try:
        inputs = measurement_inputs(asset, settings, enabled_parts) if portrait else None
    except (AttributeError, OSError, TypeError, ValueError):
        inputs = None
    result = _assess_asset_mode(asset, mode=mode, crop_settings=settings,
                                cache_dir=cache_dir, body_check=body_check)
    if normalize_mode(mode) == 'landscape':
        return result
    evidence = dict(result.focus_evidence or {})
    for key in ('measurement_inputs', 'automatic_selection', 'measurement_selection_digest',
                'measurement_confirmation_digest', 'enabled_parts', 'primary_quality'):
        evidence.pop(key, None)
    after = confirmed_selection(asset, settings)
    try:
        inputs_current = inputs is not None and inputs == measurement_inputs(asset, settings, enabled_parts)
    except (AttributeError, OSError, TypeError, ValueError):
        inputs_current = False
    if inputs_current:
        evidence['measurement_inputs'] = inputs
        evidence['enabled_parts'] = enabled_parts
        try:
            automatic = automatic_selection(asset, settings, evidence, inputs)
        except (KeyError, AttributeError, OSError, TypeError, ValueError):
            automatic = None
        if automatic is not None:
            evidence['automatic_selection'] = automatic
            evidence['measurement_selection_digest'] = automatic['digest']
    # Never assign the current confirmation to historical evidence.
    if inputs_current and before is not None and after is not None and before['digest'] == after['digest']:
        evidence['measurement_confirmation_digest'] = before['digest']
    policy = evaluate_local_evidence(asset, settings, evidence)
    evidence['primary_quality'] = policy
    evidence.update(state=policy['state'], recommendation=policy['recommendation'],
                    reliability='adequate' if policy['recommendation'] != 'review' else 'insufficient')
    from copy import copy
    from .focus_protocol import make_scope, FocusProtocolError
    scoped = copy(asset)
    scoped.clarity_evidence = evidence
    try:
        asset.focus_scope = make_scope(scoped, settings)
    except (FocusProtocolError, OSError, TypeError, ValueError):
        asset.focus_scope = None
    selection = current_selection(asset, settings, evidence)
    asset.person_review_pending = selection is None
    prefix = 'confirmed' if after is not None else 'automatic'
    reason = ('primary_selection_unconfirmed' if selection is None else
              {'reject':prefix+'_primary_severe_blur', 'keep':prefix+'_primary_clear',
               'review':prefix+'_primary_quality_uncertain'}[policy['recommendation']])
    return replace(result, focus_evidence=evidence, rejected=policy['recommendation']=='reject', reason=reason)


def recommend_group(assets) -> dict[str, dict[str, Any]]:
    """Tied local recommendations from quality categories, without star scores.

    Non-winners do not acquire a reject flag. This helper never changes manual
    or AI decisions, and has no image decode or paid-service side effects.
    """
    selected = []
    for asset in assets:
        evidence = getattr(asset, 'clarity_evidence', None) or {}
        if evidence.get('recommendation') == 'keep' and evidence.get('reliability') == 'adequate':
            selected.append(asset)
    selected_ids = {id(asset) for asset in selected}
    return {str(asset.primary_path): {'recommended':id(asset) in selected_ids,
                                    'tied':len(selected)>1 and id(asset) in selected_ids,
                                    'recommendation':((getattr(asset,'clarity_evidence',None) or {}).get('recommendation','review')
                                                      if (getattr(asset,'clarity_evidence',None) or {}).get('reliability') == 'adequate' else 'review')}
            for asset in assets}
