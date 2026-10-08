# Run the python test suite (the same command `make test` runs).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
uv run pytest @args
exit $LASTEXITCODE
