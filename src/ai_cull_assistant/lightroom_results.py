"""Lightroom catalog interchange. Never writes image metadata or sidecars."""


# None means no current technical assessment: leave Lightroom's keyword alone.
def _value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def focus_review_status(asset):
    if _value(asset, 'person_review_pending', False):return True
    rejected=bool(_value(asset,'auto_rejected',_value(asset,'technical_rejected',False)))
    reason=_value(asset,'screening_reason','') or _value(asset,'technical_reason','')
    if rejected or reason in {'subject_not_obviously_blurred', 'ai_focus_clear', 'ai_focus_blur'}:
        return False
    if reason in {
        'person_selection_pending', 'ai_focus_uncertain', 'face_focus_uncertain', 'body_focus_uncertain', 'face_too_small_for_focus', 'no_reliable_face', 'head_only_localized',
        'source_preview_geometry_mismatch', 'source_unreadable_for_focus',
        'preview_unreadable', 'preview_unavailable',
    }:
        return True
    if _value(asset,'clarity_version')=='clarity-v2':
        evidence=_value(asset,'clarity_evidence')
        state=evidence.get('state',evidence.get('status')) if isinstance(evidence,dict) else None
        return False if state in {'clear','severe_blur','blur'} else True
    return None


def focus_review_reason(photo):
    """Return a readable reason for the Lightroom pending collection."""
    concern=_value(photo,'selection_focus_concern')
    if (isinstance(concern,dict) and concern.get('fingerprint')==_value(photo,'fingerprint')
            and isinstance(concern.get('reason'),str) and concern['reason'].strip()):
        return concern['reason'].strip()
    focus=current_focus(photo)
    if isinstance(focus,dict) and focus.get('status')=='uncertain' and isinstance(focus.get('reason'),str):
        if focus['reason'].strip():return focus['reason'].strip()
    evidence=_value(photo,'clarity_evidence')
    codes=evidence.get('reasons',[]) if isinstance(evidence,dict) else []
    evidence_labels={
        'head_without_visible_face':'已定位头部，但没有可靠的可见人脸证据，需检查原图清晰度。',
        'missing_reliable_landmarks':'没有可靠的眼部关键点，无法准确检查眼部细节。',
        'insufficient_native_eye_pixels':'原图中的双眼像素不足，无法可靠判断。',
        'insufficient_native_face_pixels':'原图中的人脸像素不足，无法可靠判断。',
        'insufficient_local_contrast':'人脸局部对比度不足，清晰度证据不充分。',
        'high_frequency_noise_or_artifacts':'噪点或压缩痕迹可能干扰清晰度判断。',
        'possible_directional_smear':'检测到疑似方向性拖影，需要检查原图。',
        'weaker_than_comparable_burst':'相比同组选片中姿态、位置接近的照片，主体细节明显偏弱。',
        'borderline_focus_evidence':'本地多项清晰度指标处于边界，证据不足。',
    }
    details=[evidence_labels[code] for code in codes if code in evidence_labels]
    if details:return ' '.join(dict.fromkeys(details))
    reason=_value(photo,'screening_reason','') or _value(photo,'technical_reason','')
    labels={
        'person_selection_pending':'检测到多个人脸，请先确认合影主体并重新扫描。',
        'ai_focus_uncertain':'AI 清晰度复核仍无法确定。',
        'face_focus_uncertain':'本地人脸清晰度证据不足或相互冲突。',
        'body_focus_uncertain':'身体清晰度实验检查无法确认主体主要部位清晰，请检查原图。',
        'face_too_small_for_focus':'主体人脸像素不足，无法可靠判断清晰度。',
        'no_reliable_face':'未检测到可靠的主体人脸。',
        'source_preview_geometry_mismatch':'原图与预览方向或比例不一致，无法可靠定位主体细节。',
        'source_unreadable_for_focus':'无法读取原图进行清晰度检查。',
        'preview_unreadable':'无法读取预览图进行清晰度检查。',
        'preview_unavailable':'缺少可用预览图，无法检查清晰度。',
        'screening_disabled':'本次未执行本地清晰度检查。',
    }
    return labels.get(reason,'清晰度证据不足，需要检查原图。')


def ai_metadata(selection=None, focus=None, pending=None, technical_reason='', pending_reason=''):
    """Human-readable plugin fields; callers pass only current valid results."""
    fields = {}
    if selection is not None:
        fields['selection_reason'] = selection.get('reason', '')
        fields['review_items'] = '\n'.join(selection.get('review_items', []))
    status = {'clear': '清晰', 'blur': '模糊', 'uncertain': '清晰度待确认'}
    if pending:
        fields.update(clarity_status='清晰度待确认', clarity_reason=pending_reason or focus_review_reason({}))
    elif isinstance(focus, dict) and focus.get('status') in status:
        fields.update(clarity_status=status[focus['status']], clarity_reason=focus.get('reason', ''))
    else:
        fields.update(clarity_status='清晰度待确认' if pending else ('本地初筛弃置' if technical_reason else ''),
                      clarity_reason=('本地检测判定主体明显模糊或抖动，未提供 AI 核查理由。' if technical_reason else ''))
    return fields



