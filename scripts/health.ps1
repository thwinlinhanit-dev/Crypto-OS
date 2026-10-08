# GET /v1/status and report the API's own view of the world.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$url = "http://127.0.0.1:8000/v1/status"
try {
    $status = Invoke-RestMethod -Uri $url -TimeoutSec 5
    $status | ConvertTo-Json -Depth 6
    if ($status.status -ne "ok") { exit 1 }
} catch {
    Write-Host "health: API unreachable at $url ($($_.Exception.Message))" -ForegroundColor Red
    exit 1
}
