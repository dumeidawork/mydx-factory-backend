# Mingyuan ERP backend dependency installer (Windows server)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\install-deps.ps1
$ErrorActionPreference = "Stop"

$PY = if ($env:PYTHON_EXE) { $env:PYTHON_EXE } else { "C:\Program Files\python\python.exe" }
$MIRROR = if ($env:PIP_MIRROR) { $env:PIP_MIRROR } else { "https://pypi.tuna.tsinghua.edu.cn/simple" }
$BACKEND = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $PY)) {
    $PY = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $PY) { throw "Python not found. Set PYTHON_EXE or install Python." }
}

Set-Location $BACKEND
Write-Host ">>> Python: $PY"
Write-Host ">>> Backend: $BACKEND"
Write-Host ">>> Mirror:  $MIRROR"

Write-Host ""
Write-Host ">>> Upgrading pip ..."
& $PY -m pip install --upgrade pip -i $MIRROR --default-timeout=300

Write-Host ""
Write-Host ">>> Installing requirements.txt ..."
& $PY -m pip install -r requirements.txt -i $MIRROR --default-timeout=300 --retries 5

Write-Host ""
Write-Host ">>> Verifying docx2pdf ..."
& $PY -m pip show docx2pdf
& $PY -c "from docx2pdf import convert; print('docx2pdf import OK')"

Write-Host ""
Write-Host ">>> Verifying pymupdf (import fitz) ..."
& $PY -m pip show pymupdf
& $PY -c "import fitz; print('pymupdf import OK', fitz.version)"

Write-Host ""
Write-Host ">>> Done."
