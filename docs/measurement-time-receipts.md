# 测量时运行来源收据（显式评估模式）

本轮只增加记录与保守转换，不改算法、阈值、采样、主体选择、分组或人工决定。默认关闭；不会为了收据重扫旧工作区，也不会给历史缓存事后补签。真实准确率：**尚未测量**。

## 可运行入口与范围

当前支持同步 `screen_assets(..., evaluation_recorder=recorder)` 的显式、有界评估批次，以及 `MeasurementRecorder.freeze(...)`。现有未传入 recorder 的调用保持原行为。**GUI `ProcessingJob` 的并行线程和 GUI 保存流程未接入此开关**，不能把本轮报告解释为 GUI 测量已自动获得收据；也没有新增 GUI 选项。

从干净、已提交的本轮源码和当地 Python 3.12 图像依赖环境运行：

```bash
PYTHONPATH=src python tools/measurement_receipts_demo.py --output /tmp/measurement-demo-new
```

Windows PowerShell（换成仓库外尚不存在的目录）：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe tools/measurement_receipts_demo.py --output 'C:\Temp\measurement-demo-new'
```

示例生成一张棋盘 PNG，调用真实风景测量入口，冻结，再调用现有只读导出和离线评估工具；**不是手拼收据 JSON**。不下载模型、不调用 API、不读取用户照片。人工标注模板为空，所以报告不产生准确率。重复运行必须选择新目录，不覆盖旧结果。脏树或缺少可验证 Git 身份时示例明确拒绝，不能把当前 main 填作历史运行身份。

独立重跑后半段，无需再次读取或解码原片：

```bash
python tools/frozen_prediction_export.py \
  --session /tmp/measurement-demo-new/frozen/scan-session.json \
  --samples /tmp/measurement-demo-new/frozen/samples.json \
  --provenance /tmp/measurement-demo-new/frozen/measurement-receipt.json \
  --output /tmp/measurement-export-new
python tools/offline_evaluation.py evaluate \
  --samples /tmp/measurement-demo-new/frozen/samples.json \
  --predictions /tmp/measurement-export-new/predictions.json \
  --annotations /tmp/measurement-export-new/annotations.json \
  --catalog /tmp/measurement-export-new/catalog.json \
  --output /tmp/measurement-evaluation-new
