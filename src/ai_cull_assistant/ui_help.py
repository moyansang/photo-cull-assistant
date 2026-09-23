"""Shared compact page navigation, contextual help and non-blocking tooltips."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, font

from .window_layout import fit_window


PAGE_HELP = {
    'home': ('主页面', '扫描图片只进行本地检测与分组。调整人脸和选片组后，进行 AI 复核，再生成联系表，最后进入 AI 选片与导出。\n\n停止与继续控制扫描和 AI 复核。清空日志仅清除界面文字；清空工作区会清除工作区结果，不删除原照片。\n\n身体清晰度检查是实验选项，可能增加待确认照片。AI 服务供复核和选片共用。'),
    'faces': ('检测／调整人脸框', '直接在照片上拖拽画框。蓝框为检测候选，点击可选择；已选人物为绿框，当前人物可用下拉框切换。点击裁切框后变黄，可上下左右拖动，滚轮微调范围。\n\n置信度全局生效；范围、偏移、比例按照片／人物保存。重置当前裁切不取消选脸。去除所有框选只清除当前照片已选框。\n\n补齐人脸使用当前人物作为参考，跨组寻找漏检候选。必须看图确认身份和位置，不保证所有姿态均可匹配。保存后仅重新扫描修改过的图片。'),
    'assist': ('补齐人脸', '参考人物来自外层当前选脸。软件在工作区的未标记照片中查找外观相似候选；相似分数不是身份概率。\n\n直接画框、点击框后拖动、滚轮缩放。确认后显示已确认；再次修改会撤销确认。不是这个人和跳过都不会采用本张。\n\n采用已确认候选写入外层编辑草稿，仍需外层保存。取消不写入草稿。已有人工框选不会被覆盖。'),
    'groups': ('编辑选片组', '选择组，再选择照片作为拆分起点。可与相邻组合并。人工分组修改立即保存。\n\n分组只决定照片比较范围，不改变人脸检测置信度。列表及缩略图区域可独立滚动。'),
    'review': ('AI 选片与导出', 'API 选片使用主页面服务；网页选片不需要 API，可合并多个批次上传，并导入整份回答。两种方式共用结果。\n\n开始／继续只提交未完成批次。重新提交使用当前偏好发起新评审，可能产生费用。暂停需等待当前请求结束。\n\n选片结果显示对应回复、星级与弃置状态。导出合并技术弃置、AI 结果及清晰度待确认，最终在 Lightroom 中复核。'),
    'api': ('API 配置', '选择预设后填写密钥，自定义服务在高级设置填写接口地址和模型。切换标签不会保存或关闭。\n\n密钥留空保留已保存密钥；配置文件不直接写入密钥。测试连接会请求服务，可能产生少量费用；测试成功后按现有流程保存并选用该服务。'),
    'raw': ('原始回答', '查看模型实际返回内容，不修改评分。文本区域可以独立滚动。'),
    'paste': ('导入模型回答', '粘贴整份回答并提交。软件校验任务、批次和照片编号，异常会给出提示；保留原始回答以便检查。'),
    'update': ('更新与版本', '检查 GitHub 正式版。不再自动检查更新仅关闭启动检查，仍可手动检查。\n\n更新下载复用主页面进度条；更新前需要关闭编辑窗口。用户设置和工作区不被程序文件覆盖。'),
    'lr': ('Lightroom 插件', '打开插件目录，在 Lightroom Classic 增效工具管理器中添加插件。\n\n从 AI 选片与导出页导出结果，再通过插件导入到当前目录。照片保留原位置，插件写入星级、弃置与分析信息，不主动写 XMP。Lightroom 自身的自动写 XMP 设置仍独立生效。'),
}

CONTROL_HELP = {
    '主页面': '返回主页面。编辑草稿未保存时会提示。快捷键 Alt+1。',
    '配置 API': '设置供 AI 清晰度复核和选片共用的服务。快捷键 Alt+2。',
    '检查更新': '查看版本、手动检查更新及自动更新偏好。快捷键 Alt+3。',
    'LR 插件': '查看插件安装方法并打开插件目录。快捷键 Alt+4。',
    '帮助': '打开当前页面操作说明。快捷键 F1。',
    '扫描图片': '进行本地人脸检测、清晰度检查与分组，不自动发送 API。',
    '编辑选片组': '查看、拆分及合并选片组，修改立即保存。',
    '检测/调整人脸框': '选择主体人脸，修正位置、范围和比例。',
    'AI 复核': '使用主页面 API 复核清晰度待确认照片。',
    '生成联系表': '根据当前分组、筛选与人脸设置生成联系表。',
    'AI 选片与导出': '通过 API 或网页提交选片，查看结果并导出到 Lightroom。',
    '停止处理': '请求停止，当前处理单元结束后保存进度。',
    '继续处理': '从已保存的中断位置继续，后续图片使用当前设置。',
    '清空工作区': '清空当前工作区的分析、联系表、AI 结果及导出，不删除原照片。',
    '清空日志': '仅清除窗口中显示的日志，原日志文件保留。',
    '打开联系表目录': '打开当前工作区生成的联系表目录。',
    '重新定位原照片': '原片移动或盘符改变时，重新匹配原照片，保留工作区结果。',
    '上一张': '查看上一张照片。', '下一张': '查看下一张照片。',
    '上一张未标记': '向前循环寻找还没有选脸的照片。',
    '下一张未标记': '向后循环寻找还没有选脸的照片。',
    '恢复本张自动选脸': '清除本张手动选择，重新采用检测结果。',
    '去除所有框选': '清除当前照片的全部已选人物和手动画框，其他照片不变。',
    '重置当前裁切': '恢复当前裁切范围、偏移和比例，不删除已选人脸。',
    '补齐人脸': '以当前人物为参考，跨组查找未标记照片，候选需逐张确认。',
    '确认此框': '确认当前候选；修改位置或范围后需要重新确认。',
    '不是这个人': '排除此候选，不作为所选人物采用。',
    '跳过': '不采用当前照片的候选框。',
    '采用选中候选': '将已确认候选合入外层人脸编辑草稿，之后需保存。',
    '采用已确认结果': '将已确认候选合入外层人脸编辑草稿，之后需保存。',
    '重新扫描修改过的图片': '保存人脸编辑，只重新检测修改过的照片。',
    '从所选照片拆分': '以所选照片为起点拆出新组。',
    '与上一组合并': '将当前组与上一组合并并保存。',
    '与下一组合并': '将当前组与下一组合并并保存。',
    '开始 / 继续未完成批次': '串行提交未完成批次，已完成结果保留。',
    '重新提交': '用当前选片偏好重新评审，可能产生 API 费用。',
    '完成当前批后暂停': '等待当前请求返回并保存后暂停，不提交下一批。',
    '拆分所选批次重试': '接口图片或大小限制时，将所选批次拆小后重试。',
    '精选照片再选一轮': '对符合条件的精选照片再进行跨组比较。',
    '全选未完成批次': '选择尚未完成的批次用于网页合并提交。',
    '全选': '选择当前列表全部项目。',
    '合并准备所选批次': '合并为一份完整提示词和一组联系表。',
    '复制完整提示词': '复制所选网页提交的完整提示词。',
    '打开全部联系表目录': '打开当前合并提交的所有联系表。',
    '导入整份回答': '粘贴并校验本次网页选片的完整回答。',
    '查看原始回答': '查看模型实际返回内容，不修改评分。',
    '查看所选批次原始回答': '查看当前批次保存的原始回复。',
    '导出到 LR': '合并 AI 星级、弃置、技术筛选和清晰度待确认，生成插件结果文件。',
    '打开原图': '使用系统关联程序打开所选原照片。',
    '测试连接': '测试服务的图片请求能力，可能产生少量服务费用。',
    '保存': '保存当前页面设置。', '关闭': '关闭当前窗口。',
    '取消': '关闭本次编辑，未提交的改动不应用。',
    '新建': '开始填写一个新的 API 配置。',
    '停止查找': '停止后续候选搜索，保留已经生成的候选。',
    '开始查找': '后台搜索工作区中未标记照片的相似人物候选。',
    '选择': '选择对应的文件夹。',
    '明显虚焦／严重抖动弃置': '本地检查给出明显模糊弃置标记，导入 Lightroom 后应用，不删除原照片。',
    '身体清晰度检查（实验）': '随本地扫描检查身体区域，可能增加清晰度待确认照片。',
    '不再自动检查更新': '关闭启动时自动检查，仍可手动检查更新。',
    '立即检查': '检查 GitHub 正式版；不自动安装，发现新版本后提示。',
    '打开插件目录': '打开随软件提供的 Lightroom 插件目录。',
}


FIELD_HELP = {
    '全局置信度': '人脸检测阈值，默认 0.80；降低可减少漏检，也可能增加错框。全局影响，与分组灵敏度无关。',
    '置信度': '人脸检测阈值；降低可减少漏检，也可能增加错框。',
    '当前人物': '选择当前调整的合影成员，其裁切参数独立保存；补齐人脸以此人为参考。',
    '裁切比例': '设置人脸细节小窗比例，只影响当前照片／人物的裁切。',
    '分组灵敏度': '调整照片自动分组的严格程度，不影响人脸检测。',
    '每页照片数': '生成联系表时每页容纳的照片数量。',
    '列数': '联系表每行照片列数。',
    'AI 服务': '供 AI 清晰度复核和 API 选片共用；网页选片无需 API。',
    '照片文件夹': '存放原始照片的文件夹，软件不会因弃置建议删除原照片。',
    '工作区': '保存扫描分析、联系表、AI 结果与日志的位置。',
    'API 密钥': '填写服务商的 API Key；留空时保留当前配置已保存的密钥。',
    '服务预设': '自动填入服务地址和模型，自定义服务可在高级设置中填写。',
    '目标数量': '本轮选片的目标数量，可保留不限制。',
    '补充要求': '附加给模型的选片偏好，重新提交时按当前内容评审。',
}


class ToolTip:
    def __init__(self, widget, text, delay=600):
        self.widget, self.text, self.delay = widget, text, delay
        self.timer = self.popup = None
        widget.bind('<Enter>', self.schedule, add='+')
        for event in ('<Leave>', '<ButtonPress>', '<FocusOut>', '<Destroy>'):
            widget.bind(event, self.hide, add='+')

    def schedule(self, _event=None):
        self.hide()
        self.timer = self.widget.after(self.delay, self.show)

    def show(self):
        self.timer = None
        try:
            if not self.widget.winfo_viewable():
                return
            text = self.text() if callable(self.text) else self.text
            if isinstance(self.widget, ttk.Widget) and self.widget.instate(['disabled']):
                owner = self.widget.winfo_toplevel()
                app = _app_for(owner)
                if getattr(owner, '_preparing_task', False):
                    reason = '正在准备当前任务，请等待准备完成。'
                elif getattr(owner, '_api_active', False):
                    reason = '正在处理 API 请求，请等待或暂停当前请求。'
                elif app is not None and getattr(app, '_processing_busy', False):
                    reason = '正在处理照片，请等待任务结束或停止后再试。'
                elif app is not None and getattr(getattr(app, 'updates', None), 'busy', False):
                    reason = '正在检查或下载更新。'
                elif str(self.widget.cget('text')) == '停止处理':
                    reason = '当前没有正在运行的照片处理任务。'
                elif str(self.widget.cget('text')) == '继续处理':
                    reason = '当前没有可继续的中断任务。'
                else:
                    reason = '当前没有可操作的选择或任务，请先完成对应准备步骤。'
                text += '\n当前不可用：' + reason
            popup = self.popup = tk.Toplevel(self.widget)
            popup.overrideredirect(True)
            popup.attributes('-topmost', True)
            ttk.Label(popup, text=text, wraplength=340, padding=8, relief='solid').pack()
            popup.update_idletasks()
            from .window_layout import work_area_for
            area = work_area_for(self.widget)
            x = min(self.widget.winfo_rootx(), area.left + area.width - popup.winfo_reqwidth() - 8)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
            if y + popup.winfo_reqheight() > area.top + area.height:
                y = self.widget.winfo_rooty() - popup.winfo_reqheight() - 5
            popup.geometry(f'{max(area.left, x):+d}{max(area.top, y):+d}')
        except tk.TclError:
            self.hide()

    def hide(self, _event=None):
        try:
            if self.timer:
                self.widget.after_cancel(self.timer)
            if self.popup:
                self.popup.destroy()
        except tk.TclError:
            pass
        self.timer = self.popup = None


def install_control_help(container, page_id='home'):
    """Bind once, retaining normal command and keyboard behavior."""
    for child in container.winfo_children():
        if isinstance(child, tk.Toplevel):
            continue
        if not hasattr(child, '_control_tooltip'):
            text = ''
            if isinstance(child, (ttk.Button, ttk.Checkbutton, ttk.Radiobutton)):
                label = str(child.cget('text'))
                text = CONTROL_HELP.get(label, '')
                if not text and label:
                    text = f'{label}。更多使用方法见顶部帮助。'
            elif isinstance(child, (ttk.Entry, ttk.Spinbox, ttk.Combobox)):
                label = ''
                try:
                    info = child.grid_info()
                    siblings = child.master.winfo_children()
                    if info:
                        candidates = []
                        for sibling in siblings:
                            si = sibling.grid_info()
                            if isinstance(sibling, ttk.Label) and si and si.get('row') == info.get('row') and si.get('column', 0) < info.get('column', 0):
                                candidates.append((si['column'], str(sibling.cget('text'))))
                        if candidates:
                            label = max(candidates)[1]
                    else:
                        for sibling in siblings[:siblings.index(child)]:
                            if isinstance(sibling, ttk.Label) and sibling.cget('text'):
                                label = str(sibling.cget('text'))
                    text = FIELD_HELP.get(label, f'{label}：选择或填写当前设置；详细作用见帮助。' if label else '')
                except tk.TclError:
                    pass
            if text:
                child._control_tooltip = ToolTip(child, text)
        install_control_help(child, page_id)


def _app_for(window):
    node = window
    while node is not None:
        if callable(getattr(node, '_open_api_config', None)):
            return node
        node = getattr(node, 'master', None)
    return None


def show_page_help(window, page_id):
    old = getattr(window, '_page_help_window', None)
    if old is not None and old.winfo_exists():
        old.lift()
        return
    title, text = PAGE_HELP.get(page_id, PAGE_HELP['home'])
    help_window = tk.Toplevel(window)
    window._page_help_window = help_window
    help_window.title(title + ' · 帮助')
    help_window.transient(window)
    footer = ttk.Frame(help_window, padding=10)
    footer.pack(side='bottom', fill='x')
    previous = window.grab_current()
    def close():
        help_window.destroy()
        if previous is not None and previous.winfo_exists():
            previous.grab_set()
    ttk.Button(footer, text='关闭', command=close).pack(side='right')
    body = tk.Text(help_window, wrap='word', height=12, padx=16, pady=14)
    body.insert('1.0', text)
    body.configure(state='disabled')
    body.pack(fill='both', expand=True)
    help_window.protocol('WM_DELETE_WINDOW', close)
    help_window.bind('<Escape>', lambda _e: close())
    fit_window(help_window, (620, 350), minimum_size=(360, 240), parent=window)
    help_window.grab_set()


def _home(window):
    app = _app_for(window)
    if app is None:
        return
    if window is app:
        app.lift()
        return
    # Nested pages return through their owners, preserving each close contract.
    if getattr(window, '_page_id', '') in ('faces', 'assist', 'api'):
        if not messagebox.askyesno('返回主页面', '返回主页面会关闭当前编辑窗口。尚未保存或采用的改动不会应用，是否继续？', parent=window):
            return
    previous_owner = getattr(window, '_previous_grab', None)
    close = getattr(window, '_close', None) or window.destroy
    close()
    if window.winfo_exists():
        return  # API/preparation may still be saving; never bypass it.
    parent = previous_owner if previous_owner is not None and previous_owner is not window and previous_owner.winfo_exists() else getattr(window, 'master', None)
    if isinstance(parent, tk.Toplevel) and parent.winfo_exists():
        _home(parent)
    else:
        app.lift()
        app.focus_set()


def install_page_chrome(window, page_id):
    if hasattr(window, '_page_chrome'):
        return window._page_chrome
    window._page_id = page_id
    bar = ttk.Frame(window, padding=(4, 1))
    bar.pack(side='top', fill='x')
    window._page_chrome = bar
    from .ui_style import style_page_chrome
    def go(action):
        if action in buttons and buttons[action].instate(['disabled']):
            return
        if action == 'home':
            _home(window)
            return
        app = _app_for(window)
        if app is None:
            return
        if action == 'api' and page_id == 'api':
            window.lift()
            return
        method = {'api': '_open_api_config', 'update': '_open_update_page', 'lr': '_open_lr_page'}[action]
        callback = getattr(app, method, None)
        if callback:
            callback()
    buttons = {}
    for name, action, shortcut in [('主页面', 'home', 'Alt+1'), ('配置 API', 'api', 'Alt+2'), ('检查更新', 'update', 'Alt+3'), ('LR 插件', 'lr', 'Alt+4')]:
        button = ttk.Button(bar, text=name, command=lambda a=action: go(a))
        button.pack(side='left', padx=(0, 4))
        button._control_tooltip = ToolTip(button, f'{name}（{shortcut}）')
        if action == 'home':
            button._always_available = True
        buttons[action] = button
        window.bind('<Alt-Key-' + shortcut[-1] + '>', lambda _e, a=action: (go(a), 'break')[1], add='+')
    help_button = ttk.Button(bar, text='帮助', command=lambda: show_page_help(window, page_id))
    help_button._always_available = True
    help_button.pack(side='left')
    help_button._control_tooltip = ToolTip(help_button, '当前页面使用帮助（F1）')
    bar.help_button = help_button
    window.bind('<F1>', lambda _e: (show_page_help(window, page_id), 'break')[1], add='+')
    bar.buttons = buttons
    style_page_chrome(window)
    window.after_idle(lambda: install_control_help(window, page_id) if window.winfo_exists() else None)
    return bar
