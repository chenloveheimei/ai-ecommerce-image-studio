$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "$Root\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "尚未安装依赖。请先运行 setup.ps1。"
    exit 1
}
Set-Location $Root
& $Python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
