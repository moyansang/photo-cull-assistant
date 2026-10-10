# AI选片助手

在 Windows 上完成 RAW 人物照片初筛、AI 辅助选片，并将结果交给 Lightroom Classic 复核。

[![Windows 构建](https://github.com/moyansang/photo-cull-assistant/actions/workflows/build-windows.yml/badge.svg)](https://github.com/moyansang/photo-cull-assistant/actions/workflows/build-windows.yml)

**[下载 Windows x64 便携版](https://github.com/moyansang/photo-cull-assistant/releases/latest)** · [使用指南](docs/user-guide.md) · [常见问题](docs/faq.md) · [反馈问题](https://github.com/moyansang/photo-cull-assistant/issues/new/choose)

当前发布：**v1.6.0 · 构建 2**。本地扫描无需 API Key，便携包无需安装 Python。原照片保留在原目录；“弃置”是审核结果和 Lightroom 标记，不是删除原片。

## 它能做什么

- **本地初筛**：读取 RAW/JPG、检测人脸、辅助判断清晰度，并按拍摄时间和图像相似度整理连拍组。
- **人工修正**：拆分或合并组，调整人脸与裁切范围，只重新扫描修改过的照片。
- **人物辅助补框**：使用外观匹配，或可选的离线 SFace 人脸特征模式；积累已确认参考，保留确认、拒绝、跳过和恢复进度。
- **两种 AI 选片方式**：通过自选 API 提交，或生成联系表和提示词后手动上传大模型网页，回填评分与理由。
- **保存工作进度**：保留分组、审核和 AI 结果，支持停止续做、增量扫描及同一批照片换盘符后的重新定位。
- **连接 Lightroom Classic**：将星级、弃置、AI 理由与清晰度待确认状态导入 Catalog，在原图上做最终判断。

## 下载与运行

发布包面向 **Windows x64**，需要可写的解压目录。本地分析使用 CPU；RAW 格式支持取决于包内 LibRaw，尚未承诺覆盖所有相机。Lightroom Classic 只在使用 LR 插件时需要。

在 [最新 Release](https://github.com/moyansang/photo-cull-assistant/releases/latest) 的 **Assets** 中选择：

| 文件 | 用途 |
|---|---|
| `AI-Photo-Cull-v1.6.0-Windows-x64-portable.zip` | **普通用户下载这个**：完整程序、依赖、模型及 LR 插件 |
| `SHA256SUMS-v1.6.0.txt` | 核对便携包的 SHA256 |
| `update.json` | 软件更新器读取的元数据，无需手动打开 |
| `Source code (zip / tar.gz)` | 开发源码，不是可以直接运行的 EXE |

完整解压后运行 `AI选片助手.exe`，保留旁边的 `_internal`、`lightroom` 等目录，不要只复制 EXE。

已有用户可在软件中点击**检查更新**；更新器支持同一版本内的构建号升级。手动换版本时保留设置和工作区，详见[更新说明](docs/user-guide.md#安装与升级)。

## 五步开始选片

1. **选择照片和工作区**。工作区建议放在照片目录外，用来保存分析、缓存与审核进度。
2. **扫描并检查人脸**。调整分组，修正漏框或误框；修改后点击“重新扫描修改过的图片”。人物候选需要你确认和采用。
3. **按需复核清晰度**。可使用 API 复核待确认照片，也可留到 Lightroom 人工检查。“待确认”不会直接作为清晰照片进入正式 AI 选片。
4. **生成联系表并选片**。选择 API 提交，或在网页模式复制提示词、上传生成的图片，再粘贴模型回答。检查返回的评分、理由与弃置结果。
5. **导出并复核原图**。点击“导出到 LR”，在 Lightroom Classic 插件中导入结果，确认后再进行后续修图。

没有 API Key 也可以本地扫描、手动调整、导出已有筛查状态；网页选片由你自行提交材料。[完整工作流](docs/user-guide.md)

补框审核中可用 **Y** 确认、**X** 排除当前候选、**P** 暂时跳过；确认后仍需“采用选中候选”，不会自动改动原片。

## 数据与隐私

| 操作 | 数据去向 |
|---|---|
| 本地扫描、补框、本地人脸特征匹配 | 在本机处理，不向 AI 服务发送照片；人脸特征只在当轮任务内存中使用 |
| API 复核或选片 | 向你配置的服务发送所需图片细节、预览或联系表，以及提示词；可能产生费用 |
| 网页选片 | 由你向所选大模型网页上传生成材料 |
| 检查或下载更新 | 连接 GitHub 获取发布信息和程序包，不提交照片 |

API 接口使用支持图片输入的 **OpenAI-compatible Chat Completions** 协议；预设只是配置模板，模型能力和价格以服务提供方为准。API Key 使用当前 Windows 用户的凭据管理器，不写入工作区 JSON；换电脑需要重新配置。

工作区会保存处理记录、生成材料，以及相关 AI 请求图片、提示词和回答证据。不要公开整个工作区；分享日志或提交 Issue 前先移除照片、路径和其他私人信息。详见[API 与网页提交](docs/user-guide.md#api-与网页提交)。

## Lightroom Classic 集成

1. 在 Lightroom Classic 的**文件 → 增效工具管理器 → 添加**中，选择包内 `lightroom/PhotoCullAssistant.lrplugin` 文件夹。
2. 将原照片导入当前 Catalog，再通过**文件 → 增效工具附加功能 → 导入 AI选片助手结果**选择导出的 `lightroom_results.json`。
3. 核对匹配数量后应用，在元数据面板选择“AI选片助手”查看理由；待确认照片可在专用智能收藏夹中继续检查。

插件仅支持 **Lightroom Classic**，不支持云端 Lightroom。照片按完整路径匹配，RAW/JPG 分别处理；插件不直接编辑 `.lrcat` 或写入 XMP，也不覆盖调色，但 Lightroom 自身的自动写 XMP 设置仍会生效。

[插件安装与使用](lightroom/安装与使用.txt) · [路径、换电脑与工作区](docs/user-guide.md#更换电脑或移动硬盘盘符)

## 已知限制

- 软件提供辅助判断，不保证找全人脸、正确识别所有人物或准确区分全部清晰／模糊照片。最终请检查原图。
- 人脸特征模式和身体清晰度检查仍是实验功能。侧脸、小脸、浓妆、遮挡、同服装不同人等真实场景，不能由小规模公开人像评测推断可靠性。多参考候选始终需要人工审核。[人脸模式与验证边界](docs/face-identity-mode.md)
- 当前没有 GPU 后端；扫描速度取决于照片、CPU、内存与磁盘，不承诺固定提速比例。[性能说明](docs/scan-performance.md)
- 已有自动测试、EXE 自检和部分桌面检查，不等同于所有显示器 DPI、另一台实体电脑或真实 Lightroom Catalog 的完整验收。

## 从源码运行

推荐 **Windows x64、Python 3.12 和 Git**。开发使用独立虚拟环境，不覆盖便携版安装目录。

```powershell
git clone https://github.com/moyansang/photo-cull-assistant.git
cd photo-cull-assistant
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[raw,dev]"
.\.venv\Scripts\python.exe launcher.py
```

运行回归测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --capture=sys
```

生成 Windows 便携包：

```powershell
.\make_portable_zip.ps1
```

打包脚本会先构建 EXE，再生成 ZIP 和 `update.json`；首次构建需要联网安装依赖、准备并校验模型。已有完整打包环境时可使用 `-UseExistingEnvironment`。开发数据与便携版数据分离；源码同步用 Git，程序升级用 Release，两者不会自动互相同步。

[开发与打包说明](docs/development.md) · [构建工作流](.github/workflows/build-windows.yml)

## 文档与反馈

- [使用指南](docs/user-guide.md) / [常见问题](docs/faq.md)：详细操作、工作区与恢复。
- [更新日志](CHANGELOG.md) / [发布记录](https://github.com/moyansang/photo-cull-assistant/releases)：查看版本变化。
- [清晰度验证](docs/clarity-v2-validation.md) / [人物匹配说明](docs/face-identity-mode.md)：样本规模、方法和限制。
- [贡献说明](CONTRIBUTING.md)：提交问题时请注明版本及构建号、复现步骤、照片格式、预期与实际表现；不要上传密钥或私人照片。

## 许可证与致谢

**仓库目前尚未为整体软件提供统一许可证。** 源码公开不代表第三方可以按某个未声明的开源许可证使用或再分发；如需相关授权，请先联系维护者。

依赖和模型各自遵循原有许可：YuNet 使用 MIT，SFace 与相关 MediaPipe 模型使用 Apache-2.0，LR 插件中的 `json.lua` 使用 MIT。详见[第三方来源](docs/third-party.md)与[SFace 模型许可](src/ai_cull_assistant/data/SFACE-LICENSE.txt)。本项目不是 Microsoft、Adobe 或模型提供方的官方产品。
