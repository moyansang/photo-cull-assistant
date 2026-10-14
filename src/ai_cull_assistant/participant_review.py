"""Shared participant confirmation and readable per-person evidence."""

def needs_person_review(asset, entry):
    if getattr(asset, 'analysis_mode', 'portrait') == 'landscape':
        return False
    if entry.get('hidden') or entry.get('manual_face') or 'selected_faces' in entry or 'selected_bodies' in entry:
        return False
    local = getattr(asset, 'clarity_evidence', None) or {}
    return ((getattr(getattr(asset, 'subject_features', None), 'candidate_count', None) or 0) > 1
            or bool(local.get('selection_pending') and len(local.get('body_candidates', [])) > 1))


def participant_labels(asset, entry, saved_entry, count):
    if needs_person_review(asset, entry):
        return ['人物待确认：请选中所有合影主体，排除背景路人。']
    changed = any(entry.get(k) != saved_entry.get(k) for k in ('selected_faces', 'manual_face', 'selected_bodies', 'hidden'))
    if changed or getattr(asset, 'ai_focus_dirty', False):
        return [f'人物 {i}：待重新扫描' for i in range(1, count + 1)]
    ai = {} if (getattr(asset, 'ai_focus_stale', False) or getattr(asset, 'focus_stale', False)) else (getattr(asset, 'ai_focus_result', None) or {})
    local = getattr(asset, 'clarity_evidence', None) or {}
    values = ai.get('participants') if ai.get('participants') else local.get('participants')
    source = 'AI' if ai.get('participants') else '本地'
    if not values and count == 1:
        values = [ai if ai.get('status') else local]
        source = 'AI' if ai.get('status') else '本地'
    labels = {'clear': '清晰', 'blur': '模糊', 'severe_blur': '明显模糊', 'uncertain': '待确认'}
    rows = {item.get('participant', 1): item for item in (values or []) if isinstance(item, dict)}
    return [f"人物 {i}：{labels.get(rows.get(i, {}).get('status', rows.get(i, {}).get('state')), '暂无结果')}（{source}）"
            for i in range(1, count + 1)]


def body_participant_labels(asset, entry, saved_entry):
    """Use current, geometry-matched AI body evidence before local evidence."""
    from .body_subject_dialog import visible_body_boxes
    from .crop_settings import face_box_key
    from .lightroom_results import current_focus
    boxes = visible_body_boxes(asset, entry)
    if not boxes:
        return []
    changed = any(entry.get(key) != saved_entry.get(key)
                  for key in ('selected_bodies','selected_faces','manual_face','hidden'))
    if changed or getattr(asset, 'ai_focus_dirty', False):
        return [f'身体 {index}：待重新扫描（眼部未评估）' for index in range(1,len(boxes)+1)]
    local = getattr(asset, 'clarity_evidence', None) or {}
    bodies = local.get('body_results') or [item for item in local.get('subjects', []) if item.get('evidence_kind') == 'body']
    ai = current_focus(asset) or {}
    ai_bodies = ai.get('participants') or [ai]
    def matching(values, box):
        identity = 'body:'+face_box_key(box)
        for item in values:
            if item.get('evidence_kind') != 'body':
                continue
            found = item.get('body_box', item.get('box'))
            if item.get('subject_id') == identity or (found is not None and face_box_key(found)==face_box_key(box)):
                return item
        return None
    labels = {'clear':'身体细节清晰', 'blur':'身体明显模糊', 'severe_blur':'身体明显模糊', 'uncertain':'身体待复核'}
    rows = []
    for index,box in enumerate(boxes,1):
        value = matching(ai_bodies,box)
        source = 'AI' if value is not None else '本地'
        value = value if value is not None else (matching(bodies,box) or {})
        state = value.get('status',value.get('state'))
        associated = value.get('associated_face')
        link = f'，对应人物 {associated}' if associated else ''
        rows.append(f"身体 {index}：{labels.get(state,'暂无结果')}（{source}{link}；眼部未评估）")
    return rows
