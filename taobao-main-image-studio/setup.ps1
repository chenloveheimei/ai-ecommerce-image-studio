$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$OfficialPython = Get-ChildItem "$env:LocalAppData\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
$Python = if ($OfficialPython) { $OfficialPython } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $Python) {
    throw "未找到 Python。请安装 Python 3.10 或更高版本（64 位）后重试。"
}
$VersionText = & $Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ([Version]$VersionText -lt [Version]"3.10") {
    throw "当前 Python 为 $VersionText。rembg + u2netp 需要 Python 3.10 或更高版本（64 位）；请安装新版 Python 后重新运行。"
}
& $Python -m venv "$Root\.venv"
& "$Root\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$Root\.venv\Scripts\python.exe" -m pip install -r "$Root\requirements.txt"
Write-Host "安装完成。请运行 Start-Demo.ps1。"
