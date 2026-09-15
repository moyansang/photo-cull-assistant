# Read the application version without importing application dependencies.
$VersionText = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'src/ai_cull_assistant/version.py') -Raw
if ($VersionText -notmatch '(?m)^VERSION\s*=\s*[''"]([0-9]+\.[0-9]+\.[0-9]+)[''"]') {
    throw 'Cannot read application version'
}
$AppVersion = $Matches[1]
$ReleaseVersion = 'v' + ($AppVersion -replace '\.0$', '')
