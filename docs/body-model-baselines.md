# 基础模型与微调边界

本仓库保留两份未修改的 OpenCV Zoo MediaPipe ONNX 基础推理权重，用于离线运行、回归比较和未来实验的版本追溯。固定提交为 `47534e27c9851bb1128ccc0102f1145e27f23f98`，逐文件字节数、SHA256 和许可链接见 [manifest.json](../body_models/manifest.json)，文件位于 `src/ai_cull_assistant/data/body_models/`。

| 模型 | 上游转换说明 | 本仓库用途 |
|---|---|---|
| `person_detection_mediapipe_2023mar.onnx` | MediaPipe TFLite；上游描述 PINTO 下载或 `tflite2tensorflow` 转换、简化 | 人体锚点检测及头部补框 |
| `pose_estimation_mediapipe_2023mar.onnx` | MediaPipe TFLite 经 `tensorflow-onnx` 转换并简化 | 33 点姿态、身体掩码，用于实验性身体清晰度证据 |

两份上游目录均声明 Apache-2.0。本仓库未修改模型字节，保留随包许可证、来源和转换工具引用。基础模型变更必须同时更新固定来源、哈希、许可记录及推理回归，不应仅更换文件或禁用校验。

## ONNX 还不足以开始微调

2026-10-10 核对的官方资料中，OpenCV Zoo 对这两模型提供转换后的 ONNX、推理实现和演示。MediaPipe 的 Pose 模型清单提供检测与关键点 TFLite 推理模型；这些下载项不是对应的原始训练 checkpoint。当前核对的 MediaPipe Model Maker 视觉模块包括手势识别、图像分类、目标检测，未见 Pose Landmark 专用微调入口。这不等于证明所有历史版本或第三方实现都没有训练方案。

现有 ONNX 可保存基础权重和推理图，但不能据此承诺直接恢复原始训练流程。后续还需要确认兼容的可训练模型定义和权重导入方式、损失与训练代码、预处理和标签规范、获得许可的标注数据与独立验证集，以及训练后导出 ONNX 的数值一致性验证。如需继续原训练过程，还需要对应训练状态。当前不下载这些资源、不训练、不替换基础模型。

训练代码、配置、数据清单与实验摘要可以在审查后纳入 Git；原始照片、标注中的私人信息、大量 checkpoint 和训练输出应另行管理。预留的 `training-data/`、`training-checkpoints/`、`training-runs/` 已忽略，不自动创建。未来选择 Git LFS、对象存储或其他资源管理方式时，另行决定容量、许可和访问范围。

## 已核对的官方来源

- [OpenCV Zoo 人体检测说明（固定提交）](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/person_detection_mediapipe/README.md)
- [OpenCV Zoo 姿态说明（固定提交）](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/pose_estimation_mediapipe/README.md)
- [MediaPipe Pose 模型及模型卡清单](https://github.com/google-ai-edge/mediapipe/blob/master/docs/solutions/models.md#pose)
- [MediaPipe Pose 模型与推理说明](https://github.com/google-ai-edge/mediapipe/blob/master/docs/solutions/pose.md)
- [MediaPipe Model Maker 视觉模块](https://github.com/google-ai-edge/mediapipe/tree/master/mediapipe/model_maker/python/vision)
