# 铭远 ERP 后端停止（按监听端口结束 uvicorn）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\stop-backend.ps1
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Continue"

$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $connections) {
    Write-Host "端口 $Port 无监听进程，后端未运行。"
    exit 0
}

$pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($pid in $pids) {
    try {
        $proc = Get-Process -Id $pid -ErrorAction Stop
        Write-Host ">>> 结束进程 PID=$pid ($($proc.ProcessName))"
        Stop-Process -Id $pid -Force -ErrorAction Stop
    } catch {
        Write-Warning "无法结束 PID=$pid : $_"
    }
}

Start-Sleep -Seconds 1
$still = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($still) {
    Write-Host ">>> 警告: 端口 $Port 仍在监听，请检查任务管理器。"
    exit 1
}

Write-Host ">>> 后端已停止。"
exit 0
