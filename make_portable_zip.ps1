$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Source = "dist\AI选片助手"
$Output = "dist\AI选片助手-v0.4.10-portable.zip"

if (-not (Test-Path "$Source\AI选片助手.exe")) {
    Write-Host "尚未构建 EXE，先运行 build_exe.ps1..."
    & .\build_exe.ps1
}

if (Test-Path $Output) { Remove-Item $Output -Force }
Compress-Archive -Path $Source -DestinationPath $Output -CompressionLevel Optimal
Write-Host "便携包已生成：$Output"
