# 铭远 ERP 后端生产启动（RDP 专用账户 + Word COM）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\start-backend.ps1
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$BACKEND = Split-Path -Parent $PSScriptRoot
$LOG_DIR = Join-Path $BACKEND "logs"
$LOG_FILE = Join-Path $LOG_DIR "uvicorn.log"

function Resolve-PythonExe {
    if ($env:PYTHON_EXE -and (Test-Path $env:PYTHON_EXE)) {
        return $env:PYTHON_EXE
    }
    $candidates = @(
        "C:\Program Files\Python311\python.exe",
        "C:\Program Files\python\python.exe",
        (Join-Path $BACKEND ".venv\Scripts\python.exe")
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) { return $path }
    }
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    throw "未找到 Python。请安装 Python 或设置环境变量 PYTHON_EXE"
}

function Test-PortListening {
    param([int]$ListenPort)
    $conn = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue
    return $null -ne $conn
}

Set-Location $BACKEND
New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null

if (Test-PortListening -ListenPort $Port) {
    Write-Host "端口 $Port 已在监听，后端可能已运行。若需重启请先运行 scripts\stop-backend.ps1"
    exit 0
}

$PY = Resolve-PythonExe
$uvicornArgs = @(
    "-m", "uvicorn", "app.main:app",
    "--host", "0.0.0.0",
    "--port", "$Port",
    "--workers", "1"
)

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LOG_FILE -Encoding utf8 -Value "`n===== START $stamp =====`n"

Write-Host ">>> Python:  $PY"
Write-Host ">>> 目录:    $BACKEND"
Write-Host ">>> 端口:    $Port"
Write-Host ">>> 日志:    $LOG_FILE"

$proc = Start-Process `
    -FilePath $PY `
    -ArgumentList $uvicornArgs `
    -WorkingDirectory $BACKEND `
    -WindowStyle Hidden `
    -RedirectStandardOutput $LOG_FILE `
    -RedirectStandardError $LOG_FILE `
    -PassThru

Start-Sleep -Seconds 2
if (Test-PortListening -ListenPort $Port) {
    Write-Host ">>> 后端已启动 (PID $($proc.Id))"
    exit 0
}

Write-Host ">>> 启动可能失败，请查看日志: $LOG_FILE"
exit 1
