# Start PromptStudio and keep it running. Open http://127.0.0.1:5000/
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = "C:\Users\archi\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if (-not (Test-Path $Py)) { $Py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $Py) { Write-Error "Python not found"; exit 1 }

# Free port 5000 if a dead/old server is stuck
Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

Set-Location $Root
Write-Host "Starting PromptStudio from $Root"
Write-Host "Open: http://127.0.0.1:5000/"
& $Py -u server.py
