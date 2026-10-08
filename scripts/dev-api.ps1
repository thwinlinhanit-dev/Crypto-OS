# Dev API server (foreground). Port comes from the environment, default 8000.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$port = if ($env:API_PORT) { $env:API_PORT } else { 8000 }
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port $port --reload
