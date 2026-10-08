# Dev web shell (foreground).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "apps/web/package.json")) {
    Write-Host "NOT_IMPLEMENTED: apps/web has no app yet (M0 shell lands next)." -ForegroundColor Yellow
    exit 1
}
pnpm --filter web dev
