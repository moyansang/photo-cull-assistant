4070 离线测试包（Windows x64）

1. 把整个 ZIP 解压到可写文件夹。不要只取出 python.exe，也不要在压缩包里运行。
2. 包内 photos 已包含原先测试的同一批80张 RW2，无需另外准备照片。
   若通过命令行指定其他照片目录，请使用包外的1～80张RW2，不要混入JPG。
3. 电脑接电，保持相同电源模式，关闭游戏等重负载程序。
4. 双击 START.bat，默认自动使用包内80张照片。
   自检后顺序执行 CPU、GPU单队列、GPU双队列，然后倒序再运行一次，共六次扫描。
   根据照片数量可能需要数分钟或更久，请不要同时启动第二份。
5. 完成后，results 的本次时间目录里有 summary.md 和 feedback.zip。
   把 feedback.zip 发回来即可。该包不含照片、预览和详细工作区。

自带 Python 3.12.10、依赖和模型；不需要另装 Python、CUDA Toolkit 或配置 API Key。
仍需已安装可用的 NVIDIA 显卡驱动。缺少原生 NVIDIA OpenCL 时会明确失败，不假装用了GPU。
若缺少运行库 DLL，记录完整错误交给 Codex 诊断；不要替换显卡驱动或修改系统设置来掩盖错误。
这是实验测试包，不是正式 v1.4 选片 EXE。不读取旧设置、不修改照片、不上传网络。
详细工作区留在 results 内，不要导入正式应用。本次测试不自动清理，也不更新正式软件。

命令行（在本目录中运行）：
  .\runtime\python.exe -X utf8 .\run.py --check
  .\runtime\python.exe -X utf8 .\run.py --input "D:\测试照片" --rounds 2 --workers 4

让另一台电脑的 GPT 执行：
使用能够运行本机命令的 Codex，在那台电脑上打开本测试包文件夹作为项目，
把 CODEX_PROMPT.txt 内容发给它即可。不要选择云端执行。
普通网页聊天若没有本机命令工具，不能直接使用你的4070；请自己运行 START.bat，
再上传 feedback.zip 让它分析。
官方桌面工作区说明：https://learn.chatgpt.com/docs/app

已知基准：GTX1650，原80张，最多4张在途，两轮CPU平均75.822秒，GPU1为74.627秒，
GPU2为74.085秒；实际收益很小。4070需实测，不预设哪种方案更快。
