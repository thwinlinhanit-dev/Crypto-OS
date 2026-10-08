# Dev worker. No worker entry point exists until M1, so say so (PD-9: no
# pretending an unbuilt module is running).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path "apps/worker/__main__.py")) {
    Write-Host "NOT_IMPLEMENTED: apps/worker has no entry point yet (lands in M1)." -ForegroundColor Yellow
    exit 1
}
uv run python -m apps.worker
