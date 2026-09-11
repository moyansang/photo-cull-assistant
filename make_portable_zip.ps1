$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Source = "dist\AI选片助手"
$Output = "dist\AI选片助手-v0.5.9-portable.zip"

if (-not (Test-Path "$Source\AI选片助手.exe")) {
    Write-Host "尚未构建 EXE，先运行 build_exe.ps1..."
    & .\build_exe.ps1
}

if (Test-Path $Output) { Remove-Item $Output -Force }
Compress-Archive -Path $Source -DestinationPath $Output -CompressionLevel Optimal
Write-Host "便携包已生成：$Output"

$ReleaseVersion = "v0.5.9"
$ReleaseName = "AI-Photo-Cull-$ReleaseVersion-Windows-x64-portable.zip"
$UpdateInfo = @{version=$ReleaseVersion; url="https://github.com/moyansang/photo-cull-assistant/releases/download/$ReleaseVersion/$ReleaseName"; sha256=(Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash.ToLower(); size=(Get-Item -LiteralPath $Output).Length}
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot 'dist/update.json'), ($UpdateInfo | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
