$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    py -3.13 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.13 and the Python launcher first.' }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
if (-not (Test-Path -LiteralPath '.env')) {
    & .\.venv\Scripts\python.exe -c "from pathlib import Path; import secrets; p=Path('.env'); p.write_text(Path('.env.example').read_text().replace('MOUSECAM_ACCESS_CODE=', 'MOUSECAM_ACCESS_CODE='+secrets.token_urlsafe(24), 1), encoding='utf-8')"
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .env.' }
}
Write-Host 'Ready. Open .env privately to read your lab access code. Run .\start.ps1 -Simulated for a test camera.'
