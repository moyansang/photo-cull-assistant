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
    if job and getattr(job, 'assets', None) is not None:
        assets = job.assets
    if assets is None:
        lines.append('扫描图片：尚无可恢复的扫描结果。')
        return lines
    lines.append(f'扫描图片：已恢复 {len(assets)} 张照片的分析记录。')
    counts = focus_counts(assets)
    reviewed, pending, uncertain, dirty = (counts[k] for k in ('reviewed', 'pending', 'uncertain', 'dirty'))
    lines.append(f"AI 复核未完成原因：待提交 {pending} 张；请求失败 {counts['failed']} 张；修改后失效 {dirty} 张；主动跳过 {counts['skipped']} 张。")
    pending += counts['failed'] + counts['skipped']
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
        from collections import Counter
        states = Counter(str(b.get('status', 'pending')).lower() for b in batches)
        lines.append(f"AI 选片未完成原因：待提交 {states['pending']} 批；请求失败 {states['failed']} 批；部分返回 {states['partial']} 批；请求中断/进行中 {states['running']} 批；主动跳过 {states['skipped']} 批。")
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


def focus_counts(assets):
    counts = dict(reviewed=0, pending=0, uncertain=0, dirty=0, failed=0, skipped=0)
    for asset in assets or []:
        if getattr(asset, 'ai_focus_dirty', False):
            counts['dirty'] += 1
            continue
        value = getattr(asset, 'ai_focus_result', None)
        if (isinstance(value, dict) and value.get('status') in {'clear', 'blur', 'uncertain'}
                and isinstance(value.get('reason'), str) and value['reason'].strip()):
            counts['reviewed'] += 1
            counts['uncertain'] += value['status'] == 'uncertain'
        elif focus_review_status(asset) is True:
            state = getattr(asset, 'ai_focus_attempt', None)
            counts[state if state in {'failed', 'skipped'} else 'pending'] += 1
    return counts


def recommended_next_step(workspace, assets, sheets_ready, job=None):
    if job:
        mode = getattr(job, 'mode', None) or getattr(job, 'kind', 'scan')
        label = {'scan': '扫描图片', 'rescan': '重新扫描修改过的图片', 'regenerate': '重新扫描修改过的图片',
                 'focus': 'AI 复核', 'sheets': '生成联系表'}.get(mode, '任务')
        return f'继续{label}（点击“继续处理”）'
    if assets is None:
        return '扫描图片'
    if any(getattr(a, 'person_review_pending', False) for a in assets):
        return '检测/调整人脸框 → 确认合影主体'
    counts = focus_counts(assets)
    if counts['dirty']:
        return '检测/调整人脸框 → 重新扫描修改过的图片'
    if counts['pending'] or counts['failed']:
        return '继续 AI 复核' if counts['reviewed'] or counts['failed'] else 'AI 复核'
    if not sheets_ready:
        return '生成联系表'
    path = workspace / 'ai_project.json'
    if not path.exists():
        return 'AI 选片与导出 → 开始 AI 选片'
    try:
        data = json.loads(path.read_text('utf-8'))
        tasks = data['tasks']
        task = next((t for t in tasks if t['id'] == data.get('current_task_id')), tasks[-1] if tasks else None)
        if any(p.get('stale') for p in data.get('photos', {}).values()):
            return 'AI 选片与导出 → 重新提交失效结果'
        batches = task['batches'] if task else []
        if batches and any(str(b.get('status')).lower() not in {'complete', 'completed', 'done', '已完成'} for b in batches):
            return 'AI 选片与导出 → 继续 AI 选片'
        if not batches and not all(getattr(a, 'auto_rejected', False) or focus_review_status(a) is True for a in assets):
            return 'AI 选片与导出 → 开始 AI 选片'
        if not data.get('last_export_id') or data.get('export_dirty'):
            return 'AI 选片与导出 → 导出 LR'
        return '本工作区处理已完成；可在 Lightroom 应用导出结果'
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return 'AI 选片与导出 → 检查保存记录'
