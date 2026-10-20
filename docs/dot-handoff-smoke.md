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

## Windows 本机验收与回传待执行

当前仅完成 Linux 第一程。Windows 本机验收及回传均待执行，尚不能宣称 Linux ↔ Windows 往返完成。

1. 从独立交付记录取得完整 BASE、HEAD 和哈希，先核对收到的 ZIP 与解压出的原始文件。
2. 在拥有 BASE 历史的现有仓库运行 `handoff.py verify`；只导入全新的 `refs/remotes/handoff/...` 引用，不覆盖已有引用、本地分支或工作树。
3. 审查差异后，从该引用建立新的独立 worktree 及 Windows 功能分支，保留原工作区及其未提交内容。
4. 在 Windows 当地 Python 3.12 虚拟环境安装快速检查依赖，运行相同检查，实际记录平台、Python、通过／失败／跳过数量和原因。Linux 或 CI 的结果不能代替 Windows 本机实测。
5. 回程仅修改本 smoke 文档，补充真实 Windows 验证结果；创建本地提交，在干净提交上重跑检查。用现有 `handoff.py create` 生成增量包，回程 BASE 必须是本次收到的云端完整 HEAD。
6. 按接续手册验证并交付原始四文件包；不得重新 `git init`、混入无关历史、强制重置／清理或自动合并 `main`。

本轮不验收 GUI、DPI、快捷键、文件锁、Junction、真实 Lightroom、完整业务流程或算法准确率；`windows_application_acceptance` 保持 `pending`，`installed` 保持 `false`。不构建或替换 Windows 程序，不推送分支或标签，不发布 Release。
