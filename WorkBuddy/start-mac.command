#!/bin/bash
# WorkBuddy Dashboard · macOS 启动脚本
cd "$(dirname "$0")"
echo "正在启动 WorkBuddy Dashboard 本地数据看板…"
python3 dashboard.py
if [ $? -ne 0 ]; then
  echo ""
  echo "启动失败。请确认已安装 Python 3：终端执行 python3 -V"
  read -r -p "按回车键关闭…"
fi
