# 风景与相似分组：公开数据探索性实测（2026-10-14）

## 结论先行

- 已实际下载并校验官方 KonIQ OSF 的 60 张照片，共 31,674,349 字节；运行的是本仓库真实 `assess_asset_mode(mode='landscape')` 和 `assign_groups()`，没有模型替身、付费 API、训练或调阈值。
- **60 张是混合场景，只有 5 张经助手按图像内容筛为风景/室外景观（含建筑）**。这不是人工标注的风景子集，也不是随机样本或独立盲测。不能把“测了60张”说成“测了60张风景”。
- 5 张风景/景观都输出 keep，0 reject，0脸框；其官方人工 ACR 均分为 2.734–3.738/5。没有人工 K/D/U 可弃置标签，因此**误弃率、坏片检出率和可靠自动弃置验收均为证据不足**。全为 keep 不代表准确率100%。
- 5 张原图各施加 Gaussian blur(radius=3) 后，全部 review；程序没有可靠风景 reject 分支，严重模糊也只能待复核。衍生样本不是新增独立场景，也没有人工弃置真值。
- 可复现分组弱点：5 个原图来源族分别增加四边各裁去5%的版本，**5/5裁切版均与同族其余版本分组分离**，strict/standard/loose 均发生。这说明对本批裁切的鲁棒性不足，不等同于人工比较组的误拆分率。
- 另外的60张混合内容实测为59 keep、1 review、0 reject。54张可匹配官方人工评分；其中 ACR<2 的8张全部 keep。这是“keep不是整体质量高”的实际反例信号，**不能把这8张直接标成必须弃置或把8/8解释成风景漏检率**。
- 已有合成/流程回归63/63通过，独立保存 JUnit。没有修改任何生产实现、没有推送或提交。

## 版本与复现

代码基线：`fe0aaf3e0a0a0b15f47b70a2759e281a8195427d`。评估时相关源码SHA256、依赖、主预设保存在 `results/summary.json`。

环境：Python3.12.14、Pillow12.3.0、NumPy2.3.5、OpenCV4.14.0，Linux云电脑。采用已有独立环境，无安装或升级。数据解码为官方1024×768 JPG；这不是相机RAW原尺寸验证，也不是Windows验证。

复现（从仓库根目录）：

```
PYTHONPATH=src <python-with-dependencies> evaluations/landscape-20261014/evaluate.py --data <private-data-directory> --output <new-results-directory>
PYTHONPATH=src <python-with-dependencies> evaluations/landscape-20261014/mixed_scene_smoke.py <private-data-directory> <output.json>
PYTHONPATH=src <python-with-dependencies> -m pytest -q tests/test_analysis_modes.py tests/test_grouping.py tests/test_grouping_round2.py
```

所需私有数据目录包含 `images/`、`metadata.csv`、`scene_selection.json`。原图及衍生图均在仓库外，没有纳入提交。`input-manifest.json`包含官方逐图下载链接、公开SHA256与本次校验结果；`scene-selection.json`记录选入清单，复制为私有目录的`scene_selection.json`可复现。脚本验证原图运行前后哈希不变。`download_subset.py`从保存的官方OSF列表按前60条下载，单图限制3MB并核验SHA256。

## 数据来源、标签和许可边界

