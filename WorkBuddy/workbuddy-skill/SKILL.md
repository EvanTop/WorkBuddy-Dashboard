---
name: workbuddy-dashboard
description: 启动、停止或查看 WorkBuddy 本地数据看板（WorkBuddy Dashboard）。当用户想查看本机 WorkBuddy 用量统计，例如 Token 消耗、请求趋势、模型占比、工具调用排行、会话记录、活跃热力图或技能库时使用；典型说法包括「打开看板」「看看我的用量」「WorkBuddy 数据统计」「打开数据看板」「关掉看板」「看板状态」。此技能通过本机只读数据生成页面，无需联网或安装依赖。
agent_created: true
---

# WorkBuddy Dashboard

## Overview

WorkBuddy Dashboard 是一个本地使用数据看板：把本机国内版 `~/.workbuddy/` 与国际版 `~/.workbuddy-ai/` 的 `workbuddy.db` 与 `traces/*.jsonl` 以只读方式汇总成一块实时页面（Token、趋势、模型、工具、会话、热力图、技能库），页面内可切换版本。本技能封装了看板的启动、停止、状态查询，使 WorkBuddy 能用一句话管理它。

看板本体已自包含在 `scripts/` 目录，不依赖任何外部项目路径，安装本技能即可使用。

## When to use

在以下场景触发本技能：

- 用户要求打开 / 启动 / 查看数据看板、用量统计
- 用户询问本机 WorkBuddy 的使用情况（用了多少 Token、哪些模型、调用了什么工具、活跃时段等）
- 用户要求关闭看板、查看看板是否在运行、重启看板
- 用户提供看板地址或提到 `workbuddy.db` 的统计需求

## Quick start

所有操作通过 `scripts/ctl.py` 完成，它跨平台（macOS / Windows / Linux）并以 JSON 输出结果。脚本路径记为 `CTL`：`~/.workbuddy/skills/workbuddy-dashboard/scripts/ctl.py`（Windows 下为 `%USERPROFILE%\.workbuddy\skills\workbuddy-dashboard\scripts\ctl.py`）。

### 打开看板

1. 运行 `python3 <CTL> start`（Windows 用 `python <CTL> start`）。
2. 从输出的 JSON 读取 `url` 字段。若 `already_running` 为 true，说明服务已在运行，直接复用该地址。
3. 用 `present_files` 打开该 URL，让用户在预览面板看到看板。若 `present_files` 不可用，直接在回复里给出 URL。
4. 简短告知用户看板已启动、地址，以及默认访问口令 `admin`（除非用户此前改过）。

### 查看状态

运行 `python3 <CTL> status`，输出 JSON 含 `running` / `url` / `pid` / `healthy`。据此如实回答服务是否在运行、地址是什么。

### 关闭看板

运行 `python3 <CTL> stop`，并向用户确认已关闭。

### 重启看板

运行 `python3 <CTL> restart`，再按「打开看板」第 2-4 步展示结果。

## Options

`start` 与 `restart` 支持以下参数，按需附加：

- `--demo` — 强制使用随技能自带的脱敏演示数据（数据不全或想先看效果时使用）。
- `--no-auth` — 关闭访问口令（仅本机自用场景；默认保留 `admin` 口令）。
- `--port <端口>` — 固定端口（默认从 8790 起自动顺延到 8810）。

示例：`python3 <CTL> start --demo --no-auth`

## Data and privacy

- 正常模式自动发现两个版本的数据源：国内版 `~/.workbuddy/`、国际版 `~/.workbuddy-ai/`（各含 `workbuddy.db` 与 `traces/`），页面右上角可切换并记忆选择。环境变量 `WORKBUDDY_DB` / `WORKBUDDY_TRACES`（国内版）、`WORKBUDDY_INTL_DB` / `WORKBUDDY_INTL_TRACES`（国际版）可手动指定，`WORKBUDDY_EDITION=cn|intl` 设默认版本；找不到时回落到技能自带的 `scripts/demo/` 演示数据，并在页面上标注。
- 数据库以只读 URI（`mode=ro` + `query_only=ON`）打开，traces 只读流式解析；任何情况下都不修改原始数据。
- 服务默认只监听 `127.0.0.1`，不联网、无遥测、无 CDN。除非用户明确要求局域网访问，不要设置 `PANEL_BIND=0.0.0.0`。

## Troubleshooting

- **start 输出 `started:false`**：查看日志 `<技能目录>/scripts/.ctl.log` 末尾定位原因。常见为端口全被占用或 Python 启动失败。
- **状态显示 running 但页面打不开**：可能是口令未通过；确认用完整 URL 访问并输入口令 `admin`。
- **数据为空或显示演示数据**：本机尚无 WorkBuddy 数据，或数据源不在默认位置；可用 `WORKBUDDY_DB` 指定，或加 `--demo` 用演示数据。
- **停止后端口未释放**：ctl 会先 SIGTERM 再兜底 SIGKILL；如仍异常，检查 `.ctl.pid` 对应进程。

## Resources

### scripts/

- `ctl.py` — 控制器，负责 start / stop / restart / status / url，后台常驻并输出 JSON。
- `dashboard.py` — HTTP 服务与只读聚合（纯 Python 标准库）。
- `index.html` — 单文件前端（原生 JS + SVG，无框架无 CDN）。
- `make_demo.py` — 重新生成脱敏演示数据（可选）。
- `demo/` — 随技能自带的脱敏演示数据与 traces。
