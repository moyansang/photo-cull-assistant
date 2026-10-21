# 外部参考项目落地对照（A～E）

## 阅读口径与范围

本轮基线 `d0bbd2276de5e3fba9b779cefddd646f884edcf1`。2026-10-21 对下列**固定提交**作静态源码／文件阅读，不运行外部工程、不下载模型或数据、不复制源码、不新增依赖。7 个工作流项目均读到实现；5 个研究项目中 HiFST、LIQE、SER-FIQ 读到实现，PyIQA 仅核对固定 ModelCard／LICENSE，PIQ2023 仅核对固定 README 的数据说明／条款，**后两者未审计推理实现**。这不是第三方软件运行测试、全面代码审计或性能比较。

最初 web 的部分 raw／blob 请求返回 `Cache miss`；随后 GitHub connector 在相同完整 SHA 成功读取，未改用 main。逐文件 URL、commit、Git blob SHA、访问方式见独立证据包 `external-reference-access.json`。许可证栏区分本轮读到的条款和任务方案登记；任何后续引入仍须重新核对代码、权重、训练数据及分发条件。

以下“采用”均指**独立实现的流程理念**，不是移植第三方算法。项目功能由本轮任务驱动；源码阅读同时用于检验取舍，不能倒称每项实现源自该项目。

## 1. 七个工作流项目：实现与取舍

