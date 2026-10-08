param([switch]$UseExistingEnvironment)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Source = "dist\AI选片助手"
. "$PSScriptRoot/release_version.ps1"
$ReleaseName = "AI-Photo-Cull-$ReleaseVersion-Windows-x64-portable.zip"
$Output = Join-Path 'dist' $ReleaseName

# Always build from current source; an existing EXE may belong to an older commit.
& .\build_exe.ps1 -UseExistingEnvironment:$UseExistingEnvironment

if (Test-Path $Output) { Remove-Item $Output -Force }
Compress-Archive -Path $Source -DestinationPath $Output -CompressionLevel Optimal
Write-Host "便携包已生成：$Output"

# Hash with .NET so stripped-down Windows PowerShell installations also work.
$ArchiveStream = [System.IO.File]::OpenRead((Join-Path $PSScriptRoot $Output))
$Hasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $ArchiveHash = [System.BitConverter]::ToString($Hasher.ComputeHash($ArchiveStream)).Replace('-', '').ToLowerInvariant()
} finally {
    $ArchiveStream.Dispose()
    $Hasher.Dispose()
}
$UpdateInfo = @{version=$ReleaseVersion; build=$ReleaseBuild; url="https://github.com/moyansang/photo-cull-assistant/releases/download/$ReleaseVersion/$ReleaseName"; sha256=$ArchiveHash; size=(Get-Item -LiteralPath $Output).Length}
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot 'dist/update.json'), ($UpdateInfo | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
