# 开发与打包

[返回首页](../README.md)

推荐 Windows x64 + Python 3.12。源码运行与 EXE 用户数据分离，开发数据位于 `%LOCALAPPDATA%/AIPhotoCullAssistant/development/仓库编号/`。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[raw,dev]"
python -m pytest -q
python launcher.py
```

## 目录

| 目录 | 内容 |
|---|---|
| `src/ai_cull_assistant` | 桌面 UI、本地扫描、AI 与导出 |
| `tests` | 单元测试、UI 测试、Lua 插件模拟宿主测试 |
| `lightroom` | Lightroom Classic 插件与安装说明 |
| `body_models` | 可选身体/头部模型下载器、校验清单和许可证 |
| `docs` | 使用、验证、版本与开发说明 |
| `build`、`dist` | 被 Git 忽略的构建缓存、发布产物 |

测试使用合成图片或隔离临时目录，不应直接修改真实工作区。真实图片测试需另行指定样本和工作区，不能把合成测试当作模型准确率证明。

## 发布 v1.5

版本唯一来源：`src/ai_cull_assistant/version.py`。Python 包动态读取它；PowerShell 发布脚本也读取它。内部 `1.5.0` 对应发布标签 `v1.5`，补丁版本如 `1.5.1` 对应 `v1.5.1`。

```powershell
# 每次会先重新构建 EXE，再生成一个标准名称的 ZIP。
.\make_portable_zip.ps1
# 也可只构建目录：.\build_exe.ps1
```

产物：`dist/AI-Photo-Cull-v1.5-Windows-x64-portable.zip` 和 `dist/update.json`。ZIP 包含程序依赖、模型、LR 插件和快速开始，不包含 API 配置、用户照片或工作区。构建需要联网安装依赖/按哈希下载模型；首次较慢，保留 `build/body_models` 可复用已校验模型。

发布前：

1. 检查改动、测试、版本号和 Release 说明；当前依赖采用版本范围，尚不承诺不同机器位级可复现。
2. 构建后运行 `AI选片助手.exe --self-test <报告绝对路径>`，检查报告 `ok=true`。仅使用生成图片、不请求付费 API。
3. 核对程序清单、ZIP 与 `update.json` 的 SHA256/大小/版本；确认未混入用户数据。
4. 提交源码，创建对应 Git 标签，再上传 ZIP 与 `update.json`。同时上传 SHA256 校验文件方便人工核对。
5. 发布后验证附件、下载 URL 和 Latest；推送源码不等于发布 EXE。

GitHub Actions 在版本标签或手动触发时构建并自检，上传 workflow artifact；它本身不自动创建 Release。维护者需上传已验证产物。

## 第三方与贡献

请保留来源与许可证文件；[第三方说明](third-party.md)。未引入 GPU 后端。提出算法改进时应附可复现样本、旧/新结果与误判范围，不承诺无漏检。
