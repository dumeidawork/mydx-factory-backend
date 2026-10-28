@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 将按预置数据清空并灌入【物料-数量/托】到当前 .env 指向的数据库。
python scripts\deploy_france_auto_packing_qty_seed.py
if errorlevel 1 (
  echo 部署失败
  pause
  exit /b 1
)
echo 部署完成
pause
