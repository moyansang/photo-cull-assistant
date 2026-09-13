"""Lightroom catalog interchange. Never writes image metadata or sidecars."""


# None means no current technical assessment: leave Lightroom's keyword alone.
def _value(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def focus_review_status(asset):
    rejected=bool(_value(asset,'auto_rejected',_value(asset,'technical_rejected',False)))
    reason=_value(asset,'screening_reason','') or _value(asset,'technical_reason','')
    if rejected or reason in {'subject_not_obviously_blurred', 'ai_focus_clear', 'ai_focus_blur'}:
        return False
    if reason in {
        'ai_focus_uncertain', 'face_focus_uncertain', 'body_focus_uncertain', 'face_too_small_for_focus', 'no_reliable_face',
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
    if isinstance(concern,dict) and isinstance(concern.get('reason'),str) and concern['reason'].strip():
        return concern['reason'].strip()
    focus=_value(photo,'ai_focus_result')
    if isinstance(focus,dict) and focus.get('status')=='uncertain' and isinstance(focus.get('reason'),str):
        if focus['reason'].strip():return focus['reason'].strip()
    evidence=_value(photo,'clarity_evidence')
    codes=evidence.get('reasons',[]) if isinstance(evidence,dict) else []
    evidence_labels={
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
