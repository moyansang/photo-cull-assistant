# 离线评估与失败归因工具 v1

任务 C 的前置工具，遵循 [路线第 7、8 节](project-roadmap-20261019.md)、[标注方案](validation-labeling-plan-20261014.md) 和 [开发契约](development-contract.md)。本轮只有工具、空白模板和合成契约测试。**真实照片准确率尚未测量**，Windows 与真实样本验收另行进行。没有算法、阈值、采样、UI、工作区格式或生产依赖改动。

## 复用与边界

- 沿用标注方案的 `asset_id / variant_family_id / comparison_group_id / scene_family_id` 和 `K/D/U/X`，加 `capture_id` 明确同一次曝光。
- 读取已冻结的本地 `keep/reject/review` 建议，不调用判断器、解码器、AI、模型或付费服务，不读取原图和工作区，不从“未检测到脸”推断风景。
- 原因取自生产 `reason/reasons/*_reason(s)` 表达式的静态字面量，仅形成可识别词汇快照。`analysis_modes.py`、`face_focus.py`、`body_focus.py`、`primary_quality.py`、`screening.py`、`focus_protocol.py`、`body_pipeline.py`、`subject.py` 的源哈希和精确 Git 提交一并固定。没有创建或改写生产原因系统。动态码可能提取不到；未知码不等于错误码，原字符串始终保留并计数。
- 历史 `evaluations/landscape-20261014/evaluate.py` 混合运行模型、MOS 与衍生图；ORL 是身份代理实验。其输出不能直接成为本工具的技术真值。旧 CSV 队列表只有待标注字段，不是已完成标签。没有自动转换旧诊断集、审美分数或 AI 临时标签的入口。
- 工具只检查登记元数据和冻结文件的关系；不会验证原图字节与登记哈希相等，也不会发现未登记的近重复或隐瞒的历史接触。需要保管者另外核查。结果不自动证明独立性、通过门槛或允许生产启用。

## 文件与冻结契约

三份输入是 UTF-8 JSON 对象，`schema_version=offline-evaluation-v1`，分别为 `kind=samples/predictions/annotations`，各有 `rows` 数组。不是生产会话文件。模板在 [offline-evaluation-templates](offline-evaluation-templates/)，所有 `null` 都是待填值，**空模板有意不能直接通过冻结校验**。`row-templates.blank.json` 提供三类行，按需要复制到对应数组，不是第四份评测输入。

共同字段：`dataset_id`、`dataset_version`、`guideline_version`、`manifest_version` 必须非空且对应一致。每次改变清单、划分、来源选择或指南都要更新版本并重新冻结。

### 样本清单

- `evaluation_kind`：`synthetic / development / locked_regression / independent_holdout`。这是提供者的声明，工具不会认证盲测资格。合成演示固定为 synthetic。
- 每行必填：四层 ID、`asset_id / capture_id / sample_version / split / scene_type / source_authorization / historical_exposure / evaluation_source_id / sources / person_ids`。`scene_type=portrait/landscape/mixed/unknown` 必须事先明确，不能由人脸检测结果补写。
- `sources` 每项含唯一 `source_id`、64 位小写 `sha256` 和 `format`；模板另留尺寸、方向、相机、镜头、ISO。敏感路径不用放入清单。只记录必要的去标识人物 ID。
- 同曝光 RAW+JPG 在一行的 sources 中，提前选择唯一 `evaluation_source_id`。预测和人工标签都必须绑定这个来源与哈希，禁止挑有利来源或把两个文件当两张独立样本。本工具 v1 不估计 RAW/JPG 配对一致率，不做配对结果的自动合并。
- 重复 asset、capture、source ID 或源字节哈希拒绝出指标。相同曝光的不同命名也须登记同 capture ID；隐瞒别名无法靠元数据检查发现。
- `person_ids=[]` 表示未登记人物身份，不能解释为没有人。可补 `session_id / burst_id`。`exception_tags` 可用 `must_select / unique_usable / key_person / background_sharp_subject_blur / single_eye_blur / low_texture / denoise / halos / reflection` 等既定压力标签，逐类单列，不能让其被总体均值掩盖。
- `ai_temporary_labels` 可原样保留来源说明，完全不参与技术真值。

### 冻结预测

顶层：`prediction_version / source_git / dependency_lock_sha256 / configuration_sha256 / decode_configuration_sha256 / run_id / manifest_sha256`。`source_git` 必须完整 40 位提交；三个 provenance 哈希指向实际冻结的依赖版本清单、算法配置、解码/方向/色彩/锐化/缩放配置文件，保管在私有评测目录。工具验证哈希格式，**不会凭空验证这些外部文件或重跑环境**。合成演示的哈希明确为虚构占位，不代表真实依赖锁。

