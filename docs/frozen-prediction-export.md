# 冻结预测的只读导出

状态：离线工程工具；真实准确率尚未测量。适配只读取明确指定的 JSON 副本，不打开其中记录的照片路径，不调用模型、API 或工作区加载函数。

## 新测量收据 v2

显式开启同步评估记录后，可由实际测量路径产生 `frozen-prediction-provenance-v2`，见 [测量时运行来源收据](measurement-time-receipts.md)。v2 只接受同一完成目录中的会话、清单、收据及独立 `complete.json`；`load_completed_set` 和 CLI 会验证文件字节绑定、拒绝 staging 或缺失完成标记。完整人工确认与独立身体阶段才可扩展转换；旧 v1 拒绝条件不变。依赖记录是实际快照，不声称生产锁。

以下 v1 核查与边界保留作为旧格式兼容说明；“生产不会生成”及人工/身体不支持仅指 v1。新实际采集测试及未支持范围以 v2 文档为准。

## 现有字段核查

| 内容 | 保存／生成位置 | 导出边界 |
| --- | --- | --- |
| 本地建议 | `PhotoAsset.local_recommendation`、`clarity_evidence.recommendation`、`clarity_evidence.primary_quality`；`screening.py:screen_assets`、`analysis_modes.py:assess_asset_mode` | 必须有明确保存的技术建议；`auto_rejected=False` 不是 keep。相对清晰度调整可能修改顶层建议（`relative_focus.py`），冲突不能静默选择 |
| 工作区快照 | `session_store.py:save_session` 保存 `scan-session.json` 的 assets、source_stats、clarity_version、focus_scope | 直接解析副本。禁止调用 `load_session`，因为其缺失源文件协调逻辑会写回工作区 |
| 单次筛查输出 | `screening.py:save_screening_results` 保存 `screening_results.json`，按唯一 stem 索引 | 缺少独立来源身份；本轮不通过文件名猜测来源，也不将其单独作为可验证预测 |
| 人工最终选择／星级 | `ai_project.json` 的 final；`ai_project.py` | 不作为技术真值或本地预测。导出工具不读该文件 |
| AI 复核 | `ai_focus_result` 及 dirty/stale/attempt；`processing_job.py:_apply_focus_review` | 会修改 auto_rejected/screening_reason 和 ScreeningResult 的 rejected/reason；这些字段不能冒充原本地判断 |
| 实际图像成员 | `clarity_evidence.decode`，由 `face_focus.py` 和风景 `analysis_modes.py` 保存 | 必须使用其 source_path/source_kind，不能用 primary_path 猜测。优先 JPEG、RAW 内嵌预览、RAW 全解码可能不同 |
| 身体来源 | `analysis_modes.py`、`body_pipeline.py` 可另外调用 full_image | 当前没有普遍保存身体每次测量的独立解码来源，本轮不能把人脸 decode 冒充身体来源 |
| 证据范围／版本 | `primary_quality.py:measurement_inputs`；`focus_protocol.py:make_scope` | 保存 source_signature、parameters_digest、enabled_parts、主体及确认摘要等；这些是不同含义的版本与摘要，不能用当前常量替换历史值 |
| 原因与区域 | clarity_evidence 的 participants、primary_quality、issues、reasons_by_part 等 | 保留原始原因及结构。未知码不改名；不同坐标空间不强行归一化。未检测到脸不改标成风景 |

## 当前真实数据缺口

生产快照目前没有完整、统一的预测运行 Git 提交、实际依赖记录、有效配置、解码配置以及每阶段实际源文件内容哈希的绑定。source_stats 仅是大小／mtime；source_signature 还依平台含其他文件身份信息，但不保证总含完整内容 SHA-256。

因此单独拿旧 scan-session.json 不能安全补成完整冻结预测。适配器要求已有的测量时来源记录；不得事后编造运行提交、把当前配置套到旧证据、用零哈希占位或重新给旧证据签名。附加记录只证明声明与副本内部一致，不是对声明真实性的数字认证，也不能证明原照片或实时设置仍未变化。

本轮不迁移生产持久格式。最小后续补充方案需另行授权：在本地分析完成时原子保存只读运行收据，包含确切代码提交／脏状态、实际依赖与有效配置、各测量阶段实际源成员和内容哈希、证据版本及范围摘要，再冻结结果副本。身体测量应单独记录来源。不能为使旧数据通过而补写历史收据。

## 准备真实评估资料

