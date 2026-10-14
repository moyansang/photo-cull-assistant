# 风景噪声支持门：保守实现与开发回归

日期：2026-10-14。基线：`70f73a99`。候选风景版本：`landscape-noise-guard-v1`。

## 结论与边界

已实现一个范围有限的保守修复：原规则认为 clear 的分块，若其细节能量主要可由估计噪声解释，就不再作为整图 keep 的支持。整图仍需至少两块支持，只能把原 keep 降为 review，不能把 review 升为 keep，也没有新增 reject。

- 14 张原图：原先 13 keep / 1 review，候选完全相同。
- 25 张已保存的原图/重复/裁切/JPEG/blur3 变体：原先 20 keep / 5 review，候选完全相同。
- 两个真实输入的独立 RGB Gaussian 加噪反事实：原先被提升的 45 个重复例全部从 keep 降为 review，覆盖 σ4–32 和 5 个种子。
- 有明确额外复核代价：原生稀疏岩壁和水面反射细节加 σ8/16 后，共 12 个重复例由 keep 降为 review。无加噪的五类天然细节均保留 keep；草、叶、铁丝网加同样噪声仍 keep。
- **并非噪声不变性：213 个合成阈值附近的加噪升级仍有 51 个通过；JPEG 压缩和空间相关噪声也能绕过本门。**
- 空闲 CPU 下，14 图分析时间的逐图中位数之和为 1.304 秒 → 1.497 秒，增加约 14.8% / 0.193 秒。不是完整扫描或解码耗时。

没有人工 K/D/U、独立场景留出集或用户用途标签，以上不能称为产品准确率、误弃率、已校准锐度或已完成可靠自动弃置。

## 实现

仅生产文件 `src/ai_cull_assistant/analysis_modes.py` 改动。原身体指标、原风景取样、人物算法、分组、星级、手工/AI 优先级及 UI 路径均未在本改动中调整。

1. 保留原生 4×4 取样，每格中心最多 384×384；不缩放、不放大、不新增人脸分析。
2. 仅对原 `_region_metrics` 判为 clear 的格子做支持检查，原始 region state 和数值完整保留。
3. 对灰度应用外积 `[1,-2,1] × [1,-2,1] / 6`。该核平方和为 1；忽略边缘两圈后，用绝对响应的中位数除以 0.67448975，得到 8 位灰度单位下的噪声 σ 估计。
4. 原 fine power 从 `laplacian_normalized × max(contrast², 0.0025)` 还原，严格与身体指标分母一致。
5. 原 Gaussian σ0.65 + 3×3 Laplacian 的噪声功率增益为 9.690651，估计噪声功率为 `9.690651 × (σ/255)²`。
6. 只有噪声功率份额小于 0.5，才保留该 clear 格子的支持。这等价于要求估计剩余信号功率大于噪声功率，即估计 SNR > 1。此工程支持门并未经过人标阈值校准。
7. 小于 32px 的辅助输入、非有限/无效能量返回 unsupported/uncertain；噪声支持不足不会生成 reject。

`clear_regions` 继续表示原规则的 clear 数；新增 `supported_clear_regions` 控制 keep。每个被检查格子有 `detail_support`，包括估计噪声 σ、份额及原因。原始噪声份额可大于 1，不做会掩盖估计局限的截断。

版本仅改变风景 evidence 与风景 `ScreeningResult.analysis_version`，人物 `VERSION='manual-modes-v1'` 保持原值。没有引入加载会话时重算或写回既有结果的流程。

开发中比较过额外 Gaussian、双边滤波及非局部均值。非局部均值在原诊断噪声强度上有帮助，但扩展到 σ24–32 后仍会升级，又增加开销，因此最终生产代码没有加入去噪变换。

## 真实来源与反事实结果

仅使用之前已取得的 5 张 KonIQ 景观及 9 张 SPAQ Landscape 图。输入来源/许可沿用 `evaluations/landscape-20261014`、`evaluations/spaq-20261014`，没有新下载、用户照片或远程 AI。所有变体仅在内存生成；脚本在结束时验证原始输入 SHA256 未变。

两个来源：SPAQ `08862.jpg`，以及 KonIQ 田野 `169779095__blur3.jpg`。σ为每个 RGB 通道、8 位像素单位；种子 20261014–20261018。

|输入|σ|基线 keep / 5|候选 keep / 5|
|---|---:|---:|---:|
|SPAQ08862|0|0|0|
|SPAQ08862|4|0|0|
|SPAQ08862|8|5|0|
|SPAQ08862|16|5|0|
|SPAQ08862|24|5|0|
|SPAQ08862|32|5|0|
|田野blur3|0|0|0|
|田野blur3|4|5|0|
|田野blur3|8|5|0|
|田野blur3|16|5|0|
|田野blur3|24|5|0|
|田野blur3|32|5|0|

共有 60 个运行记录，其中 10 个无噪声重复和 5 个原本已 review 的 SPAQ σ4 控制。45 个升级被阻止，不能把 45 个重复当作 45 个独立真实场景。

## 天然细纹理、稀疏细节与代价

将每个来源两处不同原生 256×256 裁块放入 1024×1024 平坦背景，只让两格包含景物细节，不改变像素尺度。来源与准确裁框写入 JSON。天然纹理来源：KonIQ 田野的草、SPAQ01517 的树叶、KonIQ1674951001 的岩壁、KonIQ1679551869 的铁丝网、SPAQ04290 的水面反射。

