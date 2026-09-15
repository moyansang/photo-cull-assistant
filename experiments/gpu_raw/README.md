# 内存式 GPU RAW 显影实验

独立原型，不被 `src/ai_cull_assistant` 导入，不进入 EXE，不改变正式扫描后端。
原始照片只读；样图、结果、运行依赖及实验工作区均位于忽略目录 `build/`。
实验工作区含不同算法的分析结果，**不要把它作为正式工作区导入主程序**。

## 实现边界

rawpy/LibRaw 负责 RW2 文件打开和原始像素解包。GPU 完成黑电平扣除、
相机白平衡、Malvar-He-Cutler（2004）去马赛克、相机色彩矩阵转换、
近似匹配现有输出的 BT.709 型伽马编码，输出 8 位 RGB。
方向变换和 PIL 图像构造留在 CPU，其时间已计入 GPU 路径端到端计时。
没有逐张启动外部程序，也没有通过 PNG 文件传递计算结果。
输出 JPG/PNG 仅供观察，发生在计时和清晰度分析之后。

OpenCL context/program/kernel 常驻；相同尺寸复用缓冲区，尺寸变化时释放旧缓冲区。
显存缓冲区预算不超过设备总显存的三分之一，单缓冲区不超过设备分配上限。
本轮仅允许原生 NVIDIA CUDA 平台的 OpenCL GPU，不选 OpenCLOn12 或 CPU 设备。
`Developer` 仅用于串行调用，不是线程安全的应用级调度器。

只支持方形像素、三色 RGBG、2×2 Bayer、统一黑电平、有效相机白平衡和色彩矩阵，
以及方向 0/3/5/6。其他传感器/布局明确报错，不能宣传为通用 RAW GPU 解码器。
与默认 rawpy AHD 显影不逐像素等价；没有实现 LibRaw 的全部校正、高光和相机特殊处理。
GPU/CPU 数值一致性测试指本原型 GPU 与同算法 CPU 参考实现，不代表与 rawpy 等价。

## 本地复现

使用项目现有虚拟环境中的 numpy、OpenCV、rawpy、Pillow 与 pytest。
新增依赖独立安装，避免改变应用依赖和构建环境：

```powershell
.venv/Scripts/python.exe -m pip install --no-deps `
  --target build/gpu-memory-prototype/deps -r experiments/gpu_raw/requirements.txt
$env:PYTHONPATH = (Join-Path (Get-Location) 'build/gpu-memory-prototype/deps')
$env:PYTHONUTF8 = '1'
.venv/Scripts/python.exe -m pytest experiments/gpu_raw/test_backend.py -q
```

小样本清单为 JSON 数组，每项包含 `path`（RAW 绝对路径）和 `label`（人工标签或未知）。
脚本最多接收 10 张唯一 RW2，输出目录必须不存在：

```powershell
.venv/Scripts/python.exe experiments/gpu_raw/benchmark.py `
  --samples build/gpu-memory-prototype/samples.json `
  --output build/gpu-memory-prototype/new-samples-run
```

CPU/GPU 使用同一次解包得到的像素，提前快照元数据并检查传感器缓冲区未被显影修改。
交替 CPU/GPU 显影次序；计时包含各自输出内存图像所需的工作，GPU 初始化单独报告。
保留 OpenCL 事件计时、形状、框位置、判定、指标和观察图。原片前后 SHA-256 校验。

完整扫描脚本最多接收 80 张 RW2，固定照片串行、身体检查关闭、无 API、新工作区。
只在脚本进程内临时替换原图加载函数，不修改应用文件或用户配置：

```powershell
.venv/Scripts/python.exe experiments/gpu_raw/benchmark_scan.py `
  --input D:/test-raw --output build/gpu-memory-prototype/new-cpu --backend cpu
.venv/Scripts/python.exe experiments/gpu_raw/benchmark_scan.py `
  --input D:/test-raw --output build/gpu-memory-prototype/new-gpu --backend gpu
