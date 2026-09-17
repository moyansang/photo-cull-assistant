# Windows EXE 构建

完整说明请见 [开发与打包](docs/development.md)。

在 Windows 上安装 Python 3.12 后，双击 `build_portable.bat`，或在项目目录运行：

```powershell
.\make_portable_zip.ps1
```

此入口会先构建一次 EXE，再生成便携 ZIP 和 `update.json`，产物位于 `dist/`。无需提前运行 `build_exe.ps1`；仅需要未压缩的程序目录时，才单独运行它。

版本号、依赖安装、自检及发布校验步骤统一维护在上述开发文档中。
