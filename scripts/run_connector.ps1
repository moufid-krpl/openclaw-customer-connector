$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$env:CONNECTOR_CONFIG_PATH = Join-Path $RepoRoot "config\config.json"
.\.venv\Scripts\python.exe -m app.main
