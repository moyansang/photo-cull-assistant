# 更新日志

当前正式版使用 CPU。本页将发布历史与当前行为分开；本地 GPU 研究未作为正式版功能发布。

## v1.5.9 构建 2 · 2026-10-03

- v1.5.9 构建 2：补框确认、拒绝、跳过、手动调整和停留位置可恢复；已确认框自动积累为同一参考链的多参考样本，下一轮优先复用同组参考，拒绝框降低相似候选排序。新增重新查找未确认、移除参考和清空积累，不自动采用候选，不训练模型。

## v1.5.9 · 2026-10-03

正式发布此前本地构建中的恢复摘要、流程推荐、多人确认及更新检查改进，并包含辅助工具窗口和人脸查找修复。见 [发布说明](docs/releases/v1.5.9.md)。

以下为本版开发过程的构建记录：

- v1.5.8 构建 7：自动检查更新不锁住主界面，TLS 读取提前结束可有限重试；多脸未确认主体时阻止进入 AI 复核/选片，本地检查保留待确认；人脸页显示逐人本地或 AI 清晰度结果，修改选择后提示重新扫描。

- v1.5.8 构建 6：根据保存进度推荐继续扫描、AI 复核、生成联系表、继续选片或导出 LR；恢复摘要分别统计待提交、失败、失效、跳过和部分返回。新发生的 AI 复核失败/跳过状态随照片保存，成功重试后清除。

- v1.5.8 构建 5：启动和切换工作区时，日志显示保存的分组、清晰度及联系表设置，以及扫描、AI 清晰度复核、联系表、AI 选片和 LR 导出的恢复摘要；区分未完成任务、失效结果和复核后仍待确认的照片。摘要只读取记录，不发起 AI 请求。

- v1.5.8 构建 4：检查更新时遇到连接提前关闭、读取中断或临时服务错误，每个地址最多重试一次；REST 请求失败可切换公共发布地址。网络失败显示中文说明，不写入成功检查缓存。保留更新包校验和用户数据保护。

## v1.5.8 · 2026-09-29

- 工作流页面嵌入主窗口，统一导航、返回主页和共享进度／日志；切换页面保留编辑状态。
- 分组缩略图自适应窗口及照片数量，人脸预览按横竖方向布局，减少黑边和重复文字。
- 全局置信度移到主页，整理框选、重新扫描与 LR 导出按钮。详见 [发布说明](docs/releases/v1.5.8.md)。

## v1.5.7 · 2026-09-28

- 多脸自动主体加入人工确认范围；手动选人可替换自动选择，保存候选数量并兼容旧工作区后台预览补查。
- 补齐人脸新增 Y／X／P 快捷键，确认、排除和暂时跳过后自动前进。
- 文件夹选择回到对应路径；照片首次选择打开“此电脑”。
- 改善 Windows DPI 缩放和帮助提示。详见 [发布说明](docs/releases/v1.5.7.md)。

## v1.5.6 · 2026-09-23

- 统一桌面工作台、顶部入口与子页面视觉，支持工作区人物候选查找和人工确认。
- 完善 API 配置窗口与千问预设、AI 任务准备关闭及恢复。详见 [发布说明](docs/releases/v1.5.6.md)。

## v1.5.5 · 2026-09-21

- 清理无调用辅助函数、资源属性别名、多余导入和旧重新生成入口；保留旧工作区兼容及仍被自检使用的流程。后台预备错误写入完整日志、主页面隐藏，日志写入失败不阻断复核。

- 清空工作区旁新增“清空日志”，仅清除当前窗口显示，保留日志文件；后续日志正常记录和显示。
- 切换工作区、重新定位原照片后清除旧日志显示，不重新载入历史日志；软件启动时仍可恢复日志。
- 处理或更新期间保留“清空工作区”按钮可点击，点击说明暂不能清理的原因，避免无响应的禁用按钮。

## v1.5.4 · 构建 2 · 2026-09-21

- AI 清晰度复核保持串行请求，在等待期间由单个后台线程预备下一张原尺寸细节与上传编码；上传编码缓存上限 32 MiB。
- 每轮有限量预备联系表缩略图，生成联系表时复用；照片预览改变后自动失效，清理预览时一起清理。预备失败不改变复核结论。
- 停止后不再开始新预备任务，等待当前图片处理收尾；继续时使用本次设置。临时上传编码退出复核时释放，原尺寸细节沿用暂停保留、复核完成清理规则。

## v1.5.4 · 2026-09-21

- 构建 1：移除人脸预览加载提示，避免图片已显示或选择蓝框时出现遮挡文字；保留后台加载和缓存。

- 人脸预览按内存预算缓存，图片与检测分开复用，并预加载附近照片。
- 扫描顺手准备待复核的原尺寸细节，API 重试复用，减少重复 RAW 解码。
- 临时细节暂停时保留，整轮 AI 复核完成后清理，保留判断结果和审计记录。

[完整 v1.5.4 说明](docs/releases/v1.5.4.md)

## v1.5.3 · 2026-09-20

- 新增跨电脑原照片重新定位，保留分组、人脸设置、AI 结果与暂停进度；迁移前核对并备份。
- 分组缩略图、人脸预览后台加载；AI 复核补建缺失预览。
- 清空工作区后台执行，明确停止等待状态；选择新照片目录及时切换工作区。

[完整 v1.5.3 说明](docs/releases/v1.5.3.md)

## v1.5.2 · 2026-09-20

- 滚动补入扫描任务，保留停止和断点恢复，隔离切档前后的测速样本。
- 区分复用线程与新增线程的内存预算，补充完整日志诊断信息。
- 清晰度判断规则不变，不承诺固定提速比例。

[完整 v1.5.2 说明](docs/releases/v1.5.2.md)

