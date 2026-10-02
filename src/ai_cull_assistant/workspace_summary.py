"""Read-only restoration summary; never creates tasks or changes saved results."""
import json

from .lightroom_results import focus_review_status


def restoration_summary(workspace, assets, settings, sheets_ready, job=None):
    on = lambda value: '开启' if value else '关闭'
    lines = [f"工作区恢复摘要：{workspace.name}",
             f"当前保存设置：分组灵敏度 {settings['grouping']}；每页 {settings['per_page']} 张；{settings['columns']} 列；"
             f"虚焦/抖动检查 {on(settings['screening'])}；身体清晰度检查（实验）{on(settings['body_screening'])}；"
             f"人脸检测置信度 {settings['confidence']:g}。",
             "设置为当前工作区保存值；已处理照片可能使用过不同设置。"]
    if job:
        mode = getattr(job, 'mode', None) or getattr(job, 'kind', 'scan')
        label = {'scan': '扫描图片', 'rescan': '重新扫描修改过的图片',
                 'regenerate': '重新扫描修改过的图片', 'focus': 'AI 清晰度复核',
                 'sheets': '生成联系表'}.get(mode, '处理')
        lines.append(f"未完成任务：{label}，进度约 {int(job.percent)}%；可点击“继续处理”。")
    if assets is None:
        lines.append('扫描图片：尚无可恢复的扫描结果。')
        return lines
    lines.append(f'扫描图片：已恢复 {len(assets)} 张照片的分析记录。')
    reviewed = pending = uncertain = dirty = 0
    for asset in assets:
        if getattr(asset, 'ai_focus_dirty', False):
            dirty += 1
            continue
        value = getattr(asset, 'ai_focus_result', None)
        valid = (isinstance(value, dict) and value.get('status') in {'clear', 'blur', 'uncertain'}
                 and isinstance(value.get('reason'), str) and bool(value['reason'].strip()))
        if valid:
            reviewed += 1
            uncertain += value['status'] == 'uncertain'
        elif focus_review_status(asset) is True:
            pending += 1
    if pending or dirty:
        lines.append(f'AI 清晰度复核：未完成；已复核 {reviewed} 张，尚未取得有效结论 {pending} 张，修改后待重新处理 {dirty} 张。')
    elif reviewed:
        lines.append(f'AI 清晰度复核：已完成当前需复核照片，共 {reviewed} 张。')
    else:
        lines.append('AI 清晰度复核：未发现有效 AI 复核记录；当前没有检测到待复核照片。')
    if uncertain:
        lines.append(f'复核后仍为“清晰度待确认”：{uncertain} 张，不进入 AI 选片。')
    lines.append('联系表：' + ('已生成。' if sheets_ready else '未生成或需要重新生成。'))
    path = workspace / 'ai_project.json'
    if not path.exists():
        lines.extend(['AI 选片：尚未创建任务。', 'LR 结果：未发现导出记录。'])
        return lines
    try:
        data = json.loads(path.read_text('utf-8'))
        tasks = data['tasks']
        task = next((t for t in tasks if t['id'] == data.get('current_task_id')), tasks[-1] if tasks else None)
        batches = task['batches'] if task else []
        completed = sum(str(b.get('status')).lower() in {'complete', 'completed', 'done', '已完成'} for b in batches)
        stale = sum(bool(p.get('stale')) for p in data.get('photos', {}).values())
        if not batches:
            lines.append('AI 选片：尚无可提交批次。')
        else:
            state = '当前任务已完成' if completed == len(batches) else '当前任务未完成'
            lines.append(f'AI 选片：{state}，已完成 {completed}/{len(batches)} 批；未完成批次包含待提交、失败或部分返回的批次。'
                         if completed != len(batches) else f'AI 选片：{state}，{completed}/{len(batches)} 批。')
        if stale:
            lines.append(f'AI 选片：{stale} 张旧结果已失效，需要重新选片。')
        exported = bool(data.get('last_export_id'))
        lines.append('LR 结果：' + ('已有导出记录，但结果已变化，需重新导出。' if data.get('export_dirty') and exported
                     else '已导出；是否已在 Lightroom 应用需在 LR 中确认。' if exported
                     else '未导出。'))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        lines.append('AI 选片及 LR 导出：保存记录无法读取，暂不能确认进度。')
    return lines
