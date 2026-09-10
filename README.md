# AI选片助手 v0.5.1

Windows 便携选片工具：本地扫描照片、分组、生成联系表、修正人脸小窗；通过 Lightroom Classic 插件把评级和弃置状态写入 Catalog。

## 使用流程

1. 解压完整便携包，运行 `AI选片助手.exe`，选择照片文件夹和工作区。
2. 扫描照片，检查分组和人脸细节；扫描只生成技术建议，不生成可应用的弃置结果。
3. 点击“AI 选片与人工复核”，设置偏好，点击“新建全量初选”。软件生成带唯一照片编号的独立联系表快照。
4. 网页模式：选中批次 → 复制本批提示词 → 打开本批图片目录 → 将提示词及这些图片交给模型 → 粘贴完整 JSON 回答。不要混用旧主联系表。
5. API 模式：配置接口和支持图片的模型 → 开始未完成批次。提交前显示范围；失败停止，可继续、暂停或拆小本批重试。不会自动重复已完成批次。
6. 切换“人工复核”，查看 AI 理由和原图，指定最终星级、旗标，逐张“确认并保存”。未指定的字段保持 LR 现有值；“无标记”明确清除旗标。
7. “导出已确认结果”生成 `lightroom_results.json`，在 Lightroom Classic 插件中检查匹配数量后应用。全部照片始终保留原目录。
8. 可选：从 LR 插件“查看上次导入报告”复制 JSON，在 EXE “导入 LR 回执”，记录本次应用状态。

## API 模型预设

打开“配置 API”，选择预设、填写对应平台 API Key，测试并保存即可。高级设置可展开修改名称、地址、模型和超时等；也可选择“自定义”。原有配置保留。

| 预设 | 模型编号 | 默认接口前缀 |
|---|---|---|
| 阿里百炼·北京 / Qwen3-VL-Plus | `qwen3-vl-plus` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 阿里百炼·北京 / Qwen3-VL-Flash | `qwen3-vl-flash` | 同上 |
| 智谱 / GLM-4.6V-Flash | `glm-4.6v-flash` | `https://open.bigmodel.cn/api/paas/v4` |
| OpenAI / GPT-5.4 mini | `gpt-5.4-mini` | `https://api.openai.com/v1` |
| DeepSeek / Flash（支持图片） | `deepseek-flash` | `https://api.deepseek.com` |

