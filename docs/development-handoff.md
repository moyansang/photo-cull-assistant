# Dot Linux ↔ Windows 开发接续

任务 A 的工具入口。目标是每轮传递有共同祖先的提交，保留双方历史，用 Git 审查与集成差异。生产算法、现用程序和在线仓库不在工具的自动操作范围内。

## 本轮基线与交付

本轮从 `6b7152c331c171bd30b3d9be1e8fa10b5c6672a1` 建立 `topic/development-handoff`。源码生产树仍对应 build18 的 `02c1052`；现用 build17 保持，build18 的用户验收单独进行。

当前开发分支与旧 `origin/main` 没有共同祖先，不能把旧远端 main 当增量包前置基线。接续包只取当前明确分支，不包含其他分支、标签或旧远端历史。首次包的完整 HEAD 与 bundle SHA-256 见本地交付记录，之后按每轮记录更新。

已实现：

- [共享契约](development-contract.md)：两端采用同一套范围、测试、交接和数据保护要求。
- [doctor.py](../tools/doctor.py)：只读环境／导入位置／模型诊断。
- [check_dev.py](../tools/check_dev.py)：统一快速检查与绑定 HEAD 的 JSON 报告。
- [handoff.py](../tools/handoff.py)：首次单分支完整包、后续增量包、独立验证与新引用接收。
- [dev-checks.yml](../.github/workflows/dev-checks.yml)：Windows／Ubuntu 双平台快速检查配置。

本机验证和实际云端接续是两个状态。CI 配置在本地写好不代表 GitHub 已运行；未推送时不会自动触发远端检查。

## 一次性初始化：新云目录保留原 Git 身份

使用完整历史的精确单分支 bootstrap bundle。它包含该分支可达的源码历史与已固定模型；不包含其他 refs。完整包只在首次初始化或确实缺失前置历史时使用。

传输前审查当前分支历史的文件与内容。工具检查已知私人路径、非法路径、链接和大小写／Unicode 冲突；这不等于通用密钥扫描。自带哈希用于发现损坏，收件端仍应核对发件端另行给出的完整 HEAD 与 bundle 哈希。

不要把 shallow clone 导出的无基线 bundle 当完整包；它可能缺少父提交。`bootstrap` 会拒绝浅仓来源。不要把旧云端的无关历史合并进新基线。

云端接收流程（占位目录需替换；新源码目录必须尚不存在）：

```bash
# 在传输目录核对来源记录中的 SHA-256；不要只信包内的自述。
sha256sum /transfer/bootstrap/source.bundle
git clone --single-branch --no-tags --branch topic/development-handoff \
  /transfer/bootstrap/source.bundle /work/photo-cull
cd /work/photo-cull
git rev-parse HEAD
# 将 SHA 与本轮独立交付记录逐字比对，再继续。
python3 tools/handoff.py verify-bootstrap \
  --package /transfer/bootstrap --expected-head <本轮完整HEAD>
```

旧云目录如有未交付改动，保留原目录与分支；在新目录接续，不覆盖旧文件。本地包无需上传到 GitHub，可通过已有文件传输渠道送到 Dot。传输和云端执行需在对应环境完成，本地生成包不会替你建立云端连接。

普通远端日后获得推送授权时，可只推送选定分支并让两端拉取它。不要使用 `push --all`、`--mirror` 或依赖旧远端默认分支。工具没有自动配置或推送远端的功能。

## 各机环境：快速检查与完整开发分开

快速检查只需 Python 3.12、Git 和固定 pytest，不需要打开 GUI、加载模型或调用 API：

| 操作 | Linux | Windows PowerShell |
| --- | --- | --- |
| 创建当地环境 | `python3.12 -m venv .venv` | `py -3.12 -m venv .venv` |
| 安装快速检查依赖 | `.venv/bin/python -m pip install -r requirements-dev-checks.txt` | `.\.venv\Scripts\python.exe -m pip install -r requirements-dev-checks.txt` |
| 完整开发依赖（按需） | `.venv/bin/python -m pip install -e '.[raw,dev]'` | `.\.venv\Scripts\python.exe -m pip install -e ".[raw,dev]"` |
| 环境与模型诊断 | `.venv/bin/python tools/doctor.py --json --check-models` | `.\.venv\Scripts\python.exe tools/doctor.py --json --check-models` |

`doctor.py` 不安装依赖。仅安装 pytest 的最小环境会报告缺少业务依赖，这不阻止独立的快速检查；要运行应用或业务集成测试，需解决对应必需项。Tcl 诊断不创建窗口，也不证明显示服务、Windows DPI 或实际 GUI 已验收。

可选 RAW／HEIF／ExifTool 缺失与必需项失败分别显示。`--check-writable <现有临时目录>` 只在明确给定的目录写入并清除自己的探测文件。`--output <报告路径>` 显式保存环境报告，须选择源码仓库外尚不存在的文件；不读取设置或凭据，不自动下载模型。

## 每轮开发：一项功能，一条连续提交链

```text
共同基线 → Linux 功能分支与提交 → Linux 检查 → 增量交接
         → Windows 新引用／独立 worktree → Windows 检查与修正
         → 返回增量 → Linux 从已接收提交继续
```

开始时记录完整 base SHA。提交前检查暂存文件；有与任务无关的改动先保护，不批量纳入交接。清单生成要求当前分支干净，报告与该提交一致。

以下 PowerShell 示例从仓库根执行；`$deliveryRoot` 指仓库外已创建的交付目录，不能是照片或活跃工作区：

