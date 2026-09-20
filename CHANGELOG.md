# 更新日志

当前正式版使用 CPU。本页将发布历史与当前行为分开；本地 GPU 研究未作为正式版功能发布。

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