### Facet
- 固定提交：`9a46621f621f8343655beb84a35f762ccdb683ba`；已读 [face.py](https://github.com/ncoevoet/facet/blob/9a46621f621f8343655beb84a35f762ccdb683ba/analyzers/face.py)、[burst_score.py](https://github.com/ncoevoet/facet/blob/9a46621f621f8343655beb84a35f762ccdb683ba/processing/burst_score.py)、[模型许可说明](https://github.com/ncoevoet/facet/blob/9a46621f621f8343655beb84a35f762ccdb683ba/docs/THIRD_PARTY_MODELS.md)。
- 实现观察：输入已加载图像；逐脸检测、眼区锐度、眨眼分别形成字段，burst 再加权聚合。无脸返回零值；burst 部分缺值按零处理。**此提交逐脸眼分取左右眼最大，照片眼分取各脸平均**，不是方案文字可能暗示的“跨脸也取最大”；face_quality 来自检测置信度，不能称眼睛合焦真值。
- A／D 取舍：采用维度可解释、组内人工比较理念；不引入加权总分、缺值归零、逐眼算法或个人排序。落地 `scan_review_ui.evidence_text` 四路呈现、`ComparisonWindow`；人工决定独立于技术状态。未有有效 AI 结果显示缺失／过期，不制造数值。
- 验收：`test_scan_review_ui.py`、`test_scan_review_decisions.py`；Windows 看两张同组图与四路文本，不把眼带称逐眼。
- 许可：固定模型说明自述代码 MIT，并明确若干权重非商用；**MIT 不覆盖所有权重**。本轮没有引入其代码／模型。

### OpenPhotoCull
- 固定提交：`8824c83b8b2dce461e1d33f0ac77e0d7168d5053`；已读 [scan.rs](https://github.com/zwoodard/OpenPhotoCull/blob/8824c83b8b2dce461e1d33f0ac77e0d7168d5053/src-tauri/src/commands/scan.rs)。
- 实现观察：单图处理复用 analysis_image；灰度生成模糊／曝光，图像生成 hash，人脸框优先、显著性备用；主体结果可能覆盖全局模糊判定。无法得到分析图时保留索引但分析字段为空。扫描池上限 8；主体框带来源，坐标原点须转换。
- A 取舍：采用来源和维度明示、失败输入不消失的理念；不移植清晰度阈值、主体覆盖规则、Vision、并行池或分组。落地 `result_state`、`recorded_overlays`、`load_inspection_source`：历史同来源／尺寸才画框，过期不画，缺少逆变换的测量子区明确缺项；不凭 EXIF 证明运动。
- 验收：`test_scan_review_ui.py`、`test_scan_review_restore.py`；Windows 验旋转、原生倍率、离线来源及候选／选用／确认图层。
- 许可：MIT 为方案登记，本轮未另读根 LICENSE；没有采用代码，Apple 专有接口不视为 Windows 可用能力。

### LrGeniusAI
- 固定提交：`4234398348b946a3ffee09d0c3586c67d4a71ab2`；已读 [TaskCullPhotos.lua](https://github.com/LrGenius/LrGeniusAI/blob/4234398348b946a3ffee09d0c3586c67d4a71ab2/plugin/LrGeniusAI.lrdevplugin/TaskCullPhotos.lua)。
- 实现观察：选择范围→缺项准备→服务分组→收集建议与警告→写插件属性和结果集合；准备局部失败仍处理成功照片，全失败报错；后端 keep_all 被呈现为保留整套，缺 Catalog ID 计入告警。该文件**确实写 Catalog 属性／集合**，不应描述为整个过程纯预览。本轮未读其 grouping.rs／jobs.rs，不据此宣称已有耐久任务协议。
- D／E 取舍：采用建议和用户处理分开、问题可见、已有成员不意外丢失；本轮不增加套片识别／保留算法，也不移植服务或插件。沿用 `ReviewProject.export_final`、`lightroom_results.py` 及原 JSON／Core.lua；输出文件不代表 LR 已应用，人工保留不能洗掉技术待确认。
- 验收：`test_scan_review_decisions.py`、`test_lightroom_results.py`、`test_lightroom_plugin.py`；Windows 用对应插件在测试 Catalog 验证留弃、1～5 星、0 清星和不设置不覆盖。
- 许可：AGPL-3.0 为方案登记，本轮未另读 LICENSE；不复制实现，不新增其服务／协议，也不声称已完成 LR 主机回执。

### Cullumi
- 固定提交：`168f9c4d705a3a2c667cfe3554c214e7e3f566b9`；已读 [analysis_worker.py](https://github.com/Yuumi0221/Cullumi/blob/168f9c4d705a3a2c667cfe3554c214e7e3f566b9/cullumi/analysis_worker.py)。
- 实现观察：输入 source／thumbnail，经带任务 ID 的队列送隔离进程；轮询取消和截止时间；异常退出／worker failure 有有限重试，超时返回失败分析，迟到任务 ID 不接受。没有把失败当成成功低分。本轮未读 project_store.py，不能据此声称其完整保存恢复协议已核实。
- B／C 取舍：采用失败可见、显式取消、旧结果不被迟到结果覆盖的理念；不搬 worker 架构、内存限额、SQLite 或移动原片流程。使用本项目已有 ProcessingJob／发布日志，`ScanReviewStore` pending／completed／interrupted 与失败尝试，`validate_review_publication` 多版本校验；`cancel_recheck` 取消未发布任务。
- 验收：`test_scan_review_actions.py`、`test_scan_review_cancel.py`、发布崩溃恢复测试；Windows 停止／继续／取消、工作区占用与保存失败。
- 许可：应用根许可证在方案中未确立，本轮不作许可推断；只取流程理念，无代码引入。

### Photo-Curator
- 固定提交：`1cb56a07a9ed3bff7e3d3f93a9305c31d1cd6e52`；已读 [raw_loader.py](https://github.com/kotyzap/Photo-Curator/blob/1cb56a07a9ed3bff7e3d3f93a9305c31d1cd6e52/raw_loader.py)。
- 实现观察：RAW 优先提取足够大的嵌入 JPEG；失败回退 rawpy 解码，默认 half_size；PIL 路径失败抛 OSError，OpenCV 路径允许 None。预览不是完整 RAW 原生像素。未读其 dedup／ranking 实现，分阶段去重仅保留方案设计参考，**未落地新算法**。
- A／D 取舍：采用展示真实来源、尺寸及失败的理念；本轮保留既有解码和 RAW/JPEG 成员关系，不按 stem 重配、不改解码优先级。`InspectionPane` 显示当前来源和倍率；`recorded_overlays` 不将另一来源坐标硬套。
- 验收：`test_scan_review_ui.py`、合成闭环的原片字节保护；Windows 真实 RAW／嵌入预览／旋转和来源切换必须补验。
- 许可：MIT 为方案登记，未另读根 LICENSE；无代码移植或 rawpy 新依赖。

### CullAI
- 固定提交：`6e1cd99c7a5180a2d6899fdd61c27dd3015a23f0`；已读 [session-manager.ts](https://github.com/AshminDhungana/CullAI/blob/6e1cd99c7a5180a2d6899fdd61c27dd3015a23f0/src/main/session-manager.ts)。
- 实现观察：按 image ID 存结果，用 tmp／bak／rename 和逐文件 Promise 锁写分数；读主文件损坏可回退 bak，已评分 ID 用于恢复；状态含完成／取消／崩溃。人工 updateTier 直接改 score record 的 tier，**不是原判断与人工值独立保存**；不能照搬。本轮未读 ai-client／xmp-writer，不宣称其服务调用或 sidecar 行为已验证。
- B／C／E 取舍：采用稳定 ID、持久进度和错误显式化；本轮沿已有原子写／发布恢复，不另建会话框架；人工字段写原 ReviewProject 并留撤回历史，反馈另存 `scan-review.json`。版本冲突拒绝保存；损坏记录不静默覆盖为空。
- 验收：store／restore／decisions／integration 专项；Windows 重开疑问和人工值、撤回后原技术判断仍在，模拟锁失败后不显示成功。
- 许可：MIT 为方案登记，未另读根 LICENSE；没有引入 Electron、外部服务或 XMP 写入实现。

### AnicropStudio
- 固定提交：`8b6c2faf69038c10f87160a60b804925f1acb87f`；已读 [focus_inspector.py](https://github.com/R4ksh4/AnicropStudio/blob/8b6c2faf69038c10f87160a60b804925f1acb87f/focus_inspector.py)、[rating_writer.py](https://github.com/R4ksh4/AnicropStudio/blob/8b6c2faf69038c10f87160a60b804925f1acb87f/rating_writer.py)。
- 实现观察：显示方向空间读图，检测框映回原图，关键点眼中心／启发式／AF 作为定位回退；inspect 的 score=0、eye_sharp=True 是兼容占位，**不是合焦测量**。模型缺失路径会下载；writer 用临时文件替换目标，非只读行为。
- A／D／E 取舍：仅采用坐标来源清楚与人工定位的理念；本轮不运行其定位器、不下载检测模型、不借常量当评分、不写原图／sidecar。历史 ROI 只据真实记录显示，100% 是当前加载来源的像素倍率，不承诺为 RAW 全像素；LR 仍走现有导出。
- 验收：viewer／UI 来源错配测试、合成原片哈希保护；Windows AF 文案不应表示已合焦，保存星级不应改变照片和 sidecar。
- 许可：Apache-2.0 为方案登记，未另读根 LICENSE；代码许可不自动覆盖下载权重。本轮未采用代码和模型。

## 2. 五个研究项目：明确暂缓，不伪装成生产能力

| 项目、固定提交与已读文件 | 静态观察／本轮结论 | 许可与后续门槛 |
|---|---|---|
| HiFST：`d49a5ccdafa54fcbed63dd9f96976250b74adcf5`；[HiFST.m](https://github.com/isalirezag/HiFST/blob/d49a5ccdafa54fcbed63dd9f96976250b74adcf5/HiFST.m)、[README.txt](https://github.com/isalirezag/HiFST/blob/d49a5ccdafa54fcbed63dd9f96976250b74adcf5/README.txt) | 多尺度局部窗口、频域及热图；Noise 参数实际加噪，不能叫噪声估计器。暂缓，不新增热图／全图密集计算；A 仍展示旧证据，不能写“HiFST 证明清晰”。 | 已读有条件使用／再分发和版权保留条款，不称无条件 MIT。后续先授权、样本、CPU 成本和解释性实验。 |
| LIQE：`a64655f7b84b9543b513394472851884cde5b257`；[LIQE.py](https://github.com/zwx8981/LIQE/blob/a64655f7b84b9543b513394472851884cde5b257/LIQE.py) | CLIP 图文质量／场景／失真输出。采样步长用 batch 维 x.size(0)，小 batch 可重复首 patch，是静态风险观察，未运行确认。暂缓，无模型／API；不能作为眼焦真值或替代摄影判断。 | MIT 代码为方案登记，权重另核。后续须核固定实现、采样和 CPU／内存／权重权限。 |
| PyIQA：`6b25ceebd82e8ba9a3500025a373a3b5c62f9bd6`；[ModelCard](https://github.com/chaofengc/IQA-PyTorch/blob/6b25ceebd82e8ba9a3500025a373a3b5c62f9bd6/docs/ModelCard.md)、[LICENSE](https://github.com/chaofengc/IQA-PyTorch/blob/6b25ceebd82e8ba9a3500025a373a3b5c62f9bd6/LICENSE) | 仅核模型目录／分值方向，不是推理实现审计。暂缓；未测试或安装任何指标。模型文档存在不等于当前产品可用。 | 已核 PolyForm Noncommercial 1.0.0；各模型／权重条件另查，不能按包名统一放行。 |
| SER-FIQ：`611296605db57b8d50518fd5911d5111eeb52747`；[face_image_quality.py](https://github.com/pterhoer/FaceImageQuality/blob/611296605db57b8d50518fd5911d5111eeb52747/face_image_quality.py) | 对齐人脸重复前向，比较识别 embedding 距离；默认 T=100。暂缓：识别稳定性不等于逐眼合焦，也未证明可替换当前识别器。 | 文件头已核 CC BY-NC-SA 4.0；依赖／权重另查；未来先评估算力与目标一致性。 |
| PIQ23／PIQ2023：`4a20fa824c7d082c00277229238b71a78067af3f`；[README](https://github.com/DXOMARK-Research/PIQ2023/blob/4a20fa824c7d082c00277229238b71a78067af3f/README.md) | 仅核数据说明：人像细节、曝光、整体质量成对比较；未读训练实现、未申请／下载数据。暂缓；摄影主观维度不等于本项目眼焦标签。 | 已读数据限非商用研究及附加使用条件；根代码许可本轮未核，不能把数据许可当代码许可。 |

五项均无生产入口、无效果／速度结论；对应本轮证据是未改变评分链路的回归与“未支持”提示，而非外部模型实验。后续若采用必须另立任务，不属于本次 A～E 完成量。

## 3. 横向问题、实际落地和验收闭环

下列相对源码路径均位于 `src/ai_cull_assistant/`（插件除外）。

| 问题与多项目取舍 | 本轮实际入口／状态／人工点 | 证据与 Windows 验收 |
|---|---|---|
| Facet／OpenPhotoCull：多种质量维度不能压成最终留弃；缺指标不等于差 | 主页→扫描结果与依据；`scan_review_ui.result_state/evidence_text` 分未运行、失败、待确认和建议，独立本地技术／有效 AI 技术／AI 摄影／人工值 | UI／restore 专项；全部结果中失败仍存在，默认筛选不等于删除，人工留后原问题仍见 |
| OpenPhotoCull／Photo-Curator／Anicrop：检测、显示、实际测量坐标与源各不相同 | `scan_review_viewer.recorded_overlays` 校验来源、kind、尺寸、签名；缺历史眼带逆变换不补画；框选保存草稿而非即时测量 | UI 专项；Windows 原生缩放、旋转、RAW 预览和失效来源，不将 AF／候选等同确认 |
| CullAI／Cullumi：反馈、处理中状态和最终判定分层 | `ScanReviewStore.record_feedback` 身份快照、历史、修订；`ReviewDecisions.set/withdraw` 独立留弃／星级。疑问不调用评分，不表示否决／认可 | store／decisions 专项；保存、关闭重开、编辑／撤回，0 清星与不设置区分 |
| Cullumi／CullAI：持久化不只“写成功”，还须阻止旧响应覆盖 | `prepare_scope` 先 before；ProcessingJob 单张整图、regroup=False；`validate_review_publication` 检查结果／设置／组／来源／草稿版本；`publication_transaction` 联合发布。失败保留原值，停止可继续，显式取消记 interrupted | actions／cancel／integration 和原发布崩溃恢复测试；Windows 锁、断开、旧页面保存和重查停止／继续／取消 |
| Facet／LrGeniusAI：同组比较不意味着换分组规则或强制唯一胜者 | `ComparisonWindow` 同组两图独立定位；原 group_editor 拆合组；人工可留多张，不触发新评分／AI | decisions／integration；Windows 两图并排、缩放及组关系；本轮不新增连拍／套片检测 |
| LrGeniusAI／Anicrop／CullAI：建议导出不等于改原片或 LR 已接收 | `ReviewDecisions.export`→原 ReviewProject→LR JSON；技术 review 与人工 final 独立；Core.lua 接受显式 rating 0。旧插件不兼容新清星 0 | Lightroom results/plugin 回归与合成闭环；Windows 对应插件＋测试 Catalog 实验，原图／sidecar 字节保持，撤回不丢原技术判断 |

### 代价、局限及验收状态

- 查看、筛选、写疑问、留弃／星级不新增评分或 API；原生查看有本地解码成本。只有显式应用关注范围才重测一张整图，未声称区域增量；face 可应用，body／hand／other 仅待支持草稿。已有身体选择保留，用户须确认仍属本次主要人物。
- 仍用既有工作区和附加 JSON，不另建数据库；历史增加本地体积，尚无压缩。快速来源签名不是完整内容哈希。旧工作区缺项明示，打不开的来源只读保留；整目录离线不可重算、改组或 LR 导出。
- 四路独立和保存恢复均由本项目测试验证，不由外部项目特性代替证明。专项数量、最终 clean HEAD 及合成链路日志以本次证据包主报告为准，本文不把中间运行统计冒充最终结果。
- **Linux 未完成可视窗口交互验收**；保留环境启动实际错误，headless 断言／绑定检查不算 GUI 通过。Windows Tk／DPI、文件锁、真实 RAW、摄影准确率、付费 AI、真实 LR Catalog 应用仍未验。操作步骤见交付 `WINDOWS-HANDOFF.md`；外部工程本轮全部未运行。
