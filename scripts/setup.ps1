# M0 bootstrap: python env, .env, migrations, JS workspace.
# Runs on a clean clone; every step is reported, failures stop the script.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($name, [scriptblock]$block) {
    Write-Host "==> $name" -ForegroundColor Cyan
    & $block
    if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) {
        throw "step failed ($LASTEXITCODE): $name"
    }
}

Step "uv sync" { uv sync --all-groups }

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "!!  .env created from .env.example - set DATABASE_URL before running (PD-11/PD-8)." -ForegroundColor Yellow
}

Step "alembic upgrade head" { uv run alembic -c infra/migrations/alembic.ini upgrade head }

if (Test-Path "pnpm-workspace.yaml") {
    Step "pnpm install" { pnpm install }
}

Write-Host "setup: done - run 'make dev-api' or 'make check'." -ForegroundColor Green
