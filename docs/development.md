# 开发与打包

[返回首页](../README.md)

推荐 Windows x64 + Python 3.12。源码运行与 EXE 用户数据分离，开发数据位于 `%LOCALAPPDATA%/AIPhotoCullAssistant/development/仓库编号/`。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[raw,dev]"
python -X utf8 -m pytest -q
python launcher.py
```

源码位于总目录的 `github-release/`；本地测试程序、工作区和维护资料放在源码上级总目录；测试程序路径为 `../本地测试/当前版本/AI选片助手/`，其 `workspaces` 可联接到维护目录下的 `本地测试/工作区/`。这些本机资料不进入源码 Git，旧机器绝对路径不可直接沿用。

当前 Git 范围与复现边界见 [Git 资源清单](git-scope.md)。NumPy 为直接运行依赖；Python/Tcl/Tk、Git、虚拟环境和安装的第三方包不进入源码仓库。

## 目录

| 目录 | 内容 |
|---|---|
| `src/ai_cull_assistant` | 桌面 UI、本地扫描、AI 与导出 |
| `tests` | 单元测试、UI 测试、Lua 插件模拟宿主测试 |
| `lightroom` | Lightroom Classic 插件与安装说明 |
| `body_models` | 基础身体/头部模型离线校验、显式修复脚本、来源与许可证 |
| `src/ai_cull_assistant/data/body_models` | 普通 Git 跟踪的两份固定基础 ONNX、随包许可证和来源 |
| `docs` | 使用、验证、版本与开发说明 |
| `build`、`dist` | 被 Git 忽略的构建缓存、发布产物 |

测试使用合成图片或隔离临时目录，不应直接修改真实工作区。真实图片测试需另行指定样本和工作区，不能把合成测试当作模型准确率证明。

Windows 开发测试使用 `-X utf8` 开启本次 Python 进程的 UTF-8 模式。部分测试通过未指定编码的 `Path.read_text()` 读取 UTF-8 JSON；当系统默认编码为 GBK、临时路径含中文时，会出现 `UnicodeDecodeError`。该参数不修改系统语言设置，也不跳过测试或改变断言。需要指定 `--basetemp` 时使用本轮新建的隔离目录，保留失败报告，勿指向照片或现用工作区。

已有 `.venv` 应先检查 `python --version`、模块导入位置、`python -m pip check` 和 Tk 启动；有效环境可以继续使用。`py -3.12` 不一定识别其他工具管理的 Python 3.12，找不到时先用 `py -0p` 核对已安装运行时，以核实后的 Python 3.12 绝对路径创建环境，不依赖默认 Python 版本。

## 发布 v1.5

版本唯一来源：`src/ai_cull_assistant/version.py`。Python 包动态读取它；PowerShell 发布脚本也读取它。内部 `1.5.0` 对应发布标签 `v1.5`，补丁版本如 `1.5.1` 对应 `v1.5.1`。

```powershell
# 每次会先重新构建 EXE，再生成一个标准名称的 ZIP。
.\make_portable_zip.ps1
# 也可只构建目录：.\build_exe.ps1
```

产物：`dist/AI-Photo-Cull-v1.5-Windows-x64-portable.zip` 和 `dist/update.json`。ZIP 包含程序依赖、模型、LR 插件和快速开始，不包含 API 配置、用户照片或工作区。首次构建需要联网安装依赖。两份人体模型随源码提供，构建执行 `python body_models/download.py --verify-only` 离线校验；缺失或损坏时失败，不自动下载。按需显式运行 `python body_models/download.py` 恢复固定版本。旧 `build/body_models` 缓存继续忽略，不再作为打包来源。

发布前：

1. 检查改动、测试、版本号和 Release 说明；当前依赖采用版本范围，尚不承诺不同机器位级可复现。
2. 构建后运行 `AI选片助手.exe --self-test <报告绝对路径>`，检查报告 `ok=true`。仅使用生成图片、不请求付费 API。
3. 核对程序清单、ZIP 与 `update.json` 的 SHA256/大小/版本；确认未混入用户数据。
4. 提交源码，创建对应 Git 标签，再上传 ZIP 与 `update.json`。同时上传 SHA256 校验文件方便人工核对。
5. 发布后验证附件、下载 URL 和 Latest；推送源码不等于发布 EXE。

GitHub Actions 在版本标签或手动触发时构建并自检，上传 workflow artifact；它本身不自动创建 Release。维护者需上传已验证产物。

## 第三方与贡献

请保留来源与许可证文件；[第三方说明](third-party.md)。未引入 GPU 后端。提出算法改进时应附可复现样本、旧/新结果与误判范围，不承诺无漏检。


## 换电脑继续开发

先读 [共享开发契约](development-contract.md) 与 [Dot／Windows 接续手册](development-handoff.md)。在该电脑已有源码仓库运行 `git status --short --branch`，检查未提交／未跟踪文件。把本地修改保存在仓库外备份或明确的本地提交中，再更新；不要把私人交接和工作区提交进 Git。

当前开发分支与旧远端 main 无共同祖先。首次使用精确单分支 bootstrap，后续按明确 base/head 接收增量；不要直接拉取旧 main。只有目标分支已被明确选定且具有共同历史时，才使用正常 fetch 与快进流程，例如：

```powershell
git fetch origin
git log -1 --oneline origin/<已确认的功能分支>
git merge --ff-only origin/<已确认的功能分支>
git log -1 --oneline
```

若快进失败，保留现场并比较本地与远端提交，按具体冲突处理；不要使用 `reset --hard`、`clean -fd` 或强制推送。Git 只同步源码与固定基础模型，不会同步用户照片、设置、工作区、私人交接或编译后的程序。

在目标电脑核验 Python 3.12 和当地 `.venv`，按本页安装开发/RAW 依赖；不要复制另一台电脑的虚拟环境。运行 `python body_models/download.py --verify-only`、`python -X utf8 -m pytest -q` 和 `python launcher.py --self-test <临时报告绝对路径>`。pytest 默认使用 Python 层输出捕获，以避开已复现的 Windows Tk 文件描述符捕获干扰，见 [Tk 测试说明](testing-tk.md)。

要更新该机本地测试程序，先核对实际程序目录和运行状态，备份程序文件，再按构建清单替换；保留 settings、workspaces、目录联接及全部用户数据。使用 `build_exe.ps1` 构建；仅已核验依赖齐全时使用 `-UseExistingEnvironment`。对构建及安装后的 EXE 都运行 `--self-test`。旧归档按正常选择工作区时恢复，不批量处理用户归档。

目录示例均相对源码位置；每台电脑的实际路径在本机维护说明中确认，不把共享文档改成某个 Windows 用户目录。构建或推送源码不等于发布 Release。


## 2026-10-13 开发分支附加验证

HEIC/HEIF 解码属于可选 `codecs` extra；运行 `python -m pip install -e ".[raw,codecs,dev]"` 配置开发环境。AVIF 依赖当前 Pillow 构建实际功能，扩展名可扫描不代表该构建可解码所有压缩变体。详见 `research-decode.md` 的本地实测、依赖维护与再分发限制。

GUI 测试需要真实 Tk 显示环境。无 DISPLAY 的云机器不能把 UI 失败算通过；Windows 更新器／文件属性测试还需 Windows。先运行 `tools/check_dev.py` 的明确快速范围；业务变更另跑对应测试，候选交付再做完整适用回归。记录失败、跳过及未执行项，不把快速检查当成完整回归。