每行：`asset_id / sample_version / evaluated_source_id / input_sha256 / status / recommendation / reasons`。`status=ok/failed/skipped`。failed/skipped 必须有非空 `failure_reason`，即使残留 recommendation 也不计自动决定。ok 时未知 recommendation 进入 failure，原值保留。reasons 是原始字符串数组，不翻译、不改名、不把未知码丢弃。没有预测行就是 missing，不伪造 review。

没有替应用生成预测的自动适配器：请从明确版本的只读导出中映射本地技术建议，不要映射人工最终选择、远程 AI、星级或 `auto_rejected` 的混合覆盖值。仅有 `rejected=False` 不能区分 keep 和 review，因此不能强行转换。导出需要新的授权数据与来源版本，和本工具开发分开。

### 人工标签

顶层 `annotation_version / manifest_sha256`；每行绑定相同 asset/sample/source/hash，且 `label_source=human / annotator_id` 必填。

- `technical_label=clear/unusable/borderline/unjudgeable`，分别表示按冻结用途的技术清晰、技术不可用、边界、无法判断。未知字符串单列 UNKNOWN；null/空或没有人工行是 UNLABELED，原值保留。
- `culling_label=K/D/U/X` 沿用原指南：技术上不应弃置／明确可技术弃置／必须复核／不适用。X 必须有 exclusion_reason。K 不等同“全图清晰”，确认刻意效果可为 K。技术标签和 KDUX 两个完整矩阵分别报告，不能互相替换。
- `final_selection=keep/reject/undecided/null` 只表达人类最终选择，不反推技术标签。人工保留的艺术性模糊仍可技术 unusable；同组另一张更好也不是技术不可用。
- `evidence_adequacy=adequate/insufficient/unknown/null`；`regions` 数组区分 left_eye/right_eye/face_core/required_body/landscape_subject/texture，保留区域 ID、必要性、可见性、技术标签、充分性和理由。无法看到、像素不足、无法判断均应记录，不通过插值或模型建议补真值。
- `original_ratings` 和 `adjudication_reason` 可保存独立标注、分歧与裁决。额外字段在逐例 raw 中保留，v1 不计算标注者一致率，未双标不能声称专家共识。指南、用途和复核方式应先冻结，标注时不展示算法建议。

`manifest_sha256` 是样本清单规范 JSON 的 SHA-256，不是文件排版哈希：

```python
import json, hashlib
samples = json.load(open('samples.json', encoding='utf-8'))
frozen = hashlib.sha256(json.dumps(samples, sort_keys=True, ensure_ascii=False,
    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
# 将 frozen 写入 predictions.json 与 annotations.json 的 manifest_sha256。
```

每次样本变更须重新绑定；预测与标签的样本版本、来源选择和哈希仍要逐行检查。最终报告还记录四份输入**原始文件字节**哈希、工具 HEAD/tree/dirty、工具文件哈希和时间，方便复现。

## 集合与泄漏检查

不随机重划旧数据。`split_registry` 登记本轮以外已知样本，至少含 asset/capture/variant/comparison/scene/split，另带人物、场次、连拍和源哈希。工具在当前清单与 registry 的并集检查上述身份跨集合；未知历史必须另行核查，空 registry 不能证明没有泄漏。

跨集合重叠默认使输入无效，退出码 2，没有指标。只有清单内 `declared_overlaps` 按 `field / value / splits / reason` 逐项准确登记的历史重叠可生成诊断报告；所有重叠及其理由完整列出，并标 `non-independent: declared overlaps`、`independent_blind_test=false`。这只允许查看已知污染的回归材料，绝不恢复封存或盲测资格。虚构、不匹配的重叠声明也拒绝。对应 RAW/JPG 别名要通过同 capture ID 和源哈希登记，不能跨组藏掉。

即使没有登记重叠，工具仍不宣称独立盲测。独立保管、未见历史、近重复、场景边界、授权和冻结时点需要另行验收。源哈希、标签或 registry 不完整时如实说明；不要拿旧 PIQ/CERTH、八张诊断图或重新随机切分后的旧数据冒称独立集。

## 指标、分母与未测量

所有比例输出 numerator、denominator、value、population；零分母 `value=null`，不是 0 或 100%。