```

完整扫描计时含新建任务、GPU 初始化和结果落盘；原片大小与修改时间前后核对。
两次均不重用应用缓存，但不清除 OS 磁盘缓存。GPU 异常不得冒充加速成功，
需检查每张诊断的 `experimental_raw_backend` 和判定中的源图错误。
这不是多照片并行或 GPU 生产环境回退测试。

## 2026-09-15 实测与接入决定

本机 Ryzen 7 5800H、GTX 1650 4 GiB（驱动 572.47）、约 16 GiB 内存。
9 项测试通过：四种 Bayer 排列逐像素对照独立 CPU 卷积参考，8 位输出最大差不超过 1；
显存复用、四种方向及非法参数检查通过。另已检查清楚样片和模糊人脸的 CPU/GPU 观察图，
没有发现明显色偏、旋转或裁切错位，但这不构成完整画质认证。

### 10 张小样本

选取用户分类目录中的 4 张清楚照片、3 张模糊照片、1 张待复核照片，
加上原 80 张集中的两张历史对比样本（这两张没有人工清晰度真值）。

| 指标 | 结果 |
|---|---:|
| CPU 显影并构造内存图像，平均 | 1.053 秒/张 |
| GPU 路径含上传、下载、方向和图像构造，平均 | 0.299 秒/张 |
| GPU 初始化（单独列出） | 1.294 秒 |
| 最大显式 GPU 缓冲区 | 216,720,660 字节，约 207 MiB |
| 尺寸和裁切框一致 | 10/10 |
| 判定一致 | 10/10，其中 1 张两边均无可靠人脸 |

3 张人工标注模糊的照片在两种后端下均为待确认，没有被 GPU 新增放行为清楚。
这不是“所有模糊照片都能自动弃置”的验证。本轮样本也未覆盖明确自动弃置的回归案例。
GPU 内核事件累计只占部分端到端时间，不能拿单个 kernel 时间替代上述 0.299 秒。
207 MiB 仅指程序分配的这些缓冲区，**不是驱动及其他软件在内的显存峰值**。

### 80 张完整串行扫描

同一批原始照片，先 CPU 后 GPU，两个全新工作区。照片并行固定为 1，
RAW 内部线程维持默认，身体检查关闭。GPU 初始化计入完整时间。

| 指标 | CPU | GPU |
|---|---:|---:|
| 完整墙钟耗时 | 131.25 秒 | 71.40 秒 |
| 全尺寸原图处理阶段 | 96.2 秒 | 35.2 秒 |
| 照片数 | 80 | 80 |
| 全尺寸 RAW 处理次数 | 76 | 76 |
| 最终清楚照片数 | 56 | 60 |

本轮耗时缩短 **45.6%**，不是生产环境并行扫描或 RTX 4070 的速度结论。
GPU 日志确认 76 张真正走了 OpenCL；缓冲区只分配一套并复用。
分组、人脸有无及弃置标记一致，但以下 **4 张从待确认变成清楚**：

- P1089004.RW2
- P1089067.RW2
- P1089081.RW2
- P1089094.RW2

这些变化涉及清晰度门槛，不是显示层的小数误差。例如 P1089004 的归一化
Laplacian 从约 0.01201 增至 0.01352，跨过当前判定边界。
四张缺少人工真值，不能认定 GPU 或 CPU 哪方正确，不能自动批准这些新增清楚照片。

**决定：速度原型成功，但判定一致性门槛未通过，暂不接主程序或发布 EXE。**
后续需对独立人工标注样本验证 GPU 专用阈值或边界照片 CPU 复核策略，
同时增加后端缓存隔离、异常回退与多照片调度；不能只调到这四张重新匹配就宣布合格。

证据均在忽略目录 `build/gpu-memory-prototype/`：
`run1/report.json`（10 张）、`scan-cpu/report.json`、`scan-gpu/report.json`、
`decision-differences.json`。10 张原片前后 SHA-256 一致，80 张原片大小及修改时间一致。
不提交照片、观察图、实验工作区、依赖或 API 配置。

## 资料与许可

- [PyOpenCL 常驻 program/kernel 与事件接口](https://documen.tician.de/pyopencl/runtime_program.html)
- [MHC 过滤器参考实现（Colour Developers）](https://github.com/colour-science/colour-demosaicing/blob/develop/colour_demosaicing/bayer/demosaicing/malvar2004.py)
- [OpenCV CUDA 去马赛克接口及算法说明](https://docs.opencv.org/4.13.0/db/d8c/group__cudaimgproc__color.html)
- [LibRaw 色彩矩阵归一化原理](https://github.com/LibRaw/LibRaw/blob/master/src/utils/utils_dcraw.cpp)
- [LibRaw 白平衡、白电平缩放原理](https://github.com/LibRaw/LibRaw/blob/master/src/postprocessing/postprocessing_utils_dcrdefs.cpp)

MHC 过滤器系数和参考算法使用 Colour Developers 的公开实现作为资料，
保留其 BSD-3-Clause 许可于 `COLOUR-LICENSE.txt`。GPU 内核和测试是本项目重新实现。
LibRaw 通过已有 rawpy 依赖调用，未复制 LibRaw C++ 源码进内核；相机色彩转换按其数学原理实现。
运行库未提交 Git。后续分发若新增运行库，应一并保留对应组件许可。
