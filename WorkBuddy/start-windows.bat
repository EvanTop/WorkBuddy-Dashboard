@echo off
chcp 65001 >nul
cd /d "%~dp0"
title WorkBuddy Dashboard 本地数据看板
echo 正在启动 WorkBuddy Dashboard 本地数据看板…
python dashboard.py
if errorlevel 1 (
  echo.
  echo 启动失败。请确认已安装 Python 3 并加入 PATH：python -V
  pause
)
