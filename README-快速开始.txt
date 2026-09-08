AI 选片助手 v0.3.1 — Windows 64 位便携版

1. 完整解压 ZIP，保留 AI选片助手.exe 和 _internal 文件夹在一起。
2. 双击 AI选片助手.exe，无需安装 Python。
3. 选择照片文件夹和工作区，点击“扫描并生成联系表”。
4. 需要时编辑选片组，再重新生成联系表。
5. 将工作区 contact_sheets/main 中的联系表上传给 ChatGPT 选片。
6. 将“文件名,星级”格式的评级粘贴回程序，应用选片结果。
7. 在 Lightroom Classic 中执行“元数据 → 从文件读取元数据”。

自动技术粗筛只对可靠检测到的脸部判断明显虚焦/抖动。
弃置结果请查看 contact_sheets/rejected_review 并人工复核。
程序不删除原片；评级和弃置信息写入 XMP。操作重要照片前建议备份已有 XMP。
RAW 预览所需 rawpy/LibRaw 和人物检测数据已随程序打包。
当前不直接调用 ChatGPT API，仍需手动上传联系表并粘贴评级。

目录设置会在关闭程序时保存到 EXE 旁的 settings.json，下次启动自动恢复。
首次默认工作区为 EXE 旁的 工作区 文件夹，精选导出目录为 EXE 旁的 精选 文件夹。
更新版本时，可将旧版 settings.json 复制到新版 EXE 旁以保留设置。
