# GPU RAW 实测：darktable OpenCL 尚不适合接入

日期：2026-09-15。当前 CPU 扫描、缓存、清晰度阈值和界面不变。
没有加入只能勾选、却不能证明加速的 GPU 开关；没有发布新 EXE。

## 已验证的能力

- 本机：Ryzen 7 5800H、GTX 1650 4 GiB，驱动 572.47。
- 官方 darktable 5.6.1 Windows x64 运行库能读取测试 RW2。
- OpenCL 日志确认 `highlights`、`demosaic`、`flip`、`colorin`、
  `finalscale`、`colorout` 在 GPU 执行，事件成功完成。
- 这是部分 RAW 图像处理的 GPU 加速，不能称为 RW2 压缩数据全程 GPU 解码。
- 运行库文件总计 706,364,807 字节，约 674 MiB；未打入本项目安装包。

## 有效实测

4 张来自原 80 张 RAW 测试集：P1088994、P1088995、P1088996、P1088998。
每张复制到独立实验目录；不使用用户 XMP、工作区缓存、API 或身体检查。
使用同一预览检测人脸，再分别对各后端的完整图像运行现有清晰度判定。
逐一测试，非并行。程序结束后逐文件 SHA-256 校验原片未变。

| 照片 | 当前 rawpy | darktable CPU | darktable GPU |
|---|---:|---:|---:|
| P1088994 | 1.21 s | 20.38 s | 20.13 s |
| P1088995 | 1.27 s | 5.78 s | 5.71 s |
| P1088996 | 1.26 s | 5.72 s | 5.63 s |
| P1088998 | 1.29 s | 5.79 s | 5.57 s |
| 后三张平均 | 1.27 s | 5.76 s | 5.64 s |

计时边界：rawpy 为解码到内存图像；darktable 为启动 CLI 到完成全尺寸、
8 位、无压缩 PNG 输出。**这是候选集成方式的开销对比，不是 GPU 芯片算力比较**。
如正式集成 PNG 方案，还要读取 PNG，实际额外开销尚未计入。
第一张包含首次配置/内核准备，单独列出。OS 文件缓存没有清空。

四张输出方向和尺寸均一致（4008 × 6008），但像素及清晰度指标不同。
前三张判定不变；P1088998 在当前 rawpy 下为清楚，在 darktable CPU/GPU
下均变成待确认。这表明差异来自不同 RAW 显影管线，不能简单沿用旧阈值。
这 4 张不是新增人工标注的准确率验证集，不能用它们证明哪个判定正确。

有效证据在忽略目录 `build/gpu-raw-probe/comparison2/report.json` 及同目录日志。
首次 `comparison` 因 Windows 反斜杠被输出模板解释导致文件路径错误，
未取得完整质量对比，**不作为有效实测**；脚本已改为正斜杠路径。

## 可复现工具

`tools/benchmark_gpu_raw.py` 只用于实验，不改变运行时后端。
使用项目虚拟环境及独立安装的官方 darktable Windows 程序：

```powershell
.venv/Scripts/python.exe tools/benchmark_gpu_raw.py `
  --cli C:/path/to/darktable/bin/darktable-cli.exe `
  --input D:/test-raw `
  --output build/gpu-comparison-new `
  --count 4
```

输出目录必须不存在；最多 10 张。保留各后端时间、退出码、GPU 模块日志、
图像尺寸、像素差异、人脸判定及源文件完整性结果。单次子进程限时 180 秒。
GPU 是否真实使用以日志为准，不能仅凭显卡存在或开启 OpenCL 配置认定成功。
工具不会下载软件；darktable 运行库未提交 Git。

## 后续接入门槛

1. 研究常驻进程或内存接口，避免每张启动完整显影程序和中间 PNG 传递。
   本次没有验证这种实现，不能把本次 CLI 结果推广为 GPU 永远不适合。
2. 分别测 RAW 解包、去马赛克、色彩处理、传输，确定能消除的实际瓶颈。
3. 对人工标注的清楚/模糊 RAW 集验证并校准；后端及参数纳入缓存身份，
   CPU 和 GPU 结果不得混用。
4. 达标后再接主页面默认关闭、记忆设置的实验开关；限制 GPU 并发、
   显存不足/不支持时回退 CPU，并记录实际后端。
5. GTX 1650 和 RTX 4070 分别实测。本轮只测了 GTX 1650。

## 官方资料

- [darktable Windows/OpenCL 说明](https://www.darktable.org/about/faq/)
- [OpenCL 初始化及 CPU 回退](https://docs.darktable.org/usermanual/development/en/special-topics/opencl/activate-opencl/)
- [CLI 与输出格式参数](https://docs.darktable.org/usermanual/development/en/special-topics/program-invocation/darktable-cli/)
- [5.6.1 官方发布](https://github.com/darktable-org/darktable/releases/tag/release-5.6.1)

如果后续随 EXE 分发 darktable，需要按它的 GPL 及依赖许可证处理分发材料。
本轮仅在本地实验目录安装和调用，未修改用户的照片原目录。
