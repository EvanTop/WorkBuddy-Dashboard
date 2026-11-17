# WorkBuddy Dashboard

把 WorkBuddy 留在你本机的用量数据，画成一块实时看板。**零依赖、只读、双击即用。**

## 它能做什么

Token 消耗、请求趋势、模型占比、工具排行、会话记录、活跃热力图、技能库——一眼看清「用了多少、花在哪、调了什么」。
数据只在本机读取和展示，**不上传、不联网、不写回原库**。

## 使用方法

只需 **Python 3.8+**，不用 pip 装任何东西。

1. 下载本项目
2. 双击对应脚本（macOS `start-mac.command` / Windows `start-windows.bat` / Linux `./start-linux.sh`），或终端运行 `python3 dashboard.py`
3. 浏览器打开终端给出的地址（默认 `http://127.0.0.1:8790/`），输入口令 **`admin`**

就这么简单。按 `Ctrl + C` 停止。

> 想先看效果？用演示数据启动：`WORKBUDDY_DEMO=1 python3 dashboard.py`（Windows 用 `set WORKBUDDY_DEMO=1 && python dashboard.py`）。

## 数据从哪来

自动发现本机 `~/.workbuddy/workbuddy.db` 和 `~/.workbuddy/traces/`，找不到就用自带的脱敏演示数据。也可用 `WORKBUDDY_DB` / `WORKBUDDY_TRACES` 手动指定。

## 隐私

仅监听 `127.0.0.1`，数据库以只读方式打开，无遥测、无外部请求、无 CDN，离线可用。

## License

MIT © 2026 Evan
