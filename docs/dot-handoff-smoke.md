# Dot Linux → Windows 交接 smoke

## 范围与共同基线

本轮仅新增此文档，用于验证同一 Git 历史上的跨平台增量交接。

- 共同基线 BASE：`1dabcc37992ef844fc6eef569f140798aa802018`
- 已核对远端 `main` 与 `baseline-20261020` 均指向上述完整提交；克隆时无基线后新增提交。
- 使用全新目录完整克隆，`git rev-parse --is-shallow-repository` 为 `false`。
- 当前功能分支为 `topic/dot-handoff-smoke`；未以旧归档引用为开发基线。
- 本文不预写自身提交 SHA；正式交付 HEAD、tree 与检查结果以提交后生成的交接清单和独立交付记录为准。

## Linux 实测环境与已验范围

验证日期：2026-10-20（UTC）。

- Debian GNU/Linux 13.6（trixie），Linux 6.18.55，x86_64，glibc 2.41。
- CPython 3.12.14；Git 2.52.0。
- 使用当地 Python 3.12 创建虚拟环境，仅按 `requirements-dev-checks.txt` 安装 pytest 9.1.1 及其传递依赖；已确认 pytest 从本次虚拟环境导入。
- 已读取共享开发契约、开发接续手册与 `handoff.py` 帮助；全新检出及适用父目录未发现 `AGENTS.md`。
- 基线实跑 `tools/check_dev.py`：611 通过、0 失败、0 错误、0 跳过；没有跳过原因。检查报告保存在仓库外。
- 该快速检查包含 Git 跟踪 Python 的语法检查、纯 AF 元数据契约及开发辅助工具测试，不是完整业务回归。
- 只读运行 `doctor.py --json --check-models`：8 个必需项通过、5 个必需项缺失、0 错误；4 个可选项缺失。退出码为 1，原因是最小快速检查环境未安装业务包（项目包、Pillow、NumPy、ttkbootstrap、OpenCV）；可选 RAW、ExifRead、HEIF 与 ExifTool 也未安装。
- 仓库既有四个固定模型均通过存在性与 SHA-256 检查；未另行下载、加载推理模型或安装完整业务环境。Tcl 探测未创建 Tk 窗口。

提交后须在干净工作树上再次运行 `tools/check_dev.py`，以该次实际报告生成增量包，再用现有 `handoff.py verify` 核验。正式报告与本地诊断留在仓库外；交接包仅保留工具规定的四个文件。

## Windows 接续步骤（Linux 第一程交付时的待办）

Linux 第一程交付时，Windows 本机验收及回传尚待执行。Windows 后续实测见下节；完整往返仍需 Dot 接收回程并验证。

1. 从独立交付记录取得完整 BASE、HEAD 和哈希，先核对收到的 ZIP 与解压出的原始文件。
2. 在拥有 BASE 历史的现有仓库运行 `handoff.py verify`；只导入全新的 `refs/remotes/handoff/...` 引用，不覆盖已有引用、本地分支或工作树。
3. 审查差异后，从该引用建立新的独立 worktree 及 Windows 功能分支，保留原工作区及其未提交内容。
4. 在 Windows 当地 Python 3.12 虚拟环境安装快速检查依赖，运行相同检查，实际记录平台、Python、通过／失败／跳过数量和原因。Linux 或 CI 的结果不能代替 Windows 本机实测。
5. 回程仅修改本 smoke 文档，补充真实 Windows 验证结果；创建本地提交，在干净提交上重跑检查。用现有 `handoff.py create` 生成增量包，回程 BASE 必须是本次收到的云端完整 HEAD。
6. 按接续手册验证并交付原始四文件包；不得重新 `git init`、混入无关历史、强制重置／清理或自动合并 `main`。

本轮不验收 GUI、DPI、快捷键、文件锁、Junction、真实 Lightroom、完整业务流程或算法准确率；`windows_application_acceptance` 保持 `pending`，`installed` 保持 `false`。不构建或替换 Windows 程序，不推送分支或标签，不发布 Release。

## Windows 本机接续实测（2026-10-20）

- 收到的 Linux HEAD / 本轮回程 BASE：`f9eb8416d82cde8f29240572860e8a16fe24fd9f`。
- ZIP、bundle、校验清单 SHA-256 与独立交付记录一致；包内恰为规定的四个文件。现有工具 verify 通过，实际增量只有本 smoke 文档。
- 已导入全新 handoff 引用，在独立 worktree 的 `topic/windows-dot-handoff-smoke-20261020-f9eb841` 分支接续；原工作区 main 保持 `1dabcc37992ef844fc6eef569f140798aa802018`。
- 实际环境：Windows 11（10.0.26300）、AMD64、CPython 3.12.10、Git 2.55.0.windows.3。
- 当地新建虚拟环境，仅安装 requirements-dev-checks.txt；pytest 9.1.1 的导入位置已核对属于本 worktree 的虚拟环境。
- 收到的 Linux HEAD 在本机快速检查：611 通过、0 失败、0 错误、0 跳过；271 个 Git 跟踪 Python 文件语法检查通过，检查期间工作树稳定且干净。
- doctor 退出 1：必需项 8 通过、5 缺失、0 错误；可选项 4 缺失。缺少项目包、Pillow、NumPy、ttkbootstrap、OpenCV，以及 RAW、ExifRead、HEIF、ExifTool；本轮未安装完整业务环境。四个固定模型 SHA-256 全部通过，没有下载或执行模型推理。
- 本轮仅更新此文档。提交后须在干净提交上重跑快速检查，回程包使用该次原始报告；实际回程 HEAD/tree 以包清单与独立交付记录为准，不在本文预写自身提交哈希。
- 未改算法、业务代码或生产依赖；未构建、启动或替换现用程序，未推送 GitHub 或发布 Release。
- Windows 接续检查已完成；回程交付后的 Linux 接收与同提交复跑仍待执行。完整往返尚未完成。GUI、DPI、快捷键、文件锁、Junction、真实 Lightroom 及完整业务流程仍未验收。
