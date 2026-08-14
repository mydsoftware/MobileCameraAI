$ErrorActionPreference = 'Stop'

if (-not $env:CAMERA_PASSWORD) {
    $env:CAMERA_PASSWORD = Read-Host 'Camera password'
}

if (-not $env:WEB_LOGIN_HANDLE) {
    Write-Host 'WEB_LOGIN_HANDLE is optional. Leave empty if unknown.' -ForegroundColor Yellow
}

python -m pip install -r requirements.txt
python viewer.py
