$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'Project virtual environment not found. Create .venv and install backend requirements first.'
}

& $python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