def current_selection(photo):
    """Return only the current group-dependent suggestion; keep history untouched."""
    value = _value(photo, 'ai')
    if (isinstance(value, dict) and not _value(photo, 'stale', False)
            and value.get('fingerprint') == _value(photo, 'fingerprint')
            and not value.get('stale', False)):
        return value
    return None


def current_focus(photo):
    """Technical AI depends on source/subject, never group membership."""
    value = _value(photo, 'ai_focus_result')
    if not isinstance(value, dict) or value.get('status') not in {'clear', 'blur', 'uncertain'}:
        return None
    if _value(photo, 'focus_stale', False) or _value(photo, 'ai_focus_dirty', False) or _value(photo, 'ai_focus_stale', False):
        return None
    expected = _value(photo, 'technical_fingerprint', _value(photo, 'fingerprint'))
    actual = _value(photo, 'focus_fingerprint', value.get('technical_fingerprint'))
    if expected is not None and actual != expected:
        return None
    return value


def local_recommendation(photo):
    """Return a recommendation, never convert technical scores into stars."""
    evidence = _value(photo, 'clarity_evidence') or {}
    reason = _value(photo, 'screening_reason', '') or _value(photo, 'technical_reason', '')
    if _value(photo, 'person_review_pending', False):
        return 'review'
    recommendation = evidence.get('recommendation') if isinstance(evidence, dict) else None
    if recommendation in {'keep', 'reject', 'review'}:
        return recommendation
    state = evidence.get('state', evidence.get('status')) if isinstance(evidence, dict) else None
    if state in {'uncertain', 'insufficient', 'unavailable'}:
        return 'review'
    if state in {'severe_blur', 'blur'}:
        return 'reject'
    if state == 'clear':
        return 'keep'
    # AI application in legacy sessions also populated these local fields. An
    # expired AI value must never come back through the local fallback.
    if str(reason).startswith('ai_focus_'):
        return 'review'
    if _value(photo, 'auto_rejected', _value(photo, 'technical_rejected', False)):
        return 'reject'
    return 'keep' if reason == 'subject_not_obviously_blurred' else 'review'


def effective_decision(photo):
    """One UI/export policy: explicit manual > current AI > local.

    A confirmed manual decision is durable across crop/group recomputation.
    None means leave the corresponding Lightroom field untouched. Pending
    technical evidence remains visible, but cannot silently erase a manual or
    valid selection star. No local path creates a rating.
    """
    selection = current_selection(photo)
    focus = current_focus(photo)
    final = _value(photo, 'final') or {}
    pending = _value(photo, 'focus_review') is True
    if _value(photo,'focus_stale',False) or _value(photo,'ai_focus_stale',False):
        # Old focus_review may be the expired AI verdict. Fall back to current
        # local evidence, retaining only a current selection's review concern.
        concern=_value(photo,'selection_focus_concern') or {}
        concern_current=(selection is not None and
                         concern.get('fingerprint')==_value(photo,'fingerprint'))
        pending=local_recommendation(photo)=='review' or concern_current
    if final.get('confirmed'):
        rating = final.get('rating')
        pick = final.get('pick_status')
        recommendation = 'reject' if pick == -1 else 'keep' if pick in (0, 1) else 'review'
        source, reason = 'manual', '人工确认结果'
    elif selection is not None:
        rating = selection.get('rating')
        # An inability to score is a review result, not an implicit keep.
        recommendation = ('reject' if selection.get('suggest_reject') is True
                          else 'review' if rating is None else 'keep')
        pick = {'reject': -1, 'keep': 1}.get(recommendation)
        source, reason = 'ai', selection.get('reason', '')
    elif focus is not None:
        rating = None
        recommendation = {'blur': 'reject', 'clear': 'keep', 'uncertain': 'review'}[focus['status']]
        # Mixed multi-person evidence is never an automatic whole-photo reject.
        people = focus.get('participants') or []
        if len(people) > 1 and any(p.get('status') != 'clear' for p in people):
            recommendation = 'review'
        pick = {'reject': -1, 'keep': 1}.get(recommendation)
        source, reason = 'ai', focus.get('reason', '')
    else:
        rating = None
        recommendation = local_recommendation(photo)
        pick = {'reject': -1, 'keep': 1}.get(recommendation)
        source, reason = 'local', _value(photo, 'screening_reason', '') or ''
    if type(rating) is not int or not 1 <= rating <= 5:
        rating = None
    if type(pick) is not int or pick not in (-1, 0, 1):
        pick = None
    return dict(source=source, rating=rating, pick_status=pick,
                recommendation=recommendation, reason=reason,
                focus_review=pending or (recommendation == 'review' and source != 'manual'))
