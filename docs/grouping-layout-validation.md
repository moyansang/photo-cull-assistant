# 分组页布局与原因展示：实现及验证

## 改动范围

- `src/ai_cull_assistant/group_editor.py`：紧凑组列表、当前组照片筛选和数量、照片卡三行摘要（照片标识／本地建议／首项原因与另 N 项）、可折叠只读证据栏。
- `src/ai_cull_assistant/ui_help.py`：仅 `PAGE_HELP['groups']` 文案。
- 新增 `tests/test_group_editor_evidence.py` 与 `tests/manual/render_grouping_layout.py`。
- 没有改共享主题、全局字体字号、其他页面布局、分组算法、组排序、拆合规则或即时保存回调。原有虚拟化、2 个缩略图工作线程、24 个待处理请求及 160 项缓存上限保留。

## 行为

左侧组列表不筛掉任何组。顶部下拉只筛当前组照片（全部／待复核／建议保留／建议弃置），显示匹配张数／当前组总张数。照片仍是单击选择拆分起点及查看原因，双击打开现有全屏查看器。切组清除选择；筛选若隐藏当前照片则清除拆分起点，避免误操作。

内容宽度不足 1120 像素时自动收起右栏，用户可手动展开／收起。手动选择保持，不被后续窗口调整覆盖。证据文字不变时不会因缩略图回填重置右栏滚动。

建议类别通过共享 `lightroom_results.local_recommendation()` 校验当前证据，旧 policy 或失效缓存不恢复为弃置；证据充分性不足仍展示待复核。返回本页后如果重算结果使所选照片不匹配筛选，也会清除隐藏的拆分起点。原因优先读取分部位 `reasons_by_part`，按主体选择、面部、身体、测量分开；不重新混入旧版合并原因。已知旧代码使用原有只读格式化函数，新代码保守中文解释，未知代码保留原始值。右栏保留完整原始证据 JSON 供核查。缺原因时明确写未记录，没有编造眨眼、模糊或分组理由。

## 明确未实现

- 基线没有多选、撤销与分组页人工决定数据接口，本次没有制造这几项能力；人工决定明确提示在选片页查看。
- 没有新增同步双图对比或可靠原图坐标映射；仍使用现有全屏查看器，不绘制推测问题框。
- 本页主要呈现缓存本地技术证据，不把它宣称为所有 AI 选片或人工决定的完整审计台。

## 已验证

命令：

```
../venv/bin/python -m pytest -q tests/test_group_editor_evidence.py tests/test_group_editor_model.py tests/test_viewer_controller_integration.py -k 'not fixed_actions'
```

结果：21 passed，1 deselected（被单独运行的 Tk 用例，见下一节）。包括现有分组/虚拟化布局/缩略图线程池及推荐摘要测试，新增原因不串用、未知原因诚实展示、只读不变更、可靠性筛选、切组/筛选清除拆分起点、拆分/合并即时保存回调、响应式收起和保留人工展开选择、证据滚动不被重复刷新重置。

`py_compile` 通过。保留基线 CRLF，`git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check` 通过。

## GUI 验证阻塞，未宣称通过

单独运行 `tests/test_group_editor_model.py -k fixed_actions` 在 `tk.Tk()` 初始化失败：`no display name and no $DISPLAY environment variable`，还没有执行窗口布局断言。云执行器本地准备的官方 Xvfb 启动被 Unix socket `EPERM` 阻止，提升执行又被执行器只读挂载错误阻断。未绕过限制。

因此本次没有真实新 UI 截图，900/1440 窗口实际控件尺寸、中文显示、按钮可达性和大组滚动仍需在可用显示环境完成验收。没有用生成图或其他界面替代截图。

已提供真实生产 App + 嵌入式 GroupEditor 的合成截图脚本：

```
PYTHONPATH=src ../venv/bin/python tests/manual/render_grouping_layout.py /tmp/grouping-ui-captures
```

此脚本在可用 Tk 显示环境下生成几何测试图片，注入明确标注的测试证据，不读取用户照片，不执行模型推理。预期捕获 1440×900、900×660 及大组滚动三种真实程序画面，并写入截图来源与控件指标 JSON。脚本已语法检查，但本云环境无法执行到截图步骤。

整合回归：`tests/test_viewer_controller_integration.py` 8 项通过。新增旧 portrait policy 弃置缓存不能复活、返回页面清除筛选隐藏选择的测试；3 个测试文件合计 29 passed，1 个 Tk 用例因显示环境仍未验收。