|类型|无加噪：基线→候选|σ8：基线→候选|σ16：基线→候选|
|---|---|---|---|
|草|keep→keep ×3|keep→keep ×3|keep→keep ×3|
|树叶|keep→keep ×3|keep→keep ×3|keep→keep ×3|
|岩壁|keep→keep ×3|keep→review ×3|keep→review ×3|
|铁丝网|keep→keep ×3|keep→keep ×3|keep→keep ×3|
|水面反射|keep→keep ×3|keep→review ×3|keep→review ×3|

种子 123/456/789；无噪声三个种子产生同一图，不是三张独立控制。无加噪的五类两格细节均保留，故没有通过提高最少 clear 格数来惩罚低覆盖构图。不过噪声较强、真实细节仍存在的岩壁/水面被转为复核，这是真实可见的代价，不应只展示阻止升级的收益。没有人工标签，不能把这 12 个重复直接称为错误降级或正确降级。

自动测试另覆盖两格 3/4/8/16px 棋盘格、高/低对比和加噪；这些原先 clear 的稀疏控制仍 keep。纯平坦、低纹理及很小图片仍 review，不以天空/水面缺少纹理判定坏片。

## 仍存在的失效模式

### 阈值附近的加噪升级

完整开发探针包括 225 个棋盘格/模糊变体，以及 315 个正弦纹理变体，详见脚本中的全部格长、模糊半径、周期、振幅、噪声及种子。213 个例子原本 review，加噪后基线 keep；候选阻止其中 162 个，**仍有 51 个 keep**。

例如 8px 棋盘格 blur3 + σ8，以及部分 12px 周期正弦在小噪声下，噪声能将原曲率/粗尺度指标推过临界点，但并未占据大多数 fine power。因此本门不承诺 review 输入加任何噪声都仍 review。各变体不继承原图人类质量分数。

### 非独立、压缩及有结构噪声

- 田野 blur3 加 RGB σ16，再以 JPEG 95 保存：3/3 基线 keep → review。
- 相同加噪图以 JPEG 50 或 75 保存：6/6 仍 keep。
- 将噪声用 Gaussian σ0.7 做空间相关，再归一到像素 σ16：3/3 仍 keep。

压缩、相关性会改变高通响应与原 fine 算子的噪声增益关系；条带、锐化、纹理状噪声也不在此独立 Gaussian 模型保证内。密集随机样式的真实纹理可能被高估为噪声。此门没有解决尺度变化、未采到主要景物或人类主体意图的问题，也不会把原先低 Sharpness 但 keep 的全部真实照片改判。

## 测试、耗时与可复现性

本分支聚焦命令：

```bash
PYTHONPATH=src python -m pytest -q tests/test_analysis_modes.py tests/test_landscape_noise_guard.py
```

结果：79 passed（15 个原测试 + 64 个新测试）。新增测试直接调用实际 `_region_metrics` 对受控脉冲测量噪声增益，防止未来滤波器改动与常数静默失配；同时验证 RGB/灰度噪声、多种子、稀疏细节、小数组无警告、NaN/Inf 不支持 keep、源图不变、原生 384px 上限及分离版本。

```bash
PYTHONPATH=src python evaluations/noise-fix-20261014/evaluate.py --koniq-data <KonIQ目录> --spaq-data <SPAQ目录> --output evaluations/noise-fix-20261014
PYTHONPATH=src python evaluations/noise-fix-20261014/evaluate_water.py --spaq-data <SPAQ目录> --output evaluations/noise-fix-20261014
PYTHONPATH=src python evaluations/noise-fix-20261014/evaluate_runtime.py --koniq-data <KonIQ目录> --spaq-data <SPAQ目录> --output evaluations/noise-fix-20261014
```

复放通过 git 载入固定 `70f73a99` 的真实生产函数，并逐例验证候选所有原 region state/指标与基线相同、没有 review→keep、没有 reject。主结果 687 条，水面补充 9 条；原始图像字节不写入输出。

最终空闲 CPU 计时先预热，再交替顺序运行 5 次，每张分别取中位数。已解码 PIL 图像在内存中，不包含文件解码、缩略图、UI、分组或整库扫描。逐图结果：

|来源|基线 ms|候选 ms|
|---|---:|---:|
|SPAQ00570|139.16|169.42|
|SPAQ00825|133.48|139.99|
|SPAQ01517|151.38|179.51|
|SPAQ04120|125.97|138.92|
|SPAQ04290|119.48|140.13|
|SPAQ05673|112.86|121.98|
|SPAQ08425|121.47|142.35|
|SPAQ08862|90.64|88.07|
|SPAQ09114|125.44|151.83|
|KonIQ1674951001|39.22|46.40|
|KonIQ1679551869|37.60|46.43|
|KonIQ169779095|37.30|44.48|
|KonIQ1708787959|36.06|46.82|
|KonIQ196734006|33.55|40.75|

逐图中位数和 1.303607s → 1.497081s，1.1484×。少数负耗时差属于测量波动，不声称优化了那张图。此前全量复放中的 1.1542× 是可能与其他任务并行的临时值，以 `runtime-isolated.json` 为最终参考。

交付：三个可复跑脚本、`results.json`、`water-results.json`、`runtime-isolated.json`、对应日志及本报告。结果保存基线/候选代码 SHA256、输入 SHA256、版本、每例原始指标和支持证据。没有提交、推送或发布。