1. 明确允许评估的照片或结果副本及用途；私人素材默认留在本机。提供副本，不给活动工作区写权限。
2. 冻结样本清单，登记一次曝光、RAW/JPG 配对和实际用于预测的成员；同曝光只算一次。原片及衍生版登记同一变体族。
3. 固定交付用途、必要主体／眼／脸／身体／风景区域及坐标空间，记录不可见、像素不足和不确定项。
4. 独立填写人工技术标签及理由；人工最终保留、AI 临时标签和审美星级分别记录。八张诊断图不成为技术真值。
5. 登记人物、拍摄场次、连拍、比较组、场景族和历史接触。缺少历史来源先隔离，不重新随机划分冒充盲测。
6. 以相关场景族／人物／拍摄组隔离开发与后续独立验收。看过失败并调参后该集成为历史回归集。
7. 保存真实的运行收据和冻结文件哈希。无法获得必要 provenance 的旧结果如实列为不可转换，不猜补。

Windows 待验：Python 3.12 独立虚拟环境；中文及空格路径、UTF-8 子进程、只读输入字节、既有输出拒绝覆盖、新增适配测试、统计测试和原快速检查分别执行。Windows 验收和真实样本验收是两个后续步骤。

## 命令与独立格式

```sh
python -X utf8 tools/frozen_prediction_export.py \
  --session /outside/frozen/scan-session.json \
  --samples /outside/frozen/samples.json \
  --provenance /outside/frozen/measurement-receipt.json \
  --output /outside/new-export

python -X utf8 tools/offline_evaluation.py evaluate \
  --samples /outside/frozen/samples.json \
  --predictions /outside/new-export/predictions.json \
  --annotations /outside/human/annotations.json \
  --catalog /outside/new-export/catalog.json \
  --output /outside/new-evaluation
```

这些路径是占位示例。输入必须是已冻结的副本，输出目录必须尚不存在且在源码仓库外。输出不会覆盖；不能把命令指向活动工作区当作自动冻结操作。

三个输入：

- session：生产 `scan-session.json` v1 的完整冻结副本。
- samples：已有 `offline-evaluation-v1` samples 契约，见 `offline-evaluation.md` 和其空白模板。
- provenance：独立的 `frozen-prediction-provenance-v1` 测量时收据，kind 为 `measurement_time_receipt`。这不是生产持久格式新增项；生产目前不会自动生成它。

收据包含 synthetic（布尔值）、recorded_at（带时区）、session_sha256（文件原始字节）、manifest_sha256（既有规范 JSON 摘要）、完整 source_git、run_id、prediction_version；records 中 dependency_lock/configuration/decode_configuration 各保存已有 UTF-8 content 与对应 sha256。完整生产依赖记录不是 requirements-dev-checks.txt。

每个 rows 项绑定 asset_id、sample_version、evaluated_source_id、input_sha256、asset_index、asset_sha256（规范 asset 对象摘要）、source_member（确切成员路径）、source_kind、measured_at、measurement_inputs、frozen_inputs 和 versions。所有哈希都来自真实已有记录；不能把示例／合成哈希用于真实输入。`measurement_inputs == frozen_inputs` 只验证冻结记录一致，不能凭此证明实时照片未改。

版本字典包含 analysis_modes、landscape、primary_quality、face、decode、focus_protocol、manual_face_scope，逐项与 source_git 中的静态版本记录核对，不导入业务模块。没有完整收据时应先交付缺口，不手填看似正确的历史记录。

## 本轮可转换范围与输出

保守支持有完整收据、来源绑定及保存证据一致性的风景建议和自动单人脸、仅检查脸的技术建议。人工主体确认快照未完整保存在 measurement_inputs 中；身体测量来源不完整；这两类当前明确失败，等待另行授权补全持久证据。未知／不支持版本、证据过期或冲突、来源不明也失败；样本无映射则记 missing。文档／源身份错配及重复曝光拒绝整批，保存 validation-errors.json。

有效输出：

- predictions.json：复用已有冻结预测契约，读取保存建议，不重新评分。
- catalog.json：从明确 source_git 的已有原因表达式只读提取。
- annotations.json：空白人工标签模板，rows 为空；不替用户填标签。
- diagnostics.json：映射、缺口、原始原因位置、保存范围与版本、输入哈希和工具身份。
- summary.json、cases.jsonl：无人工真值的流程计数。真实准确率固定为“尚未测量”。

未知原因保留原值；无脸结果不会自行变为风景。导出成功不表示每项预测有效，须查看 failed/missing 数量和原因；统计仍覆盖全部清单。

旧 v1 合成适配测试只有元数据，没有图片或真实模型执行。合成端到端演示中的人工标签也是显式合成的统计契约，不是人工验收结果。
