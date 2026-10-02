param([switch]$Simulated)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw 'Run .\setup.ps1 first.' }
$previousSimulation = $env:MOUSECAM_SIMULATED
try {
    if ($Simulated) { $env:MOUSECAM_SIMULATED = 'true' }
    & .\.venv\Scripts\python.exe -m mousecam_web
    if ($LASTEXITCODE -ne 0) { throw 'MouseCam exited with an error. See the console above.' }
} finally {
    $env:MOUSECAM_SIMULATED = $previousSimulation
}
