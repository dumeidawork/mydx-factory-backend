# 将 start-backend.ps1 注册到当前用户「启动」文件夹（登录后自动运行）
# 用法:
#   安装: powershell -ExecutionPolicy Bypass -File scripts\install-backend-autostart.ps1
#   卸载: powershell -ExecutionPolicy Bypass -File scripts\install-backend-autostart.ps1 -Remove
param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$BACKEND = Split-Path -Parent $PSScriptRoot
$START_SCRIPT = Join-Path $PSScriptRoot "start-backend.ps1"
$STARTUP_DIR = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$SHORTCUT_PATH = Join-Path $STARTUP_DIR "MingyuanERP-backend.lnk"

if (-not (Test-Path $START_SCRIPT)) {
    throw "未找到启动脚本: $START_SCRIPT"
}

if ($Remove) {
    if (Test-Path $SHORTCUT_PATH) {
        Remove-Item $SHORTCUT_PATH -Force
        Write-Host ">>> 已移除登录自启: $SHORTCUT_PATH"
    } else {
        Write-Host ">>> 未找到自启快捷方式，无需移除。"
    }
    exit 0
}

New-Item -ItemType Directory -Force -Path $STARTUP_DIR | Out-Null

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($SHORTCUT_PATH)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-ExecutionPolicy Bypass -WindowStyle Minimized -File `"$START_SCRIPT`""
$shortcut.WorkingDirectory = $BACKEND
$shortcut.WindowStyle = 7
$shortcut.Description = "铭远 ERP 后端 (uvicorn)"
$shortcut.Save()

Write-Host ">>> 已安装登录自启（当前用户: $env:USERNAME）"
Write-Host ">>> 快捷方式: $SHORTCUT_PATH"
Write-Host ">>> 请使用专用账户 mingyuan_svc RDP 登录后生效；断开 RDP 请勿注销。"
Write-Host ">>> 卸载: install-backend-autostart.ps1 -Remove"
