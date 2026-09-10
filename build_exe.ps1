$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== AI选片助手 v0.4.11 Windows 便携版构建 ==="

# 优先 Python 3.12；没有则使用系统默认 py。
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "未找到 Windows Python Launcher (py.exe)。请先安装 Python 3.12，并勾选 Add Python to PATH / Install launcher。"
}

$PythonLauncher = "py"
$PythonArgs = @()
try {
    & py -3.12 -c "import sys; print(sys.version)" *> $null
    if ($LASTEXITCODE -eq 0) {
        $PythonArgs = @("-3.12")
    } else {
        Write-Host "未检测到 Python 3.12，使用默认 Python。"
    }
} catch {
    Write-Host "无法调用 py -3.12，使用默认 Python 启动器。"
}

if (-not (Test-Path .venv)) {
    & $PythonLauncher @PythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "创建构建环境失败" }
}

. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "更新构建依赖失败" }
pip install -e ".[raw]"
if ($LASTEXITCODE -ne 0) { throw "安装应用依赖失败" }
pip install "pyinstaller>=6.10"
if ($LASTEXITCODE -ne 0) { throw "安装 PyInstaller 失败" }

# PyInstaller handles replacement of its own output with --noconfirm.

pyinstaller `
    --noconfirm `
    --clean `
    --onedir `
    --noconsole `
    --name "AI选片助手" `
    --paths src `
    --add-data "src/ai_cull_assistant/data;ai_cull_assistant/data" `
    --collect-data cv2 `
    --collect-binaries cv2 `
    --collect-binaries rawpy `
    --hidden-import rawpy `
    --hidden-import exifread `
    launcher.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

Copy-Item "README-快速开始.txt" "dist\AI选片助手\README-快速开始.txt" -Force
Write-Host ""
Write-Host "构建完成：dist\AI选片助手\AI选片助手.exe"
Write-Host "可继续运行 .\make_portable_zip.ps1 生成便携 ZIP。"

Copy-Item -LiteralPath "lightroom" -Destination "dist/AI选片助手/lightroom" -Recurse -Force

python build_manifest.py "dist/AI选片助手"
if ($LASTEXITCODE -ne 0) { throw "程序清单生成失败" }
