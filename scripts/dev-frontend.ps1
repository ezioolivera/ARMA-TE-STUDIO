$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot 'frontend'
Set-Location $frontendRoot

if (-not (Test-Path (Join-Path $frontendRoot 'node_modules'))) {
    throw 'Frontend dependencies are missing. Run npm install in frontend first.'
}

npm run dev
