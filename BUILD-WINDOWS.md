# Windows EXE 构建说明（v0.3）

## 推荐发行形式

使用 **PyInstaller `--onedir` 便携目录**，而不是 `--onefile`。

原因：本项目包含 `rawpy/LibRaw`、OpenCV 和 Haar 人脸检测资源。`onedir` 启动更快，资源路径更稳定，也更方便排查 Windows Defender 误报。

## 最省事的方法

Windows 10/11 安装 Python 3.12（推荐）后，在项目目录双击：

```text
build_portable.bat
```

它会依次：

1. 创建 `.venv`
2. 安装项目、RW2/RAW 支持、OpenCV、PyInstaller
3. 生成 `dist\AI选片助手\AI选片助手.exe`
4. 生成 `dist\AI选片助手-v0.3-portable.zip`

最终把 ZIP 发到另一台 Windows 电脑，完整解压后双击 EXE 即可；目标电脑不需要 Python。

## PowerShell 单独执行

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
powershell -ExecutionPolicy Bypass -File .\make_portable_zip.ps1
```

## 为什么不能在 Linux/macOS 上直接生成 Windows EXE

PyInstaller 不是跨平台编译器。Windows EXE 应在 Windows 环境构建。最稳定的方式是在 Windows 本机、Windows VM，或 GitHub Actions 的 `windows-latest` runner 上构建。

## SmartScreen / Defender

没有代码签名的内部工具可能触发 SmartScreen。开发测试阶段可确认来源后手动放行；如果以后给大量用户分发，建议购买代码签名证书并给 EXE/安装器签名。

## RAW

构建脚本默认安装：

```text
.[raw]
```

因此包含 `rawpy + ExifRead`，用于 Panasonic RW2 等 RAW 预览/EXIF 读取。
