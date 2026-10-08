# Regenerate the OpenAPI document and the typed TS client it produces.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

uv run python scripts/gen_openapi.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

pnpm --filter @cryptosos/client gen
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "gen:openapi done." -ForegroundColor Green