- 预设仅填写接口参数；账户需具备服务权限及可用额度。百炼预设使用北京地域公共域名（官方仍支持），必须搭配北京地域 Key。其他地域/业务空间专属域名请用自定义。
- 首次保存千问某一型号后，另一个型号在密钥留空时可复用同地域已保存的 Key。不会跨服务商复用；修改服务地址需重新输入密钥。
- GPT 预设自动使用 `max_completion_tokens`；Qwen 和 DeepSeek 预设关闭思考模式，GPT 使用 `reasoning_effort=none`。默认超时 300 秒，输出上限 8192 tokens。GLM 使用服务默认思考设置。
- DeepSeek 预设使用官方当前支持图片的 `deepseek-flash`；旧实验视觉模型已由新 Flash 承接。单请求编码后不超过 48 MiB，超限提示拆小批次。
- 修改预设的地址或模型后按自定义配置保存，不再套用原预设专用参数。
- 2026-09-11 核实的官方资料：[百炼视觉](https://help.aliyun.com/zh/model-studio/vision)、[百炼地域与域名](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)、[智谱视觉](https://docs.bigmodel.cn/cn/guide/models/free/glm-4.6v-flash)、[OpenAI GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini)、[DeepSeek 视觉](https://api-docs.deepseek.com/zh-cn/guides/vision/)。

## AI 任务与 API

- AI 建议、人工最终结果、弃置建议、待复核事项分别记录。低星级不会自动设为弃置；只导出明确人工确认且未过时的字段。
- 回答严格校验任务、批次和照片唯一编号。重复、未知、缺失、错误类型均显示异常；有效部分保留，原始回答也保存。兼容严格的“文件名,星级”简化输入，同名歧义拒绝导入。
- 重新评审所选组、跨组精简候选会创建新任务；旧任务和 AI 回答历史保留，人工决定不被覆盖。跨组精简仍使用相同 JSON 星级格式，作用范围以每批图片为准。
- 默认批次尽量保持完整组，约 24 张照片；超大组独立一批。“拆小本批重试”创建更小的新任务，可能拆组，提示词会注明比较范围。
- 分组成员、照片文件或裁切改变会使相关组结果过时；旧快照不能继续提交或套用回答。创建新任务后仍需重新确认过时的人工结果。
- 重启后选择同一照片目录和工作区并扫描，即可恢复任务、偏好和人工决定。扫描不覆盖已保存的 AI 项目或上次 LR 导出文件。
- **当前 API 协议仅支持带图片的 OpenAI-compatible Chat Completions**。接口地址支持前缀（如 `https://服务域名/v1`）或完整 `/chat/completions` 地址；不代表支持所有模型厂商的原生协议。
- API 普通配置位于 EXE 旁 `ai-api-profiles.json`；密钥单独存入当前 Windows 用户的凭据管理器，不写入项目 JSON。复制便携包到另一用户/电脑需要重新输入密钥。
- “测试连接”发送合成的 1×1 图片，不发送用户照片。实际提交仅发送所选批次提示词和联系表；原片绝对路径留在本地。服务方可能对测试和评审计费。
- 单请求上限：20 张联系表、每图 20 MiB、总图 48 MiB。默认超时 120 秒，可配置；超时不能证明服务方未处理，请按需重试。
- 本地模拟网络、Windows 凭据读写和 EXE 流程均有验证；未使用真实付费模型验证选片质量。

## Lightroom 插件

完整便携包包含 `lightroom/PhotoCullAssistant.lrplugin` 和中文安装说明。

在 **Lightroom Classic → 文件 → 增效工具管理器 → 添加** 选择整个 `.lrplugin` 文件夹，启用后使用图库模块菜单 **文件 → 增效工具附加功能 → 导入 AI选片助手结果**。

- 使用完整原片路径匹配当前目录；不按文件名猜测，不处理虚拟副本。
- RAW、JPEG 分别匹配；没有作为独立照片导入的 JPEG 会报告未匹配。
- 只修改结果显式指定的 `rating` 和 `pickStatus`，不改调色、标题等元数据。
- 普通低星级不是弃置；评级与旗标独立。
- 人工只填星级时保持原旗标；需要撤销弃置请显式选择“无标记”。
- 每次导入前在 LR 目录内保存上次匹配报告及变更前状态（插件菜单可查看），成功应用后更新报告。找不到的路径跳过。
- 不执行图片导入、照片移动、删除或 XMP 操作，不直接编辑 `.lrcat` 文件。
- **如需照片目录没有 XMP，请关闭 LR 的“目录设置 → 元数据 → 自动将更改写入 XMP”。** Lightroom 自身或其他工具仍可能创建 XMP。
- 旧版已有 XMP 保留，不删除；新版本不再写入或依赖它们。
- 当前测试覆盖导出流程及 Lua 5.1 模拟目录调用；真实 Lightroom 菜单与目录写入需要宿主验证。

## 照片、分组与人脸细节

支持常见图片及 RAW 预览，RAW+JPEG 视为逻辑照片。按拍摄时间和画面变化分组，严格/标准/宽松可选；支持人工拆组、合组，工作区 groups.json 保存分组。重新自动分组会覆盖人工分组。

人脸小窗使用离线 YuNet；优先置信度，结合主体位置。背景图案、侧脸和遮挡仍可能导致误检或漏检：

- 点击蓝色候选框选脸，拖框补选漏检的人脸。
- 裁切范围、上下偏移、裁切比例逐张保存。
- 置信度全局生效；手动框不受阈值影响。
- “下一张未标记”循环查找待补选照片，跳过已手动隐藏的小窗。
- 可隐藏本张小窗、恢复自动选脸；点击保存后重新生成联系表，取消则放弃本次修改。
- 修改按原照片绝对路径关联，移动原照片后需重新设置。

技术筛选只建议弃置明显人物主体虚焦/严重抖动，不能可靠确定主体时保守保留。建议查看 `contact_sheets/rejected_review` 后人工确认是否弃置。

## 目录与设置记忆

默认工作区是 EXE 所在文件夹。照片目录、工作区、分组灵敏度、每页照片数、列数、技术筛选开关、人脸设置保存在 EXE 旁 `settings.json`。升级可复制此文件。

工作区主要输出：

- `previews/`：预览图。
- `contact_sheets/main/`：主联系表。
- `contact_sheets/rejected_review/`：疑似技术问题照片。
- `groups.json`：分组。
- `screening_results.json`：技术筛选详情。
- `ai_project.json`：任务、AI 回答、人工决定和应用状态。
- `ai_tasks/`：每次任务的联系表快照与提示词。
- `lightroom_results.json`：最近一次人工确认导出的 LR 结果；重新导出会替换，扫描不替换。

## 开发与打包

Python 3.12 推荐。安装 `pip install -e ".[raw,dev]"`，运行 `python -m pytest -q`。测试包含 Lua 5.1 插件模拟宿主验证（lupa，仅测试依赖，不随 EXE 打包）。

运行 `build_exe.ps1` 构建包含 LR 插件的便携目录，运行 `make_portable_zip.ps1` 生成 ZIP。

SDK 参考： https://developer.adobe.com/lightroom-classic 及 Adobe SDK 文档镜像 https://lrc.mcor.dev/ 。json.lua 来源及 MIT 许可见 `lightroom/安装与使用.txt` 与库文件头。


## 自动更新
启动 EXE 后后台检查 GitHub 正式版。发现新版可选择下载更新；“不再自动检查更新”会记忆，手动“检查更新”仍可使用。
下载核对 GitHub SHA256，解压后按程序文件清单校验；退出当前 EXE 后只替换清单内程序文件并重启。设置、工作区、照片和用户新增文件不在替换范围内；同名程序文件被用户修改时中止更新。复制失败尝试恢复备份，不删除整个安装目录。
临时下载、旧程序备份与错误日志位于 Windows 临时目录 photo-cull-update-* 下。
v0.4.8 及更早版本没有更新器，首次需手动升级到 v0.4.9 或更新版本；此后可通过程序更新。

选片仅改变 Lightroom 中的星级与标记：不复制、移动或删除原照片。旧版生成的精选文件夹不会自动清理。

更新接口遇到 GitHub 限流或网络错误时，自动尝试 Releases 的 update.json 备用通道。成功检查缓存 6 小时；手动检查始终重新查询。发布新版本时须将 make_portable_zip.ps1 生成的 update.json 与 ZIP 一起上传。
