# Run the API and the web shell together (Ctrl+C stops both).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$api = Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$root\scripts\dev-api.ps1"
) -PassThru -NoNewWindow

$web = $null
if (Test-Path "apps/web/package.json") {
    $web = Start-Process -FilePath "powershell" -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$root\scripts\dev-web.ps1"
    ) -PassThru -NoNewWindow
}

Write-Host "dev: api pid=$($api.Id)$(if ($web) { ' web pid=' + $web.Id }) - Ctrl+C to stop." -ForegroundColor Cyan
try {
    while ($true) {
        Start-Sleep -Seconds 1
        if ($api.HasExited) { break }
        if ($web -and $web.HasExited) { break }
    }
} finally {
    foreach ($proc in @($api, $web)) {
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
