# 显式清单批量评估与本地盲标

工程入口，不是新算法或真实准确率结论。真实准确率：**尚未测量**。
仅在用户明确授权的本地清单内运行，不扫描目录，不下载数据、模型，不调用 API，不上传图像。生产 GUI 并行收据与 EXE 构建身份仍未支持。

## 链路与边界

`real_sample_evaluation.py` → `evaluation_inputs.preflight` → 生产同步 `screen_assets` + `MeasurementRecorder` → 完成标记绑定的冻结副本 → 原 `frozen_prediction_export` → 原 `offline_evaluation.evaluate`。

- 样本仍是 `offline-evaluation-v1`；新增来源登记见 [输入说明](real-sample-inputs.md)。旧离线输入/工作区无需迁移，旧来源不足收据拒绝规则不变。
- 显式来源映射绑定清单规范 JSON 哈希，成员角色与实际内容哈希验证；不按同名文件或目录猜 RAW/JPG、人物或真值。内部 stem 使用 asset ID 摘要避免不同目录同名覆盖。RAW+JPG 仍一次曝光。
- 不从无脸推断风景；未明确模式、场景或失效主体确认保留待确认原因。质量标签与主体确认是两种独立输入。
- 全清单进入统计：逐项 success、failed、missing、unsupported、provenance_insufficient、needs_confirmation、cancelled、not_run 保留在 batch-status/diagnostics；没有转换结果不删样本。运行失败不是照片模糊。
- 清晰误弃/不可用误保留等沿用原统计公式，所有比例带分子分母；按来源/版本、人物风景、压力标签、场景族、场次和连拍分层。无标签只报告流程指标。
- 输出必须是仓库外全新目录，且不在已有工作区或列出的输入目录内。只在新隔离目录写缓存、预览、工作区锁和结果。原会话、设置、人工决定及分组不读取、不覆盖；入口不运行分组。
- 根 `complete.json` 最后写入，绑定 inputs/preflight/batch状态/冻结完成标记/全部导出字节；status=partial 代表逐项失败仍完整保留，不代表全成功。取消/写入失败无根完成标记，保留不完整审计目录，不覆盖或续跑旧批次。需新输出目录重跑。冻结自身仍沿用旧事务/锁，不能拿孤立文件当完整批次。

## 测量配置与主体资料

必须给 `real-sample-measurement-config-v1` JSON：`analysis_mode`（portrait/landscape）、`body_check` 布尔、`crop_settings` 五个既有参数 `scale_factor/shift_factor/offset_x_factor/aspect_ratio/detection_confidence`；可选 `asset_modes` 按 asset ID 明确覆盖模式。拒绝未知键或被生产解析器钳制的值。阈值、采样和评分公式仍由原生产版本决定，入口不提供新阈值调节器。

可选 `--confirmations` 是 `real-sample-subject-confirmations-v1`，包含 `manifest_sha256` 与 `entries: {asset_id: 既有完整每图CropSettings条目}`。条目必须已有 `primary_subject_confirmation`；调用原 `confirmed_selection` 校验当时源码版本、路径签名、参数、ROI与确认摘要，不调用确认写入函数、不重签。路径搬迁或确认过期时应回到原主体确认流程，不能拿质量标注补确认。

输入清单、路径映射、配置和确认文件在运行/发布前反复核对原始字节；收据对来源文件、有效参数、ROI和确认核对。新工具链另在导入时绑定各工具文件SHA-256及可靠变化指纹，发布前再核对，不把生产模块身份检查冒称覆盖工具脚本。变化保守失败，不静默重算/补签。旧缓存没有本次解码观察仍不能获得有效收据。

## 预览与成本

新增 `MeasurementRecorder(prepare_asset=...)` 的可选回调只供本入口显式启用；默认 None，原调用不变。回调位于本次解码观察内，在测量前用原 build_preview 生成隔离预览，前后验证来源/参数不变；外层 shared_decode 使预览和实际测量借用同一像素，避免为收据重复完整 RAW 解码。没有修改解码选择/采样逻辑。

`total_seconds` 是预检至最终报告写入前的墙钟；`measurement_seconds` 含预览准备、生产判断和其中收据观察；`receipt_cost` 是原观察器的哈希读取/字节/耗时、身份检查与观察成本，`preflight_cost` 另列预检读取。hash_seconds 已包含于 observation_seconds，measurement_seconds 已包含其内观察开销，不能重复相加。额外来源读取是内容哈希，不是额外完整解码。审计/标记写入、输出字节哈希与最终发布检查耗时不在已写入总耗时内，不声称微秒级端到端基准。

