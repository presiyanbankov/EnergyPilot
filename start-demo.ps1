$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$demoPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $demoPython)) {
    throw 'Create the Python virtual environment and install requirements first; see README.md.'
}
if (-not (Test-Path -LiteralPath 'frontend/dist/energypilot/browser/index.html')) {
    throw 'Build the dashboard first: cd frontend; npm ci; npm run build. See README.md.'
}
Write-Host 'Open http://127.0.0.1:8000 — press Ctrl+C to stop EnergyPilot.'
& $demoPython -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
