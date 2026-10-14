# SPAQ官方许可、注释及真实风景小样本验证

日期：2026-10-14。继KonIQ探索性测试后继续，只读研究与本地评估，无生产改动、申请、签约、API费用或提交。

## 结果

**已取得官方风景标签和人工锐度评分，并实际测试9张原尺寸风景照片：8 keep、1 review、0 reject。** 9张全部 `Landscape>0`，不依赖助手猜测内容类别。完整SPAQ风景集人工锐度的最低四分位落在本批4张，其中3 keep、1 review。当前保留建议仍会放行人类锐度评分较低的风景，值得优先继续标注/研究。

这是压缩包前段的便利样本，不是随机样本、独立留出或可靠弃置准确率。Sharpness是连续的人类感知锐度分数，不等于K/D/U可弃置真值。原图读取9/9成功、0脸框、原字节未改；总分析约2.402秒，不含下载/分组。

## 许可和可得性：已解决的准确结论

- [SPAQ官方仓库](https://github.com/h4nwei/SPAQ)给出11,125照片，以及MOS、属性与场景标签。
- **[官方数据集许可问题#29](https://github.com/h4nwei/SPAQ/issues/29)**：提问明确询问dataset license以及免费/商业使用；Owner `h4nwei` 于2023-09-20答复：“It is the MIT license.” 这是数据集许可的直接作者说明，不是从代码许可证推测照片许可。本次依此作本地验证；未发现须先签字或提出申请的要求。保留作者、论文与此许可说明。仍不自行给整包追加作者未写明的版权持有人文本。
- 原官方Google Drive链接本次404。[官方问题#38](https://github.com/h4nwei/SPAQ/issues/38)中Owner于2026-03-11明确指定[Hugging Face镜像spaq.tgz](https://huggingface.co/datasets/chaofengc/IQA-PyTorch-Datasets/blob/main/spaq.tgz)。镜像不是自行从不明站点找来的。
- 镜像整包34,841,101,349字节，超过本轮预算。没有下载整包。按HTTP Range读取，设置curl硬性`--max-filesize`，确认206的Content-Range。读取了1MiB初探、8MiB前段、180,000,000字节续段，总响应体189,437,184字节，低于200,000,000字节。前两次有重复前缀，合成后唯一前段188,388,608字节。
- 初始未设硬性大小上限的Range请求被安全审查阻止，没有执行整包下载；随后改为带显式硬上限的安全请求成功。没有绕过TLS、登录或访问权限。
- tar/gzip前缀中前三个注释文件完整，之后观察到68个图像头部，尾部图像不完整，不能说68张均完整下载。最终只提取9个有Landscape标签且字节完整的JPG，其余不落盘为图片。9图共23,957,684字节。
- 每个提取文件已记录SHA256，用于后续复现一致性；镜像没有提供逐图官方校验值，所以这不是与官方逐图SHA256比对。未下载全包，未验证其整包LFS SHA256。

引用：Fang, Y., Zhu, H., Zeng, Y., Ma, K., Wang, Z., “Perceptual Quality Assessment of Smartphone Photography”, CVPR2020, pp.3677–3686。

## 官方注释实读

从作者指定镜像直接解出的三个xlsx均11,126行（含表头）：

- Scene category labels：Image name、Animal、Cityscape、Human、Indoor scene、Landscape、Night scene、Plant、Still-life、Others。
- MOS and Image attribute scores：Image name、MOS、Brightness、Colorfulness、Contrast、Noisiness、Sharpness。
- EXIF_tags：设备拍摄属性，本轮只保存原文件，没有用它构造场景真值。

按文件名精确连接11,125行。`Landscape>0`共有1,488张，`Landscape>=0.5`为1,425张。这里只把正值当作存在该官方标签，不把0.5等值擅自解释成“半数人投票”。风景Sharpness全范围5.43–93，四分位点为39.9225、63.29、75.2175。

**可按官方landscape和sharpness分层抽样，技术上成立。** 但此作者指定镜像是顺序gzip，不能直接按注释随机定位JPG而省去中间数据。本轮取预算内前缀出现的所有Landscape>0图像（最多60张，实际9张），没有按软件结果删图或挑图。更大、均衡分层样本需要可按文件访问的作者认可镜像，或用户授权更大的整包下载预算；本轮未擅自扩量。

## 逐图结果

|图片|官方Landscape|官方Sharpness|官方MOS|建议|清晰块数/16|
|---|---:|---:|---:|---|---:|
|00570.jpg|0.5|54.84|60.95|keep|16|
|00825.jpg|1|18.38|30.13|keep|3|
|01517.jpg|1|81.33|82.38|keep|14|
|04120.jpg|1|75.27|76.45|keep|8|
|04290.jpg|1|75.20|69.64|keep|10|
|05673.jpg|1|37.38|48.71|keep|5|
|08425.jpg|0.5|70.00|77.67|keep|13|
|08862.jpg|1|16.14|35.63|review|1|
|09114.jpg|1|28.00|44.80|keep|12|

均为官方原尺寸JPG，约4.92–12.98MP，程序处理EXIF方向。未缩成缩略图做清晰度测试。与KonIQ1024×768测试分开。

按完整1,488图风景分布预先计算的Sharpness四分位分层：

- 第1四分位（<39.9225）：4图，3 keep/1 review。
- 第2（39.9225–63.29）：1图，1 keep。
- 第3（63.29–75.2175）：2图，2 keep。
- 第4（≥75.2175）：2图，2 keep。

清晰块数与人工Sharpness的描述性Spearman为0.6，n=9，区间和显著性未作推断；清晰块数不是产品星级。**不能把3/4低锐度图keep称作75%坏片漏检率**，没有拍摄意图、用途和人工K/D/U标签。

分组真实运行strict/standard/loose均产生9个单图组。这里没有同场景连拍或人工比较组真值，故分组precision/recall均未计算；不从“没有合并”推断没有误拆。上一阶段5%裁切弱点仍独立成立，本阶段不改变阈值。

## 风险案例与后续材料

- 00825.jpg：Sharpness18.38，3块clear就keep。
- 09114.jpg：Sharpness28.00，12块clear仍keep，说明分块规则与全图人类锐度感受可能不一致。此处只是风险定位，不替标注者裁定必弃。
- 08862.jpg：Sharpness16.14，只有1块clear，正确进入待复核（这里“正确”仅指按现有程序规则执行，不声称人类最终标签已确认）。

下一步优先让两位标注者独立核查上述低锐度原图的主要景物、100%细节、曝光/噪声与意图。`blind-labeling-queue.csv`留空K/D/U/X和理由；避免向标注者先展示程序结果或人类MOS/Sharpness。标签足够前不改成自动弃置，不把现有样本二次随机切分称独立留出。

## 复现与文件

基线`fe0aaf3e0a0a0b15f47b70a2759e281a8195427d`；源码SHA256与依赖详见`results/summary.json`。无生产源码修改。`input-manifest.json`记录来源、范围、提取图像、注释文件SHA256。照片/xlsx/前缀包保存在仓库外，不纳入Git或报告附件。

- `prepare_prefix.py`：只读本地已下载前缀，安全按固定文件名读取注释，筛选Landscape>0，限制60图；不使用tar任意路径解包。
- `evaluate_spaq.py`：真实生产风景与分组实现、原图哈希前后核验、逐图证据、Sharpness分层。
- `selected-annotations.json`：本次9图原始注释值。
- `results/predictions.json`：真实输出及分块证据。
- `results/summary.json`：汇总、分组成员、版本和哈希。

```
python prepare_prefix.py --prefix <bounded-prefix.tgz> --output <private-data-directory>
PYTHONPATH=src <python-with-app-dependencies> evaluations/spaq-20261014/evaluate_spaq.py --data <private-data-directory> --output <new-results-directory>
```

最终状态：许可核验、官方注释获取、9张真实风景测试完成；更大代表性样本和可靠弃置验收仍未完成。前阶段5张结果也不升级为泛化准确率。