```

正式评估调用者须提供已有授权的 samples 清单，以及明确的 `asset_id → primary_path / source_id → 成员路径` 绑定；不得猜测人工标签、来源授权、同一曝光成员或训练/验证划分。示例中的清单只代表合成像素。

## 显式真实清单与盲标入口

正式清单入口及隔离本地标注见 [批量评估流程](real-sample-evaluation.md)。原合成演示保留；新入口显式提供 `prepare_asset`，让原预览生成和同步测量共享本次解码观察与像素。默认 None，不改变旧调用或 GUI。

## 字段从哪里来

| 字段 | 实际来源与限制 |
| --- | --- |
| `schema_version` | 附加收据 `frozen-prediction-provenance-v2`；旧会话格式不替换。 |
| `run_id`、开始/测量/结束/冻结时间 | 本次 recorder 的 UUID 和 UTC 时钟；不是历史结果的补填日期。 |
| `code_identity`、`source_git` | 执行源码所在 Git checkout 的完整 HEAD、脏状态和源码文件哈希；另核对已加载 Python 函数与源码、文件变化时间不晚于 OS 进程启动时间，防止长驻解释器冒认新 checkout；不是 origin/main。缺少可验证身份、运行中变化或脏树不可当可验证生产构建。 |
| `dependency_snapshot` | 当地实际安装包版本和已加载的相关原生库版本、Python/平台；未安装项明确为空。**这不是已经建立的生产依赖锁**。离线评估旧字段名 `dependency_lock_sha256` 承载该记录哈希时，诊断仍明确标注实际快照。 |
| `settings_snapshot` | 原有质量判断所用参数白名单、逐照片覆盖值及 ROI/检测/脸/身体版本；不采集任意应用设置、凭据或完整请求。 |
| `confirmation_snapshot` | 测量当时经现有契约验证的完整人工主体确认，含区域、来源签名、参数与确认摘要；不是由检测候选生成确认。 |
| `state_before` / `state_after` | 测量前后成员内容哈希、含变化时间的文件身份、独立预览哈希、有效设置、确认、模式和身体检查状态；冻结前再次核验。变化会保留为失败/缺口，不签有效收据。 |
| `stages` | 实际 scorer 周围观察到的阶段、所用成员、解码证据、实际解码器调用及 RAW postprocess 参数、阶段前后设置与确认、解码选项、坐标/图像尺寸及区域。脸与身体各自记录，RAW 升级不借用 JPG 的来源。 |
| `evidence_sha256` / `focus_scope_sha256` | 本次结束后的实际证据与范围摘要，冻结副本必须一致；不改原证据。 |
| `cache_reuse`、状态与类别 | 区分新测量、缓存、局部重算、取消、失败及来源不完整；旧缓存没有完整来源时明确拒绝转换。 |
| `session_sha256` / `manifest_sha256` / `asset_sha256` | 冻结会话字节、清单规范化内容和对应保存资产的绑定；收据不把自身哈希写回自身。 |
| `cost` | 额外哈希读取次数、字节数、哈希耗时、身份核对/快照观察耗时以及额外解码计数（观察耗时包含哈希耗时，不相加；不含全部冻结写入）；不能把这些当总算法耗时或真实照片性能基准。 |

## 保存、只读与兼容

冻结沿用 `save_session` 的序列化、现有工作区操作锁和 `atomic_json`。冻结结果写入新的外部目录；`operation(workspace)` 仍可能在活跃工作区创建或使用 `.publication-operation.lock`，因此并非对活跃工作区零写入。原会话、设置、分组及人工决定不被覆盖。持锁冲突时拒绝冻结，不发布不完整结果。结果先写同级暂存目录，完成内容绑定后一次目录重命名发布；`complete.json` 绑定会话、清单和收据，避免循环哈希。失败不把暂存目录当完成。它保证进程可见性的完整发布，不声称实现新的跨文件断电持久化系统。

冻结技术副本不包含远程 AI 响应；原照片只读、原决定保持。收据包含当地路径和技术区域信息，仍须按当地评估数据保管，不应公开上传。导出仅读取指定冻结 JSON，不追随其中路径去读照片，也不重新测量；离线统计同样只消费冻结数据。

旧工作区可以继续打开，旧会话无需迁移。v1 转换的拒绝条件保留；只有完整 v2 测量时确认与独立身体来源才能扩展人工范围支持。自动身体范围、来源不明旧缓存、缺少完整确认、混合/变化证据继续保守拒绝。收据是可核验的一致性记录，不是第三方数字签名或对抗伪造的认证。

## 额外成本与未支持项

只对主动传入的批次做来源哈希；正常关闭时不做这些读取。测量前、后及冻结前有额外完整字节读取，预览若参与测量也需绑定；同阶段复用已有解码图像，不为收据额外完整解码。解码缓存若缺少本次实际解码配置记录也保守保留缺口，不伪造历史调用。大 RAW 的哈希 I/O 会增加耗时，具体以每次 `cost` 为准。输入在处理时变化、观察失败或无法确定身份时宁可报告缺口，不能自动重算来掩盖它。

未支持：GUI 并行采集、旧缓存补签、自动身体范围导出、无可信构建记录的打包 EXE 生产身份、不能取得可靠原生变化时间的 Windows 非本地 NTFS 来源、自动多脸 body_focus 组合，以及真实 RAW 解码/人物准确率验收。合成/依赖桩测试只证明代码路径和契约，不证明真实 RAW 或人像效果。

## Windows 交回审查与候选构建

本轮涉及生产 Python 调用点，需在独立 worktree 核对完整 BASE/HEAD 后：

1. 重跑新收据测试、适配测试、统计测试及原快速检查，记录同一干净 HEAD；核对示例输出、重开兼容和关闭开关前后结果一致。
2. 另测 Windows 路径/Unicode、文件锁、输入变化、取消和写入失败，确认旧工作区、人工确认、星级与分组不损坏。Linux 通过不等于 Windows 验收通过。
3. 将**独立候选构建**列为后续项目：固定当地实际构建环境与源码、生成候选包、记录依赖和构建身份，再验证目标机器；当前打包身份缺口不得伪装为已解决。
4. GUI 采集入口尚未接入，须另行明确范围后实施/验收。当前不自动推送、合并、发布、部署或替换现用程序。

参见：[冻结导出](frozen-prediction-export.md)、[离线评估](offline-evaluation.md)、[共享契约](development-contract.md)、[开发接续](development-handoff.md)。

## 分开运行验证

```bash
python -m pytest -q tests/test_measurement_receipts.py tests/test_measurement_capture_portraits.py tests/test_measurement_decode_observer.py tests/test_measurement_adversarial.py
python -m pytest -q tests/test_measurement_receipt_export.py tests/test_frozen_prediction_export.py
python -m pytest -q tests/test_offline_evaluation.py
python tools/check_dev.py --report /outside/check-dev.json
```

函数身份核对与时间保护是本地一致性检查，不是防恶意修改的数字证明。输入不受本工具排他锁定；最终发布前复核可检测常规并发修改，但不能宣称防范恶意时间戳压制、硬件故障或最后系统调用之间的对抗竞争。