1. [KonIQ-10k 官方页面](https://database.mmsp-kn.de/koniq-10k-database.html)：10,073图，众包整体技术质量意见，官网说明供研究使用；[官方代码库元数据](https://github.com/subpic/koniq/blob/master/metadata/koniq10k_distributions_sets.csv)含文件名、5档评分分布、票数、MOS、SD、数据划分。照片来自官网列出的[官方OSF备用库](https://osf.io/hcsdy/)。仅作本地研究测试，不再分发图片，不把代码MIT许可当作照片许可。论文：Hosu et al., IEEE TIP 2020, 29:4041–4056。
2. [KonIQ++官方页](https://database.mmsp-kn.de/koniq-image-defects-database.html)额外提供人类选择模糊、伪影、颜色、对比度等缺陷的频率，适合将来补充。其官方CSV服务器本次返回TLS证书过期错误，未绕过证书。官方仓库名为 `old_label/Koniq++_old.csv` 的文件实际表头仍为基础MOS字段，本轮没有把它伪装成模糊标签；所有 `human_blur_annotation` 均为null。
3. [CID2013官方存档](https://zenodo.org/records/2647033)：474张实际图像（480次图像评价），有MOS和人工锐度等属性。许可允许研究等用途但须保留完整版权通知及来源，禁止擅自用于营销；官方图片整包608MB，本轮不作大包下载。
4. [SPAQ官方仓库](https://github.com/h4nwei/SPAQ)：11,125张，提供人工质量、sharpness等属性和明确的landscape、cityscape等多标签。**下一轮最适合先按官方landscape标签挑选，不必盲抽。**官方给Google Drive/Baidu数据和注释入口；当前README未给足明确照片再使用/再分发许可文字，正式下载前须核实随包许可或向作者确认，本轮未申请、未下载。
5. [CERTH官方](https://mklab.iti.gr/results/imageblur-sed2012/)有自然/人工模糊标签，授权科学评估用途。但本项目历史已接触CERTH/PIQ，不重新划分冒充新独立留出，本轮未用。

KonIQ本次读到的官方MOS元数据**没有场景类别字段**，`set`只是train/test划分。KonIQ++缺陷标签也不是场景标签。可先拿SPAQ小型注释文件按landscape标签、锐度区间做分层，冻结清单后只取相应少量图像；保留同场景/同拍摄序列族在同一划分。若继续KonIQ，需另做可审计内容筛选，或者找到作者提供且定义明确的场景元数据，不能拿模型自动分类冒充人工场景标签。

## 抽样与人工评分处理

下载官方OSF某页前60条，按服务器顺序，是便利抽样。之后按画面内容选择5张；筛选不参考软件输出。初始4张运行后补入第5张并重跑，因此整体只能叫探索性开发验证，不能叫冻结留出。其余55张全部保留在混合场景测试，没有因软件错误而丢弃。

官方代码库 `c1...c5` 是比例，故报告 `human_acr_mean=sum(i*c_i)`，5档原始评分量表。另保留原始 `MOS` 列，但不把该列0–100附近的数当作1–5量表。官方网页演示图展示值可能来自不同筛选版；未混合版本。60张中6张在已下载元数据没有精确文件名匹配，只报告程序输出，不补造评分（名单见混合场景JSON）。

风景内容筛选由助手完成，仅决定“是否属于本次景观范围”，不是人类清晰度标注。质量分来自原数据库的人类评分，不能直接推断拍摄意图、主要景物是否技术不可用、是否应弃置。5张共529个人类评分次数，不等于529个独立摄影样本。

## 真实5图结果

|文件|景观范围|人工ACR均分|票数|程序|clear tiles/16|
|---|---|---:|---:|---|---:|
|1674951001.jpg|洞穴，少量游客|2.734|109|keep|12|
|1679551869.jpg|海岸景观，前景围栏|3.500|104|keep|16|
|169779095.jpg|田野|3.490|104|keep|12|
|1708787959.jpg|桥与林地|3.156|109|keep|16|
|196734006.jpg|室外游乐园建筑|3.738|103|keep|13|

总推断时间约0.273s（这5张、此环境；不含下载/分组），读图成功5/5，原图字节未变。clear tile数与人工ACR的描述性Spearman约0.316，n=5极小、样本非随机，且tile数不是产品质量评分；不作显著性或泛化结论。

## 相似分组：真实原图上的合成衍生回归

主预设standard固定为dHash距离≤10、灰度块结构≥0.78、宽高比倍率≤1.15。strict=(8,0.86,1.08)，loose=(12,0.68,1.25)仅做敏感性描述，没有从三者中选最好结果宣称无偏。

- 身份代理集：5原图各加字节复制、JPEG quality90重压缩，共15图、105对。三预设均 TP15/FP0/FN0/TN90，5组。
- 扰动压力集：再各加Gaussian blur3和四边5%裁切，共25图、300对。三预设均 TP30/FP0/FN20/TN250，10组。代理pair precision=30/30，recall=30/50=60%，F1=75%。错误全为crop5拆分。
- 这里的truth只表示来源族，即已知某衍生图来自哪个原图。**不是人类认定的“应一起选片”比较组，不能声称人工分组准确率100%或召回率60%。**裁切版可能在人类实际规则下需另组，仍需裁决。
- 各asset刻意赋予间隔两年的时间，原图、复制和重压缩仍同组；时间不构成门槛。原图/副本/变体无用户的手工分组、AI决定、星级或用户工作区，不能据此关闭这些真实工作流验收。
- `results/grouping.json`保留每预设所有分组、代表ID、错误pair；`results/perturbations.json`保留每衍生风景输出及分块证据。

## 失败/风险案例与下一步

1. `169779095__crop5`对`169779095__original`：自然田野仅四边各剪5%即拆组。其余4族同样。值得研究多尺度/裁切鲁棒候选，但本轮不修改阈值或实现。
2. 混合场景`19502065.jpg`：官方ACR约1.167/5，程序clear tiles=3而keep；`185458280.jpg` ACR约1.459，clear tiles=5而keep。均为低照度演出，不是风景；现有“至少2块clear即keep”可能放行整体质量很差的图。分数低可能来自曝光/噪声等，不能据此直接认定失焦漏检。
3. 风景负样本和关键例外不足：雾、长曝光流水、故意虚化、原生严重失焦、新相机RAW等均未覆盖；没有可估计误弃/检出的D/K集合。按已有标注方案继续双人盲标/裁决，或先取SPAQ官方landscape+sharpness分层小样本。

可执行清单：`quality-labeling-queue.csv`的5张原图留空K/D/U/X、意图、证据充分性和两位标注者意见，**不向标注者显示软件输出或MOS**；`grouping-labeling-queue.csv`列出10个原图pair并留空same/different/ambiguous。使用源文件适屏+100%细节看图，先确认摄影用途及比较组定义，再填。裁切和模糊衍生另标为人工压力集，不加入独立场景数。

最终状态：真实读图/风景无脸框与合成身份回归通过；裁切一致性压力项失败；可靠风景弃置和真实人工分组精度证据不足。不能将本报告用于声称产品准确率已达标。


## 后续核验更新

SPAQ许可已在第二阶段找到官方Owner明确MIT答复，并取得官方注释及9张有Landscape标签原图完成实测。请以[SPAQ第二阶段报告](../spaq-20261014/REPORT.md)为最新许可/可得性结论；本页5张KonIQ结果的限制不变。