```powershell
$deliveryRoot = '<仓库外交付目录>'
$baseCommit = '<本轮共同基线完整SHA>'
$topicBranch = '<当前功能分支>'
.\.venv\Scripts\python.exe tools/check_dev.py --report "$deliveryRoot/checks.json"
if ($LASTEXITCODE -ne 0) { throw '开发检查未通过' }
.\.venv\Scripts\python.exe tools/handoff.py create `
  --base $baseCommit --branch $topicBranch `
  --report "$deliveryRoot/checks.json" --out "$deliveryRoot/increment" `
  --scope '本轮具体功能' --deferred 'Windows实际窗口验收待执行'
if ($LASTEXITCODE -ne 0) { throw '交接包未生成' }
```

Linux 使用同样参数，换为当地解释器，使用单行或 Bash 续行：

```bash
set -euo pipefail
.venv/bin/python tools/check_dev.py --report /transfer/checks.json
.venv/bin/python tools/handoff.py create \
  --base '<本轮共同基线完整SHA>' --branch 'topic/<功能>' \
  --report /transfer/checks.json --out /transfer/increment \
  --scope '本轮具体功能' --deferred 'Windows实际窗口验收待执行'
```

首次完整包将 `create --base ...` 换为 `bootstrap`，其他参数相同。只能从完整来源仓库生成。不要用 `HEAD~1` 猜测共同基线，也不要把检查时仍有改动的报告用于正式交接。

包内固定四个文件：

| 文件 | 用途 |
| --- | --- |
| `source.bundle` | 单一分支的 Git 对象；增量包只含共同基线之后的提交 |
| `handoff.json` | base/head/tree、范围、暂缓项、文件差异／哈希、依赖和模型摘要、平台及验收状态 |
| `checks.json` | 同一干净提交的快速检查记录，含实际通过／失败／跳过 |
| `SHA256SUMS.txt` | 前三个文件的哈希，避免自包含 |

不要往包目录塞额外文件；个人诊断、完整测试日志或说明放在独立目录。模型未改变时增量 bundle 不重复传模型文件。

## 接收：先导入新引用，再明确集成

从发件端独立记录取得 base 和 head；不要只从包内读取两者后原样传回校验。以下命令不会合并、切分支或更新工作树：

```powershell
$baseCommit = '<已约定完整base SHA>'
$headCommit = '<发件端记录的完整head SHA>'
$packagePath = '<收到的increment目录>'
.\.venv\Scripts\python.exe tools/handoff.py verify `
  --package $packagePath --expected-base $baseCommit --expected-head $headCommit
if ($LASTEXITCODE -ne 0) { throw '验证未通过，保留现场' }
.\.venv\Scripts\python.exe tools/handoff.py receive `
  --package $packagePath --expected-base $baseCommit --expected-head $headCommit `
  --ref refs/remotes/handoff/review-reasons-01
if ($LASTEXITCODE -ne 0) { throw '接收未完成' }
git diff --stat $baseCommit refs/remotes/handoff/review-reasons-01
```

接收引用只允许 `refs/remotes/handoff/...`。已存在且指向同一 HEAD 时返回已接收；指向其他提交则拒绝覆盖。`verify` 在临时 bare 仓库中检验对象，不更改接收仓库；`receive` 只增加对象和明确的新引用，不写 `FETCH_HEAD`、索引或本地分支。

审查后在新的 worktree 验收：

```powershell
git worktree add -b topic/windows-review <不存在的独立目录> refs/remotes/handoff/review-reasons-01
```

该 worktree 的业务依赖和测试按当地配置。Windows 修正提交到这条链，返回包的 base 设为收到的 head。集成基线未分叉时可明确执行 `git merge --ff-only <已验收引用>`；已分叉则保留两端分支，检查冲突后正常合并并重新验证合并树。工具不会替你选择集成方式。

如果接收机缺少 base，工具会失败。确认是否取错包／取错仓库；必要时补传精确分支的完整 bootstrap。不要用 `--allow-unrelated-histories` 或文件覆盖绕过。

## 快速检查与 CI 的实际范围

快速命令会编译 Git 跟踪的 Python 文件进行语法检查，不执行它们、不写 `.pyc`；运行明确列出的纯 AF 元数据契约和开发辅助工具测试。报告记录完整测试选择、Git 状态、平台、命令、耗时、通过数与跳过原因。运行期间源码状态变化会使报告失败。

这不是全量业务回归，也不证明算法准确率、解码完整性、GUI 或 Lightroom 可用。业务变更另加对应测试，Windows 候选包继续使用现有构建流程。

新增 CI 使用 Ubuntu／Windows 和 Python 3.12，读取权限，按 PR、功能分支推送或手动触发；不发布、推送或替换程序。测试报告作为检查产物。原 `build-windows.yml` 保留，仍负责完整候选构建。

## 常见失败的处理

| 提示／现象 | 处理 |
| --- | --- |
| dirty／存在未跟踪文件 | 审查范围，提交本任务或单独保护其他改动，再重新检查 |
| report HEAD/tree 不匹配 | 在当前已提交基线上重新跑快速检查，不能改报告里的 SHA 冒充重跑 |
| base 缺失／非祖先 | 检查实际仓库和交接链；首次使用 bootstrap，不重新建根历史 |
| 路径大小写冲突／链接／私人路径 | 在发件端查明；不在接收时静默重命名或丢弃文件 |
| Windows 实际窗口未测 | 保持待验收；Linux 的 headless 测试不代替它 |
| merge 不能快进 | 保留双方提交，处理真实冲突；不强制重置 |
| CI 尚无运行记录 | 本地配置不等于远端执行；用户授权推送后才会触发对应检查 |

共享规则变动需要同时更新契约及相应工具测试。本机路径与任务进展继续登记在 Git 外的管理文档。
