# 本地技术盲标入口

`tools/blind_annotation.py` 是独立、主动打开的 Tk 工具，不是生产 GUI 收据开关。它不接收预测文件，不读取算法建议、MOS 或数据集质量分，不自动推断技术标签、人物身份、主体确认或专家共识。没有真实获准样本时，只做合成工程验证；真实准确率**尚未测量**。

## 开始与续标

从仓库根运行，使用当地 Python 3.12 图像环境。`samples.json` 和 `paths.json` 与批量入口相同：既有 `offline-evaluation-v1` 清单，加明确授权的 `dataset_registry` 和 `real-sample-path-map-v1` 路径绑定。标注只解码每项明确的 `evaluation_source_id`，不扫描目录，不按文件名配对。来源登记不完整或授权未确认时拒绝读取图像。不同来源许可分别保留。

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -X utf8 tools/blind_annotation.py --samples 'C:\评估 输入\samples.json' --paths 'C:\评估 输入\paths.json' --output 'C:\评估 输出\blind-new' --annotator 'rater-A'
# 只可续开本工具建立且清单、路径映射、指南版本完全一致的目录。
.\.venv\Scripts\python.exe -X utf8 tools/blind_annotation.py --samples 'C:\评估 输入\samples.json' --paths 'C:\评估 输入\paths.json' --output 'C:\评估 输出\blind-new' --annotator 'rater-A' --resume
```

输出必须是仓库外的新隔离目录，不能是照片目录或活跃工作区；存在的目录不会自动接管。续标是显式操作。图像原片只读，解码缓存仅当前进程内存；没有生成旁置文件、云服务请求或上传。

## 最小试标步骤

1. 指定一个稳定的标注者 ID，先冻结用途与指南版本。窗口只显示自己的既有意见，不显示他人的盲标意见。
2. 左侧是适屏全图，右侧是原生解码栅格 100% 局部。点击左图移动右图区域。顶端显示实际来源 ID、解码来源、解码尺寸、方向处理、适屏缩放和解码警告。100% 指一个解码像素对应一个 Tk 图像像素，不承诺任意 Windows DPI 下物理屏幕像素一一对应；这必须在目标屏幕复验。
3. 技术标签、K/D/U/X、人工最终选择、审美偏好分别填写。下拉项默认空，不从其它字段推导。技术标签有 `clear/unusable/borderline/unjudgeable`；证据为 `adequate/insufficient/unknown`；最终选择可留空。`X` 必须填写排除原因。
4. `Add region template` 将当前 100% 裁块坐标加入区域 JSON。逐项明确填写 `role`、`required`、`visibility`、`technical_label`、`evidence_adequacy` 和理由。模板中空字符串/null 都有意不构成有效标签。区域坐标是方向归一化后的原生解码像素，不能越界。
5. `Save draft` 保存草稿，不改变此前有效提交或裁决；未提交过的草稿不进入统计真值。`Submit blind opinion` 验证后显式替换自己的有效提交。`Withdraw own submission` 要求明确理由，可撤回自己的有效提交；取消不作更改，历史与草稿仍保留。翻页和关闭会提示未保存内容将丢失，不会默默保存或填入标签。
6. 第二位标注者以自己的 ID 续开同一目录，独立提交。文件是本机私有数据而不是权限隔离保险库；独立性仍依赖真实人员及操作流程，不以不同 ID 自动认证双盲。
7. 自己提交后才能点击 `Adjudicate submitted opinions` 查看已提交原始意见。至少两位标注者后，显式填写裁决理由并保存当前表单作为裁决。原始意见不覆盖。之后任何新提交或显式撤回使旧裁决失效，需要重新审查；仅保存草稿不使裁决失效。即使撤回使意见集合回到旧组合，旧裁决也不会复活。技术依据 `reason` 与过程理由 `adjudication_reason` 独立保存。单标、相同意见或已裁决均不自动称“专家共识”。
8. `Export submitted labels` 写到新的、独立 JSON 文件，再交给已有离线统计入口。也可 `--resume --export 'C:\评估 输出\annotations-01.json'` 无窗口导出。各字段独立合并：技术标签、KDUX、区域结构/技术判断、证据充分性、最终选择、审美分别比较，冲突字段不采用首位意见（置 null，区域置 []，证据置 unknown），其余一致字段保留。例如仅审美冲突不抹掉一致的技术结论。`disagreement_fields`/`unresolved_fields` 明确冲突维度；多位意见的汇总身份为会话聚合身份，不冒充第一位标注者。逐人原文在 `original_ratings` 保留；技术或区域理由措辞差异不自动成为技术分歧，相同才放入共同理由，不同则为 null。未裁决冲突的总理由为 null。多位 X 的不同排除理由按标注者显式署名导出并标记 `per_rater_attribution`，不是共同理由。草稿不输出真值。

区域示例（仅格式，不是已标注数据）：

```json
{
  "region_id": "required-body-1",
  "role": "required_body",
  "required": true,
  "visibility": "insufficient_pixels",
  "technical_label": "unjudgeable",
  "evidence_adequacy": "insufficient",
  "coordinate_space": "oriented_native_decoded_pixels",
  "box": [20, 30, 100, 160],
  "reason": "按冻结用途判断，必要身体区域像素不足"
}
```

草稿允许区域模板保留空的 role/visibility/technical_label/evidence_adequacy、required=null 和空/缺失理由；区域 ID、坐标系及坐标合法性仍校验。完整提交要求每个区域有非空字符串 reason；非文本理由即使草稿也拒绝，不自动填充。无法判断可显式填写 unjudgeable 并说明原因。

旧版本 journal 若仅保存了被覆盖的裁决 reason，重开不会伪造丢失的技术理由：导出技术 reason=null、原理由作为 adjudication_reason，标记 legacy_technical_reason_unavailable=true；须重新人工裁决才能补齐。

角色允许 `left_eye/right_eye/face_core/required_body/landscape_subject/texture`，可重复角色但区域 ID 必须唯一。可见性允许 `visible/not_visible/insufficient_pixels/unjudgeable`。不见或不足不等于模糊；应明确使用无法判断及理由。没有有效坐标时 `box=null`；不能伪造框。这里的区域只描述人工质量观察，不转换成生产人物选择、ROI 确认或其它人工决定。

## 来源、写入与成本

- 标准图片复用生产标准解码；RAW 明确请求完整 RAW 解码，不允许将内嵌缩小预览称为原生全图 100%。不支持的 RAW 或缺依赖时停止该图显示，不能自动改用另一成员。真实 RAW 机型、方向、颜色和本地交互留待 Windows 验收。
- 打开图像前后验证内容哈希，提交和导出时再次核验已声明来源。额外读取是有目的的 TOCTOU 校验，不重复解码同一显示图。`cost_snapshot` 记录当前进程累计成功哈希次数/字节/耗时及解码耗时；哈希耗时与解码耗时分开，且不是完整评估批次成本。续标新进程重新计数，不可把多个累计快照相加。
- 复用 `operation()` 的跨进程锁和 `atomic_json`；单一 journal 原子更新，包含所有意见历史及修订号，避免标签和历史分开半写入。并发过期写者拒绝保存，要求重开，不盲重试。写入失败不更新内存 revision、不覆盖先前 journal；可能保留本次临时文件供检查。
- 清单/指南/路径映射变更必须新建标注会话，不给旧标签自动重新绑定。标注导出是提交后的冻结输入，可供后续对照；它不是测量收据，也不修复缺失来源的旧预测。

## 工程验证与待验项

Linux 纯核心测试使用合成图片/明确标识的合成意见，不使用私人照片。实际命令和最终计数随独立证据包报告。

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -X utf8 -W error -m pytest -q tests/test_blind_annotation.py
```

Windows 需逐项复验：中文/空格路径；原生文件锁与写入拒绝；适屏/100%裁块、DPI和方向；RAW完整解码或明确错误；保存/续标/版本失配；翻页/关闭取消；独立二人意见不提前展示；提交后分歧与裁决；原片字节不变；导出不覆盖。Linux headless 通过不能替代这些交互或真实照片验收。没有接入生产 GUI 并行收据，也没有处理 EXE 构建身份。

当前界面仅查看清单 `evaluation_source_id` 指定成员的原生解码，不切换同曝光 RAW/JPG；所有区域意见绑定该来源。收据可记录脸/身体实际不同阶段来源，但工具不会把一个成员的人类标签自动复制为另一个成员的真值。跨成员人工比较与真实RAW交互需另行验收。
