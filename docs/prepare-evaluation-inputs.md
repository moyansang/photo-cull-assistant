# 旧采集清单的显式、只读准备入口

`tools/prepare_evaluation_inputs.py` 只读取明确给定的 **JSON 元数据文件**，把声明支持的 `sample-bindings-v1` 路径记录转换为 `real-sample-path-map-v1`，并生成新的 samples 与缺项报告。不打开、解码、哈希、扫描或测量照片，不下载内容，不创建标签，不修改旧清单或其哈希，不替用户登记主体。输出目录必须不存在且位于源码和照片 roots 之外；已有输出拒绝覆盖。

**兼容边界：** 本轮没有读取本机真实 28 张清单。下列精确定义的旧结构已用合成 JSON 模板验证；真实旧清单的字段结构兼容性仍待 Windows 只读核对。不同结构、未知字段会拒绝，而不是猜测关联或默默丢弃。不要直接改原清单；如实际结构不同，应另行补明确映射并测试。

## 输入形状（占位模板，不是许可或真实来源证明）

旧 samples 保持 `offline-evaluation-v1`、`kind=samples` 和原 rows。一个来源绑定对应一个 entry；RAW/JPG 两来源各一条，必须同一个明确 asset_id，不以文件名配对：

```json
{
  "schema_version": "sample-bindings-v1",
  "roots": {"download-root": "C:\\用户明确指定的采集目录"},
  "entries": [
    {"asset_id": "已登记曝光ID", "source_id": "已登记来源ID",
     "root_id": "download-root", "relative_path": "子目录/文件.jpg"}
  ]
}
```

可选 `manifest_sha256` 必须匹配旧 samples 的规范 JSON SHA-256。`--roots` 可传一个独立的 `root_id → 本机绝对路径` JSON 对象，完整替代旧 roots；不会搜索电脑寻找文件。支持 Windows 盘符/UNC 与 POSIX 绝对路径的纯文本拼接；禁止绝对 relative_path、盘符相对路径及 `..`。不同盘符/目录同名文件不混淆。这里只能拒绝文本路径重复；符号链接、Junction、原生路径解析与实际文件存在性仍须正式入口在目标机器检查。

## 显式补缺登记（可选）

`--registration` 接受 `schema_version=evaluation-input-registration-v1`，可选 `manifest_sha256` 绑定旧 samples。以下字段均为用户或保管者明确提供；不从路径、哈希、格式推断身份或授权：

- `manifest_additions`：仅允许补 `dataset_registry`、`registry_review`、`split_registry`、`guideline_review`。
- `source_additions`：`asset_id → source_id → 字段对象`，只允许补 `dataset_id/dataset_version/original_file_id/original_sha256/preprocessing_history/width/height`。已有不同值拒绝，当前 `sha256` 不可通过此接口更改。
- `sample_additions`：`asset_id → 字段对象`，仅补 `session_id/burst_id`。
- `member_roles`：`asset_id → {primary_source_id, raw_source_id, jpeg_source_id}`。无 primary 时只使用 samples 已明确登记的 `evaluation_source_id`；RAW/栅格角色不按后缀自动填。两个成员必须明确 RAW + JPEG/JPG，不能悄悄丢成员。
- `measurement_authorizations`：数组，每条含完整 `dataset_id/dataset_version`、`scope="measurement"` 和非空 `evidence`。只有调用者确认已有证据明确允许本次测量时填写；下载许可不等同测量授权。工具不核实法律有效性，也不代签。

registry 各项要求参见 [来源与路径预检](real-sample-inputs.md)。版本与 ID 由调用者在同一来源层级明确补齐，不自动拼接其他层级。新增 `guideline_review` 的形状为 `{ "guideline_version": "与原清单完全相同的版本", "frozen": true, "evidence": "保管者真实审核依据" }`；没有真实冻结依据就不填写。工具只保留这些陈述，不证明指南已正确审核。

**授权闸门：** 没有对应显式 measurement_authorizations 时，新 samples 副本的 registry `authorization_confirmed=true` 会保守降为 false，并报告 `explicit_measurement_authorization_missing`；旧 samples/registration 不变。显式测量证据也不会把原有 false 自动升为 true，仍须正确的 registry 登记。这样仅凭下载确认不能让正式批量入口开始读图。

## 运行与结果

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONWARNINGS = 'error'
python -X utf8 -W error tools/prepare_evaluation_inputs.py `
  --samples '<旧samples.json>' --bindings '<旧bindings.json>' `
  --roots '<本机roots.json>' --registration '<明确补缺登记.json>' `
  --output '<从未存在的新准备目录>'
```

输出 `samples.json`、`paths.json`、`preparation-report.json`。后者绑定旧/新清单、bindings、roots、registration 的规范 JSON 摘要，逐例保留缺项与完整分母。缺失绑定不会删除样本；无法形成完整映射的条目不输出半截 binding，而在报告保留阻塞理由。

- exit **3** / `blocked`：缺项文件已生成供补登记；不得当成功测量。
- exit **0** / `metadata_prepared`：支持的转换和元数据门槛已完成，**不是实际预检通过或测量授权证明**。
- exit **2**：结构/路径/已有内容冲突等错误；保留现场，换新的输出位置重跑，不覆盖。

`scene_type=unknown/mixed` 原样保留并阻塞；待冻结指南、缺来源/版本/原始身份/处理历史、待确认主体/角色不会被自动修成可运行数据。转换报告明确 `preflight=not_run`、`native_path_validation=not_run`、`image_files_read=0`、`measurement_performed=false`、`authorization_granted=false`。

仅在用户另外确认测量范围、补齐真实登记且明确授权运行后，才将新 samples/paths 和用户明确配置交给 [批量入口](real-sample-evaluation.md)。不要用本转换的退出码替代正式预检、Windows 原生测试、人工试标或主体确认。真实准确率：**尚未测量**。路径/来源登记文件是私有输入，不进 Git 或交付证据包。
