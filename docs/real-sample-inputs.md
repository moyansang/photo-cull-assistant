# 真实样本入口：显式清单、来源与路径预检

本入口沿用 `offline-evaluation-v1` 的 samples，不替换旧离线样本格式、不生成另一套评分或集合划分。旧 v1 数据仍可由原离线工具读取；进入新的实际测量/盲标流程时，需要补全**用户真实登记**的附加字段。缺失值是来源不足，不自动签许可、不将 AI/MOS/目录名写成人工标签。

## 已有字段及新增来源登记

保留原有 `asset_id/capture_id/variant_family_id/comparison_group_id/scene_family_id`、来源哈希、`person_ids`、明确 `scene_type`、`split` 和 `historical_exposure`。一行代表一次曝光，RAW+JPG 是这一行的不同 sources；不同拍摄的清晰/模糊参考对分别登记 capture_id，但共享明确的比较组和场景族。

顶层新增：

- `dataset_registry`：数组，每项 `dataset_id`、`dataset_version`、`source_url`、`license`、`attribution`、`authorization`、`authorization_confirmed`、`historical_exposure`。文本必须由保管者真实填写，`authorization_confirmed` 只有明确允许本次用途时才能为 true。网址可以是公开来源链接，私人自有样本可以用明确的 `local:` 来源说明，不泄漏路径或凭据。
- `registry_review`：非空的登记范围/历史核验说明。
- `split_registry`：必须明确提供，空数组只表示“未登记其他条目”，不是独立性证明。
- 每个样本显式登记 `session_id` 和 `burst_id`；无法适用可为 null，但不能由目录名猜测。
- 每个 source 增加 `dataset_id`、`dataset_version`、`original_file_id`、`original_sha256`、正整数 `width/height`、`preprocessing_history` 数组。原始 SHA-256 必须真有来源依据；MD5、缩略图哈希不可冒充。未处理使用空数组，此时 original_sha256 应与当前 sha256 相同。变换须保留原始哈希和处理历史。预检只保留申报尺寸，不以再次完整解码替代正式测量来源记录。

多数据集使用同一 samples 顶层评估清单 ID，逐 source 指回各自 dataset/version，许可与署名各自保留，不混成一个“统一许可”。既有离线统计和逐例 raw 保留这些附加字段。

## 私有路径映射

独立 UTF-8 JSON，`schema_version=real-sample-path-map-v1`、`manifest_sha256` 为完整 samples 规范 JSON 摘要。`bindings` 对象的 key 是 asset_id；每项：

- `primary_path`：明确绝对本地路径，必须等于该项 sources 中某个成员。
- `sources`：`source_id → 绝对本地路径`，必须与清单的 source IDs 完全一致，禁止多给未授权文件或漏掉成员。
- `jpeg_source_id`：明确的普通栅格成员 ID（PNG/TIFF 等合成或合法样本亦可走既有普通图像路径）。
- `raw_source_id`：明确的 RAW 成员 ID；没有则 null。

每个成员都必须明确填写 RAW 或普通图像角色，单 RAW 也必须填写 raw_source_id；两个成员必须明确填写 RAW 与 JPEG/JPG 角色（RAW+PNG/TIFF 配对不支持）；不通过同名、相近文件名或目录判断配对。不支持一曝光超过两个测量成员时，保留来源不足/未支持原因，不能偷偷丢弃成员。不同目录同名文件可正常登记，报告 `same_basename_members` 方便审查；映射始终依赖 ID 与完整路径。重复使用同一个实际路径拒绝整批。

路径映射与照片、人工标签留在用户隔离评估位置，不入 Git、增量包或公开报告。工具只打开绑定的文件，不扫描目录或自动搜索“可能对应”的 RAW/JPG。

## 预检结果与计数

`preflight(samples, path_map, config)` 返回完整 rows，状态为 `ready / missing / failed / unsupported / provenance_insufficient / needs_confirmation`，保留原始问题列表及哈希结果。文件缺失不从全清单中删除。非法清单身份、重复 asset/capture/source/hash、清单与映射摘要不一致、额外来源和跨集合泄漏是整体契约错误。

已有核心图像包、RAW rawpy、人物 YuNet 文件及启用身体检查所需既有模型在运行前检查，无自动安装/下载。逐例 asset_modes 明确覆盖全局模式。

只有登记 `authorization_confirmed=true` 才读取对应文件字节；来源许可未确认时不先“试读”。读取为只读顺序 SHA-256，不解码；前后 stat 及打开句柄身份不同即失败。正常结果核对申报当前哈希，并保留可 JSON 序列化的 fingerprint。Windows fingerprint 使用生产 image_decode 的原生变化时间与 stat 契约；无法取得可靠原生时间时明确拒绝该项，不退回未计数的完整内容哈希。Linux 使用对应文件身份/时间字段。正式测量仍须再次用测量时收据核验运行前后与冻结时状态，预检不能替旧缓存补签。

额外读取费用单列：`hash_attempts` 包含失败尝试，`hash_files/hash_bytes` 是成功完整哈希的文件数/字节数，`hash_seconds` 包含失败哈希尝试的耗时；`total_seconds` 含元数据和路径验证等全部预检时间，不能再与 hash_seconds 相加。失败尝试的部分字节数目前不计入 hash_bytes，报告不得称其为全部系统 I/O。

`unknown/mixed` 场景要求明确主体范围；portrait 清单不能由 landscape 配置强行转成风景。未检测到脸不是风景授权。本入口不生成主体确认，人工技术标签也不是主体确认。

## 集合与历史边界

复用旧工具的 asset/capture/variant/comparison/scene/person/session/burst/source-hash 防泄漏校验。额外核对已登记原始 SHA-256 与 dataset/version/original_file_id，防止缩放后的新字节哈希把同源变体跨集合隐藏。未知历史、未登记近重复、隐瞒人物或场景不能由工具自动发现。

包含历史 PIQ/CERTH/SPAQ 的来源登记只允许 development 诊断用途；改目录、重抽样或新版本名不恢复盲测独立性。工具不将任何运行宣布为独立盲测。旧参考对与相关变体应整体保留历史接触关系，不能只给处理后文件新 ID。

## 候选来源说明（不执行下载或接受协议）

- [RealBlur 官方仓库](https://github.com/rimchang/RealBlur)：官方说明数据采用 CC BY 4.0，并列出 RAW release。仍须按实际下载版本、文件 ID、许可/署名和本次用途逐项登记；参考对不能自动当作同一次曝光。
- [RawNIND DOI](https://doi.org/10.14428/DVN/DEQCIM)：本次未核验到数据落地页的具体版本许可，须由用户查验并登记。代码仓库的 GPL 许可或模型许可不能替代照片许可。
- [Open Images V7 官方下载说明](https://storage.googleapis.com/openimages/web/download_v7.html)：保留 ImageID、OriginalURL、OriginalLandingURL、License、Author 等原始登记。OriginalMD5 是 base64 编码 MD5，不是本工具 SHA-256；缩略图可能改变，须明确实际取得的文件/尺寸/Rotation/处理历史。类别、框标注和外部质量分不能直接当作技术清晰度真值。

本轮不下载整库、不替用户接受申请协议、不上传照片。Linux 合成文件与占位 RAW 字节测试只验证工程契约，不证明真实 RAW 解码、人物判断或真实准确率。Windows 原生路径、锁、RAW 和本地盲标交互另验；真实准确率仍为“尚未测量”。