## v1.5 · 2026-09-16

- 删除部分原片后自动移除记录，保留工作区及其余分析；新增原片或 RAW/JPG 配对变化时增量扫描。
- 新增照片另行分组，保留已有分组与分析；清单变化后重新生成联系表、导出 LR。
- 精选再选只包含当前有效、未弃置的 4–5 星候选，并按 API/网页页面衔接操作。
- 统一程序与打包版本来源，每次打包重新构建，输出一个标准名称的便携包。
- 重整 README、使用指南、FAQ、第三方来源、开发说明和完整版本历史。

[完整 v1.5 说明](docs/releases/v1.5.md)

## 已发布版本索引

| 版本 | 日期 | 当时的主要更新 |
|---|---|---|
| [v1.4](https://github.com/moyansang/photo-cull-assistant/releases/tag/v1.4) | 2026-09-15 | v1.4 |
| [v1.3.1](https://github.com/moyansang/photo-cull-assistant/releases/tag/v1.3.1) | 2026-09-14 | AI选片助手 v1.3.1 |
| [v1.3](https://github.com/moyansang/photo-cull-assistant/releases/tag/v1.3) | 2026-09-14 | AI选片助手 v1.3 |
| [v1.2.3](https://github.com/moyansang/photo-cull-assistant/releases/tag/v1.2.3) | 2026-09-14 | v1.2.3 人脸框与工作区恢复修复 |
| [v1.2.2](https://github.com/moyansang/photo-cull-assistant/releases/tag/v1.2.2) | 2026-09-13 | v1.2.2 清晰度改进与身体检查实验开关 |
| [v1.2.1](https://github.com/moyansang/photo-cull-assistant/releases/tag/v1.2.1) | 2026-09-12 | v1.2.1 性能优化与裁切交互改进 |
| [v1.2.0](https://github.com/moyansang/photo-cull-assistant/releases/tag/v1.2.0) | 2026-09-12 | v1.2 分阶段选片与工作区整理 |
| [v1.1.1](https://github.com/moyansang/photo-cull-assistant/releases/tag/v1.1.1) | 2026-09-12 | v1.1.1 工作区与目录管理 |
| [v1.1.0](https://github.com/moyansang/photo-cull-assistant/releases/tag/v1.1.0) | 2026-09-12 | v1.1.0 API配置修复与LR判断理由 |
| [v1.0.0](https://github.com/moyansang/photo-cull-assistant/releases/tag/v1.0.0) | 2026-09-12 | v1.0 正式版 |
| [v0.5.11](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.11) | 2026-09-11 | v0.5.11 修复API窗口切换锁死 |
| [v0.5.10](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.10) | 2026-09-11 | v0.5.10 人脸模糊漏检修正 |
| [v0.5.9](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.9) | 2026-09-11 | v0.5.9 保留已有AI选片任务 |
| [v0.5.8](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.8) | 2026-09-11 | v0.5.8 页面布局与清理修正 |
| [v0.5.7](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.7) | 2026-09-11 | v0.5.7 选片流程与界面优化 |
| [v0.5.6](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.6) | 2026-09-11 | v0.5.6 人脸清晰度筛查改进 |
| [v0.5.5](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.5) | 2026-09-11 | v0.5.5 处理进度与停止续做 |
| [v0.5.4](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.4) | 2026-09-11 | v0.5.4 导出 AI 评分到 Lightroom |
| [v0.5.3](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.3) | 2026-09-11 | v0.5.3 恢复分析与日志 |
| [v0.5.2](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.2) | 2026-09-10 | v0.5.2 网页合并提交 |
| [v0.5.1](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.1) | 2026-09-10 | v0.5.1 API 预设与配置记忆 |
| [v0.5.0](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.5.0) | 2026-09-10 | v0.5.0 · AI选片与人工复核 |
| [v0.4.11](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.11) | 2026-09-10 | v0.4.11 修复更新检查限流 |
| [v0.4.10](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.10) | 2026-09-10 | v0.4.10 照片保留原目录 |
| [v0.4.9](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.9) | 2026-09-10 | v0.4.9 自动检查与安全更新 |
| [v0.4.8](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.8) | 2026-09-10 | v0.4.8 Lightroom 目录插件对接 |
| [v0.4.7](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.7) | 2026-09-10 | v0.4.7 快速查找未标记人脸 |
| [v0.4.6](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.6) | 2026-09-10 | v0.4.6 逐张人脸编辑与设置记忆 |
| [v0.4.5](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.5) | 2026-09-10 | AI 选片助手 v0.4.5 — 可调检测置信度 |
| [v0.4.4](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.4) | 2026-09-10 | AI 选片助手 v0.4.4 — 中文分组档位 |
| [v0.4.3](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.3) | 2026-09-10 | AI 选片助手 v0.4.3 — 默认使用程序目录 |
| [v0.4.2](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.2) | 2026-09-10 | AI 选片助手 v0.4.2 — 人脸裁切设置 |
| [v0.4.1](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.1) | 2026-09-10 | AI 选片助手 v0.4.1 — YuNet 人脸细节 |
| [v0.4.0](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.4.0) | 2026-09-08 | AI 选片助手 v0.4.0 — 人物分组与人脸放大 |
| [v0.3.1](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.3.1) | 2026-09-08 | AI 选片助手 v0.3.1 — 记住目录 |
| [v0.3.0](https://github.com/moyansang/photo-cull-assistant/releases/tag/v0.3.0) | 2026-09-08 | AI 选片助手 v0.3 — Windows 便携版 |

v1.4：共享 RAW 解码与基于实测吞吐量的 1/2/4/6/8 自适应 CPU 并行。

[历史 Release 原文](docs/release-history.md)保留各版详细说明与验证范围。
