"""Shared participant confirmation and readable per-person evidence."""

def needs_person_review(asset, entry):
    if entry.get('hidden') or entry.get('manual_face') or 'selected_faces' in entry:
        return False
    return (getattr(getattr(asset, 'subject_features', None), 'candidate_count', None) or 0) > 1


def participant_labels(asset, entry, saved_entry, count):
    if needs_person_review(asset, entry):
        return ['人物待确认：请选中所有合影主体，排除背景路人。']
    changed = any(entry.get(k) != saved_entry.get(k) for k in ('selected_faces', 'manual_face', 'hidden'))
    if changed or getattr(asset, 'ai_focus_dirty', False):
        return [f'人物 {i}：待重新扫描' for i in range(1, count + 1)]
    ai = getattr(asset, 'ai_focus_result', None) or {}
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