## Windows 可直接复跑

在验包后新的独立源码 worktree、干净 HEAD 和当地 Python 3.12 环境执行；`C:\Temp\评估 输入1`、`评估 结果1`、`盲标1` 均须尚不存在，不得指向照片目录或活跃工作区。下面生成的仅是明确标识合成棋盘，不是真实验收。

```powershell
$env:PYTHONPATH = 'src'
$py = '.\.venv\Scripts\python.exe'
& $py -X utf8 tools/real_sample_fixture.py --output 'C:\Temp\评估 输入1'
& $py -X utf8 tools/real_sample_evaluation.py --samples 'C:\Temp\评估 输入1\samples.json' --paths 'C:\Temp\评估 输入1\paths.json' --config 'C:\Temp\评估 输入1\config.json' --output 'C:\Temp\评估 结果1'
& $py -X utf8 tools/blind_annotation.py --samples 'C:\Temp\评估 输入1\samples.json' --paths 'C:\Temp\评估 输入1\paths.json' --output 'C:\Temp\盲标1' --annotator reviewer-A
# 保存后关闭再续标；提交标注后通过界面导出到全新 annotations.json。
& $py -X utf8 tools/blind_annotation.py --samples 'C:\Temp\评估 输入1\samples.json' --paths 'C:\Temp\评估 输入1\paths.json' --output 'C:\Temp\盲标1' --annotator reviewer-A --resume
& $py -X utf8 tools/offline_evaluation.py evaluate --samples 'C:\Temp\评估 结果1\frozen\samples.json' --predictions 'C:\Temp\评估 结果1\export\predictions.json' --annotations 'C:\Temp\人工 annotations.json' --catalog 'C:\Temp\评估 结果1\export\catalog.json' --output 'C:\Temp\对照 报告1'
```

原 `measurement_receipts_demo.py` 保留原来单棋盘演示用途。正式入口不会自己生成数据或标签，只有独立 fixture 工具生成合成资料。

预期：3曝光均保留，全清单 n=3，原文件字节不变，收据实际 decoder_calls 存在，空白标注未产生准确率；盲标能适屏与原生100%查看、草稿保存、关闭重开、提交、独立第二人意见、显式裁决与无覆盖导出。未双标不得称专家共识。详见 [盲标说明](blind-annotation.md)。

## 分开验证与 Windows 待验

```powershell
& $py -X utf8 -W error -m pytest -q tests/test_evaluation_inputs.py tests/test_real_sample_evaluation.py tests/test_blind_annotation.py tests/test_offline_dataset_strata.py
& $py -X utf8 -W error -m pytest -q tests/test_measurement_receipts.py tests/test_measurement_capture_portraits.py tests/test_measurement_decode_observer.py tests/test_measurement_adversarial.py
& $py -X utf8 -W error -m pytest -q tests/test_measurement_receipt_export.py tests/test_frozen_prediction_export.py
& $py -X utf8 -W error -m pytest -q tests/test_offline_evaluation.py
$env:PYTHONWARNINGS = 'error'
& $py -X utf8 tools/check_dev.py --report 'C:\Temp\本轮 checks.json'
```

Windows 待验：中文/空格路径与严格UTF-8；本地NTFS变化时间；跨进程锁冲突；取消/写入拒绝/已有结果不可覆盖；Tk DPI、适屏/100%定位、反复保存重开和关闭取消；真实获准RAW预览/全解码及RAW+JPG不同来源；人工主体确认从真实旧工作流输入；预测隐藏与标注后对照。Linux合成或依赖桩不能替代这些。

本轮触及生产收据准备接口但不接GUI，应另列独立候选构建项目：Windows在同HEAD、固定实际依赖环境构建独立候选，自检和目标机验收后再决定；本轮不构建EXE、不解决打包来源身份、不替换程序。没有真实获准清单时真实样本验收留待Windows，真实准确率尚未测量。不推送、不合并、不发布。

### 实测发现的既有小图边界

640×480 合成高细节棋盘可能由原风景判断器给 keep，但原冻结导出因原生最短边不足512的证据充分性契约拒绝自动决定；入口保留为 provenance_insufficient/failed，全部曝光仍在分母。此生产建议与导出门槛分歧未在本轮修改。成功流程fixture使用640×640，并另保留小图失败证据；不得用换fixture冒称小图已获支持。
