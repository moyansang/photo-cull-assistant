# 第三方来源与许可

[返回首页](../README.md)

当前仓库没有覆盖全部自有代码的统一许可证文件；本说明不替代授权或各组件许可证。再分发时应核对依赖的具体版本和随包许可证。

| 组件 | 用途 | 本仓库的来源/许可记录 |
|---|---|---|
| OpenCV Zoo YuNet | 人脸检测与五点关键点 | [来源](../src/ai_cull_assistant/data/YUNET-SOURCE.txt)、[许可证](../src/ai_cull_assistant/data/YUNET-LICENSE.txt) |
| OpenCV Zoo MediaPipe person / pose | 头部补救定位与身体检查实验 | [固定来源](../body_models/SOURCE.txt)、[Apache 2.0](../body_models/LICENSE-APACHE-2.0.txt)、[下载哈希清单](../body_models/manifest.json) |
| rxi/json.lua | LR 插件解析 JSON | [带 MIT 声明的源码](../lightroom/PhotoCullAssistant.lrplugin/json.lua) |
| OpenCV、NumPy、Pillow | 图像读写与分析 | Python/便携包所用依赖的各自发行许可 |
| rawpy / LibRaw、ExifRead | RAW 解码、元数据 | 随依赖发行的许可证；具体相机兼容性取决于 LibRaw |
| Adobe Lightroom Classic SDK | 插件 API | [Adobe SDK](https://developer.adobe.com/lightroom-classic/)；不随包分发 Lightroom |

正文中的 Lightroom、Windows 和模型名称属于各自权利人。本项目并非这些厂商的官方产品。

文档组织参考了 [LocalSend](https://github.com/localsend/localsend) 的下载/使用/开发入口，以及 [Czkawka](https://github.com/qarmin/czkawka) 的独立使用说明与版本记录；未复制其代码或品牌素材。
