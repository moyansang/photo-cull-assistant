"""Lightroom catalog interchange. Never writes image metadata or sidecars."""


# None means no current technical assessment: leave Lightroom's keyword alone.
def focus_review_status(asset):
    if asset.auto_rejected or asset.screening_reason in {'subject_not_obviously_blurred', 'ai_focus_clear', 'ai_focus_blur'}:
        return False
    if asset.screening_reason in {
        'ai_focus_uncertain', 'face_focus_uncertain', 'face_too_small_for_focus', 'no_reliable_face',
        'source_preview_geometry_mismatch', 'source_unreadable_for_focus',
        'preview_unreadable', 'preview_unavailable',
    }:
        return True
    return None


def ai_metadata(selection=None, focus=None, pending=None, technical_reason=''):
    """Human-readable plugin fields; callers pass only current valid results."""
    fields = {}
    if selection is not None:
        fields['selection_reason'] = selection.get('reason', '')
        fields['review_items'] = '\n'.join(selection.get('review_items', []))
    status = {'clear': '清晰', 'blur': '模糊', 'uncertain': '清晰度待确认'}
    if isinstance(focus, dict) and focus.get('status') in status:
        fields.update(clarity_status=status[focus['status']], clarity_reason=focus.get('reason', ''))
    else:
        fields.update(clarity_status='清晰度待确认' if pending else ('本地初筛弃置' if technical_reason else ''),
                      clarity_reason=('本地检测判定主体明显模糊或抖动，未提供 AI 核查理由。' if technical_reason else ''))
    return fields
