$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not (Test-Path ".\config\config.json")) {
    Copy-Item ".\config\config.example.json" ".\config\config.json"
    Write-Host "config/config.json created from example. Please edit it before first productive start."
}

py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
Write-Host "Setup completed. Start with scripts\\run_connector.ps1"
