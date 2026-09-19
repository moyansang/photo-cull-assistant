$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Source = "dist\AI选片助手"
. "$PSScriptRoot/release_version.ps1"
$ReleaseName = "AI-Photo-Cull-$ReleaseVersion-Windows-x64-portable.zip"
$Output = Join-Path 'dist' $ReleaseName

# Always build from current source; an existing EXE may belong to an older commit.
& .\build_exe.ps1

if (Test-Path $Output) { Remove-Item $Output -Force }
Compress-Archive -Path $Source -DestinationPath $Output -CompressionLevel Optimal
Write-Host "便携包已生成：$Output"

$UpdateInfo = @{version=$ReleaseVersion; build=$ReleaseBuild; url="https://github.com/moyansang/photo-cull-assistant/releases/download/$ReleaseVersion/$ReleaseName"; sha256=(Get-FileHash -LiteralPath $Output -Algorithm SHA256).Hash.ToLower(); size=(Get-Item -LiteralPath $Output).Length}
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot 'dist/update.json'), ($UpdateInfo | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