- 清晰误弃：人工 technical clear 且 reject / 所有人工 technical clear（含失败、缺失预测）。
- 不可用误保留：人工 technical unusable 且 keep / 所有人工 technical unusable（含失败、缺失）。
- 建议保留中不可用比例：unusable 且 keep / 全部 keep；建议弃置中清晰比例：clear 且 reject / 全部 reject。未知真值仍在分母，因此必须同时看 keep/reject 真值覆盖率及矩阵，不能把剩余项都当正确。
- 自动决定覆盖率：(keep+reject) / 全清单曝光；待确认率：review / 全清单曝光；运行失败率：failed/skipped/无效建议 / 全清单曝光；缺失预测率：missing / 全清单曝光。每层另有 run_status_counts（ok/failed/skipped/missing）及 run_status_denominator，failed 与 skipped 不隐藏在合并列中。
- 人工处理负担：(review+failure+missing) / 全清单曝光。unknown/unlabeled 技术真值仍参与以上流程指标。失败不是照片模糊，也不是自动正确。
- 另给 KDUX×决定矩阵及 K 误弃、D 放行、KDU 内非安全弃置、D 安全弃置精确率、弃置覆盖和 D 检出。KDU 指标排除 X/UNKNOWN/UNLABELED，但这些项保留在总矩阵，并不从全部曝光流程分母中删除。不可把 KDU 指标当全样本安全结论。

完整技术矩阵为 clear/unusable/borderline/unjudgeable/UNKNOWN/UNLABELED × keep/reject/review/failure/missing，零单元也保留。KDUX 同理。逐例同时记录人工原文、预测原文、最终选择、AI 临时字段、未知码、错误类别和原因。

分层包括集合、显式场景类别、scene family、comparison group、选定来源格式、证据充分性、历史接触和压力标签，各层带相同完整指标。scene-family 宏平均只平均定义良好的组比例，报告参与组数和总组数；不把连拍当独立试验。v1 没有置信区间、覆盖率风险曲线或统计显著性结论：需先约定方法、足够独立场景和多轮冻结预测。没有预设准入门槛就不判通过。

原因统计同时给原始 occurrence 数、每曝光去重 count、失败原因及各错误类别的原因曝光数。分布的全曝光分母明确列出；一张可有多个原因，各原因计数不能加起来当曝光总数。未输出原因的曝光不会凭空补码，逐例仍能定位。

本轮只用合成记录。`real_world_accuracy.value=null`、status=尚未测量。无人工真值时同样如此。工具不生成总体“准确率”，有真实人工标签时也只报告所提供样本上的风险与覆盖率，不能代替独立真实验收。

## 运行（仓库根目录，Python 3.12）

只用标准库；测试只用已有 pytest，无需生产依赖、模型、照片或网络。所有生成文件必须位于源码仓库外的新目录，已有输出不覆盖。

```bash
python tools/offline_evaluation.py synthetic-demo --output /transfer/offline-synthetic-demo
# 可重跑下面命令到另一个新目录，应得到相同计数与输入哈希；时间和工具元信息除外。
python tools/offline_evaluation.py evaluate \
  --samples /transfer/offline-synthetic-demo/samples.json \
  --predictions /transfer/offline-synthetic-demo/predictions.json \
  --annotations /transfer/offline-synthetic-demo/annotations.json \
  --catalog /transfer/offline-synthetic-demo/catalog.json \
  --output /transfer/offline-synthetic-repeat
# 对未来真实冻结预测，从其准确本地提交制作只读原因快照：
python tools/offline_evaluation.py catalog --source-ref <完整预测代码SHA> --output /transfer/reason-catalog.json
python -m pytest -q tests/test_offline_evaluation.py
python tools/check_dev.py --report /transfer/checks.json
```

正常输出 `summary.json` 与 `cases.jsonl`，退出码 0 表示数据契约有效且生成描述性报告，不代表算法达标。输入契约无效时退出 2；可解析的连接/版本校验错误写 `validation-errors.json`，metrics=null，不产生准确率。JSON语法、重复对象键、路径或文件I/O错误可能发生在报告目录创建前，错误写 stderr。保留原输入修正后输出到新目录。

合成报告固定 8 曝光，含一对 RAW+JPG、清晰误弃、不可用误保留、全流程 review/failed/missing、未知技术标签、无人工标签及未知原因码。它是算数契约示例，不是新照片数据或生产效果证据。

## 接续与仍缺条件

Linux 契约测试与原有快速检查分开记录；原 611 项通过不能代替新增统计测试。仅工具/文档/模板/测试入 Git，报告和合成输入在仓库外。按 [开发接续手册](development-handoff.md) 在干净提交上重新运行 check_dev 并生成同 BASE/HEAD 增量包；本工具新增测试报告另外随证据交付，不能篡改原快速检查选择冒称覆盖。

后续需要：授权且历史清楚的真实样本、用途与技术指南、必要区域及独立人工标注、完整拍摄组/人物/RAWJPG关系与集合登记、冻结预测与来源配置、预先约定错误风险/有效覆盖率及区间方法、原生 Windows 命令/路径验收和真实样本验收。真实 GUI、LR、解码全格式、Windows 打包及 EXE 安装均不在本轮，生产代码未改不触发重建。推送、合并、Release、部署、私人照片上传或付费 API 均未执行。
