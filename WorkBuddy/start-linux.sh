#!/usr/bin/env bash
# WorkBuddy Dashboard · Linux 启动脚本
cd "$(dirname "$0")"
echo "正在启动 WorkBuddy Dashboard 本地数据看板…"
python3 dashboard.py
