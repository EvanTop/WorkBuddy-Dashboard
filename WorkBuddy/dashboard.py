#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WorkBuddy Dashboard · WorkBuddy 本地使用数据看板
==========================================
把 WorkBuddy 在你本机留下的使用数据，变成一块能直接打开看的实时看板：
Token 消耗（输入/输出/思考/缓存命中）、请求趋势、模型占比、工具调用排行、
会话记录、活跃热力图、技能库、工作区、系统状态。

零安装、零依赖、零上传：仅使用 Python3 标准库，数据始终留在你本机。
workbuddy.db 以 sqlite3 只读 URI(file:...?mode=ro) 打开，绝不写库、不改库；
traces/*.jsonl 仅只读流式解析。

快速开始（项目根目录）:
    Mac     ./start-mac.command
    Windows start-windows.bat
    Linux   ./start-linux.sh
    任意平台 python3 dashboard.py

首次启动自动发现 ~/.workbuddy，找不到时回落到 demo/ 脱敏演示数据。

环境变量:
    PANEL_BIND       监听地址，默认 127.0.0.1（仅本机访问，安全默认值）
    PANEL_PORT       固定端口；不设置则从 8790 起自动顺延
    PANEL_TOKEN      访问口令，默认 admin；显式置空则免口令
    WORKBUDDY_DB     手动指定 workbuddy.db 路径或其所在目录
    WORKBUDDY_TRACES 手动指定 traces 目录
    WORKBUDDY_NO_AUTH=1   等同 PANEL_TOKEN 置空，直接免口令
    WORKBUDDY_DEMO=1      强制使用 demo/ 演示数据
    WORKBUDDY_NO_BROWSER=1 启动时不自动打开浏览器
"""

import glob
import json
import os
import signal
import sqlite3
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import pathname2url

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(HERE, "index.html")
PORT_FILE = os.path.join(HERE, ".port")
DEMO_DIR = os.path.join(HERE, "demo")

VERSION = "1.0.0"
APP_NAME = "WorkBuddy Dashboard"
DEFAULT_TOKEN = "admin"

CACHE_TTL = 5.0            # 热数据缓存秒数
TRACES_TTL = 30.0          # traces 冷数据缓存秒数（全表流式扫描，较慢）
COLD_TTL = 60.0            # 冷数据后台刷新周期
PORT_START, PORT_END = 8790, 8810
START_TS = time.time()


def _env(name, default=""):
    v = os.environ.get(name)
    return default if v is None else str(v).strip()


BIND = _env("PANEL_BIND", "127.0.0.1") or "127.0.0.1"
_PORT_ENV = _env("PANEL_PORT", "")
FIXED_PORT = None
if _PORT_ENV:
    if _PORT_ENV.isdigit() and 0 < int(_PORT_ENV) < 65536:
        FIXED_PORT = int(_PORT_ENV)
    else:
        sys.stderr.write("PANEL_PORT 取值非法：%r（应为 1-65535 的整数）\n" % _PORT_ENV)
        sys.stderr.flush()
        raise SystemExit(2)


def _resolve_token():
    if _env("WORKBUDDY_NO_AUTH", "").lower() in ("1", "true", "yes", "on"):
        return ""
    raw = os.environ.get("PANEL_TOKEN")
    if raw is None:
        return DEFAULT_TOKEN
    return str(raw).strip()


PANEL_TOKEN = _resolve_token()

_last_source = ["auto"]


def log(msg):
    try:
        sys.stdout.write("[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
        sys.stdout.flush()
    except Exception:
        pass


# ---------------------------------------------------------------- 数据源发现
def user_home():
    return os.path.expanduser("~")


def discover_sources():
    """返回 (db_path, traces_dir, source_label)。按优先级自动发现。"""
    demo_mode = _env("WORKBUDDY_DEMO", "").lower() in ("1", "true", "yes", "on")
    if demo_mode:
        return (os.path.join(DEMO_DIR, "workbuddy.db"),
                os.path.join(DEMO_DIR, "traces"), "demo")

    db_env = _env("WORKBUDDY_DB", "")
    tr_env = _env("WORKBUDDY_TRACES", "")

    home = user_home()
    default_db = os.path.join(home, ".workbuddy", "workbuddy.db")
    default_tr = os.path.join(home, ".workbuddy", "traces")

    db_path = ""
    traces_dir = ""
    if db_env:
        if os.path.isdir(db_env):
            cand = os.path.join(db_env, "workbuddy.db")
            db_path = cand if os.path.isfile(cand) else db_env
        elif os.path.isfile(db_env):
            db_path = db_env
    if not db_path and os.path.isfile(default_db):
        db_path = default_db
    if tr_env and os.path.isdir(tr_env):
        traces_dir = tr_env
    if not traces_dir and os.path.isdir(default_tr):
        traces_dir = default_tr

    if db_path and os.path.isfile(db_path):
        return db_path, traces_dir, "local"

    # 回落演示数据
    demo_db = os.path.join(DEMO_DIR, "workbuddy.db")
    demo_tr = os.path.join(DEMO_DIR, "traces")
    if os.path.isfile(demo_db):
        return demo_db, demo_tr, "demo"
    return "", "", "none"


# ---------------------------------------------------------------- 只读工具
class _ROConn:
    def __init__(self, con):
        self._con = con

    def execute(self, sql, args=()):
        return self._con.execute(sql, args)

    def cursor(self):
        return self._con.cursor()

    def close(self):
        try:
            self._con.close()
        except Exception:
            pass


def ro_connect(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        uri = "file:%s?mode=ro" % pathname2url(os.path.abspath(path))
        con = sqlite3.connect(uri, uri=True, timeout=5, check_same_thread=False)
        con.execute("PRAGMA query_only=ON")
        con.row_factory = sqlite3.Row
        return _ROConn(con)
    except Exception:
        return None


def rows(con, sql, args=()):
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []


def one(con, sql, args=(), default=None):
    try:
        r = con.execute(sql, args).fetchone()
        return dict(r) if r is not None else default
    except Exception:
        return default


def num(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def to_local_dt(s):
    """把 ISO 字符串或秒/毫秒时间戳统一转成当地 datetime。"""
    if s is None or s == "":
        return None
    # 数值时间戳：毫秒(1e12+)自动换算成秒
    if isinstance(s, (int, float)) or (isinstance(s, str) and s.isdigit()):
        try:
            v = float(s)
            if abs(v) >= 1e11:
                v /= 1000.0
            return datetime.fromtimestamp(v)
        except Exception:
            return None
    try:
        x = str(s)
        if x.endswith("Z"):
            x = x[:-1] + "+00:00"
        dt = datetime.fromisoformat(x)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except Exception:
        return None


def fmt_dt(s):
    dt = to_local_dt(s)
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def day_key(s):
    dt = to_local_dt(s)
    return dt.strftime("%Y-%m-%d") if dt else ""


# ---------------------------------------------------------------- traces 采集
_TRACES = {"ts": 0.0, "data": None, "running": False}
_TRACES_LOCK = threading.Lock()


def _empty_traces():
    return {
        "tokens": {"total": 0, "input": 0, "output": 0, "cached": 0, "reasoning": 0,
                   "requests": 0, "peak": 0, "credits": 0.0},
        "daily": {}, "monthly": {}, "models": {},
        "tools": {}, "heatmap": [[0] * 24 for _ in range(7)],
        "tool_daily": {},
        "trace_status": {"ok": 0, "error": 0, "running": 0},
        "trace_count": 0, "questions": 0,
    }


def scan_traces(traces_dir):
    """流式只读解析 traces/*.jsonl，聚合 token / 模型 / 工具 / 时段。"""
    out = _empty_traces()
    if not traces_dir or not os.path.isdir(traces_dir):
        return out
    files = glob.glob(os.path.join(traces_dir, "**", "*.jsonl"), recursive=True)
    out["trace_count"] = len(files)
    status_map = {}
    seen_gen = set()

    for f in files:
        try:
            fh = open(f, "r", encoding="utf-8", errors="ignore")
        except Exception:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                etype = o.get("type")

                # trace 级状态（以 traceId 记录，后写覆盖前写）
                if etype in ("trace_start", "trace_end", "trace"):
                    t = o.get("trace") or {}
                    tid = t.get("traceId")
                    if tid and t.get("status"):
                        status_map[tid] = t.get("status")
                    continue

                sp = o.get("span")
                if not sp:
                    continue
                # 只处理 span_end，避免 span_start 重复计数
                if etype != "span_end":
                    continue
                stype = sp.get("type")
                started = sp.get("startedAt", "")
                dk = day_key(started)
                dt = to_local_dt(started)

                # generation：usage 在 toolOutput（JSON 数组）
                if stype == "generation":
                    sid = sp.get("spanId")
                    if sid in seen_gen:
                        continue
                    raw = sp.get("toolOutput") or ""
                    if not raw:
                        continue
                    try:
                        arr = json.loads(raw) if isinstance(raw, str) else raw
                    except Exception:
                        continue
                    if not isinstance(arr, list):
                        continue
                    for item in arr:
                        if not isinstance(item, dict):
                            continue
                        usage = item.get("usage") or {}
                        if not usage:
                            continue
                        seen_gen.add(sid)
                        pt = num(usage.get("prompt_tokens"))
                        ct = num(usage.get("completion_tokens"))
                        tt = num(usage.get("total_tokens")) or (pt + ct)
                        ptd = usage.get("prompt_tokens_details") or {}
                        ctd = usage.get("completion_tokens_details") or {}
                        cached = num(ptd.get("cached_tokens")) if isinstance(ptd, dict) else 0
                        reasoning = num(ctd.get("reasoning_tokens")) if isinstance(ctd, dict) else 0
                        model = str(item.get("model") or "unknown")

                        tk = out["tokens"]
                        tk["total"] += tt
                        tk["input"] += pt
                        tk["output"] += ct
                        tk["cached"] += cached
                        tk["reasoning"] += reasoning
                        tk["requests"] += 1
                        if tt > tk["peak"]:
                            tk["peak"] = tt

                        out["daily"].setdefault(dk, {"tokens": 0, "requests": 0, "output": 0, "cached": 0})
                        dd = out["daily"][dk]
                        dd["tokens"] += tt
                        dd["requests"] += 1
                        dd["output"] += ct
                        dd["cached"] += cached

                        if dk:
                            mk = dk[:7]
                            mm = out["monthly"].setdefault(mk, {"tokens": 0, "requests": 0})
                            mm["tokens"] += tt
                            mm["requests"] += 1

                        m = out["models"].setdefault(model, {"tokens": 0, "requests": 0,
                                                              "input": 0, "output": 0, "cached": 0})
                        m["tokens"] += tt
                        m["requests"] += 1
                        m["input"] += pt
                        m["output"] += ct
                        m["cached"] += cached

                        if dt is not None:
                            out["heatmap"][dt.weekday()][dt.hour] += 1
                    continue

                # function：真实工具调用（排除 mcp_tools 这类 custom）
                if stype == "function":
                    name = sp.get("name") or sp.get("toolName") or "unknown"
                    out["tools"][name] = out["tools"].get(name, 0) + 1
                    if dk:
                        out["tool_daily"][dk] = out["tool_daily"].get(dk, 0) + 1

    for st in status_map.values():
        key = st if st in out["trace_status"] else "running"
        out["trace_status"][key] += 1
    return out


def traces_snapshot(traces_dir):
    age = time.time() - _TRACES["ts"]
    if age > TRACES_TTL and not _TRACES["running"] and _TRACES["data"] is None:
        return _refresh_traces_sync(traces_dir)
    if age > COLD_TTL and not _TRACES["running"]:
        _kick_traces_refresh(traces_dir)
    with _TRACES_LOCK:
        data = _TRACES["data"]
    return (data if data is not None else _refresh_traces_sync(traces_dir)), age


def _refresh_traces_sync(traces_dir):
    data = scan_traces(traces_dir)
    with _TRACES_LOCK:
        _TRACES["data"] = data
        _TRACES["ts"] = time.time()
    return data, 0.0


def _kick_traces_refresh(traces_dir):
    _TRACES["running"] = True

    def _run():
        try:
            _refresh_traces_sync(traces_dir)
        except Exception:
            pass
        finally:
            _TRACES["running"] = False

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------- db 采集
def collect_sessions(con):
    sql = """
        SELECT s.id, s.title, s.custom_title, s.model, s.mode, s.status,
               s.created_at, s.updated_at, s.last_activity_at, s.is_background_automation,
               s.source_mode, s.cwd,
               u.used, u.size, u.credit_json
        FROM sessions s
        LEFT JOIN session_usage u ON u.session_id = s.id
        WHERE s.deleted_at IS NULL OR s.deleted_at = 0
        ORDER BY s.last_activity_at DESC
    """
    raw = rows(con, sql)
    out = []
    for r in raw:
        title = r.get("custom_title") or r.get("title") or "(无标题会话)"
        credit = 0.0
        try:
            cj = r.get("credit_json")
            if cj:
                obj = json.loads(cj) if isinstance(cj, str) else cj
                if isinstance(obj, dict):
                    credit = round(sum(float(v) for v in obj.values()), 2)
        except Exception:
            pass
        out.append({
            "id": r.get("id", ""),
            "title": str(title)[:120],
            "model": r.get("model") or "",
            "mode": r.get("mode") or "",
            "status": r.get("status") or "",
            "created": fmt_dt(r.get("created_at")),
            "updated": fmt_dt(r.get("updated_at")),
            "last_activity": fmt_dt(r.get("last_activity_at")),
            "used": num(r.get("used")),
            "size": num(r.get("size")),
            "credit": credit,
            "cwd": str(r.get("cwd") or "")[:160],
            "background": bool(r.get("is_background_automation")),
        })
    return out


def collect_skills(con):
    sql = """SELECT name, source, description, description_zh, disabled,
                    user_invocable, version, size, indexed_at
             FROM local_skills ORDER BY name"""
    raw = rows(con, sql)
    out = []
    by_source = {}
    by_type = {}
    invocable = 0
    enabled = 0
    for r in raw:
        name = r.get("name") or "unknown"
        src = r.get("source") or "unknown"
        dis = bool(r.get("disabled"))
        inv = bool(r.get("user_invocable"))
        desc = r.get("description_zh") or r.get("description") or ""
        out.append({
            "name": name, "source": src,
            "description": str(desc)[:160],
            "version": r.get("version") or "",
            "disabled": dis, "user_invocable": inv,
            "updated": fmt_dt(r.get("indexed_at")),
        })
        by_source[src] = by_source.get(src, 0) + 1
        if inv:
            invocable += 1
        if not dis:
            enabled += 1
        # 粗略类型：从描述或名字里识别
        low = (name + " " + str(desc)).lower()
        typ = "其他"
        for key, label in (("ppt", "PPT/演示"), ("doc", "文档"), ("excel", "表格"),
                           ("sheet", "表格"), ("pdf", "PDF"), ("image", "图像"),
                           ("design", "设计"), ("search", "检索"), ("web", "网页")):
            if key in low:
                typ = label
                break
        by_type[typ] = by_type.get(typ, 0) + 1
    stats = {"total": len(raw), "enabled": enabled, "invocable": invocable,
             "by_source": by_source, "by_type": by_type}
    return out, stats


def collect_workspaces(con):
    raw = rows(con, "SELECT path, last_opened_at FROM workspaces ORDER BY last_opened_at DESC LIMIT 50")
    return [{"path": str(r.get("path") or "")[:200],
             "last_opened": fmt_dt(r.get("last_opened_at"))} for r in raw]


def fill_daily(daily_map):
    """补齐活跃跨度内逐日，返回按日期升序的列表。"""
    if not daily_map:
        return []
    keys = [k for k in daily_map if k]
    if not keys:
        return []
    keys.sort()
    try:
        start = datetime.strptime(keys[0], "%Y-%m-%d")
        end = datetime.strptime(keys[-1], "%Y-%m-%d")
    except Exception:
        return [dict(v, day=k) for k, v in sorted(daily_map.items())]
    out = []
    cur = start
    guard = 0
    while cur <= end and guard < 800:
        k = cur.strftime("%Y-%m-%d")
        v = daily_map.get(k) or {"tokens": 0, "requests": 0, "output": 0, "cached": 0}
        out.append({"day": k, "tokens": v["tokens"], "requests": v["requests"],
                    "output": v["output"], "cached": v["cached"]})
        cur += timedelta(days=1)
        guard += 1
    return out


def day_span(first_day, last_day):
    try:
        return (datetime.strptime(last_day, "%Y-%m-%d") -
                datetime.strptime(first_day, "%Y-%m-%d")).days + 1
    except Exception:
        return 0


# ---------------------------------------------------------------- 汇总
def compute_stats():
    db_path, traces_dir, source = discover_sources()
    _last_source[0] = source
    today = datetime.now().strftime("%Y-%m-%d")

    con = ro_connect(db_path)
    tr, traces_age = traces_snapshot(traces_dir)

    payload = {
        "tokens": {}, "counts": {}, "daily": [], "monthly": [], "models": [],
        "tools": [], "heatmap": tr["heatmap"], "conversations": [],
        "skills": [], "skill_stats": {"total": 0, "enabled": 0, "invocable": 0,
                                      "by_source": {}, "by_type": {}},
        "workspaces": [], "today": {"date": today, "tokens": 0, "requests": 0,
                                    "tool_calls": 0, "sessions": 0},
        "meta": {},
    }

    sessions = collect_sessions(con) if con else []
    skills, skill_stats = collect_skills(con) if con else ([], payload["skill_stats"])
    workspaces = collect_workspaces(con) if con else []
    payload["conversations"] = sessions
    payload["skills"] = skills
    payload["skill_stats"] = skill_stats
    payload["workspaces"] = workspaces

    # token 汇总（来自 traces）
    tk = dict(tr["tokens"])
    daily_list = fill_daily(tr["daily"])
    days_with_data = [d["day"] for d in daily_list if d["tokens"] > 0]
    tk["active_days"] = len(days_with_data)
    tk["first_day"] = days_with_data[0] if days_with_data else ""
    tk["last_day"] = days_with_data[-1] if days_with_data else ""
    tk["span_days"] = day_span(tk["first_day"], tk["last_day"])
    tk["avg"] = int(round(tk["total"] / tk["requests"])) if tk["requests"] else 0
    tk["cache_hit_rate"] = round(tk["cached"] * 100.0 / tk["input"], 2) if tk["input"] else 0.0
    payload["tokens"] = tk

    payload["daily"] = daily_list
    payload["monthly"] = [{"month": k, "tokens": v["tokens"], "requests": v["requests"]}
                          for k, v in sorted(tr["monthly"].items())]
    payload["models"] = sorted(
        [{"name": k, **v} for k, v in tr["models"].items()],
        key=lambda x: x["tokens"], reverse=True)
    payload["tools"] = sorted(
        [{"name": k, "count": v} for k, v in tr["tools"].items()],
        key=lambda x: x["count"], reverse=True)

    # counts
    status = tr["trace_status"]
    payload["counts"] = {
        "sessions": len(sessions),
        "traces": tr["trace_count"],
        "traces_ok": status.get("ok", 0),
        "traces_error": status.get("error", 0),
        "tool_calls": sum(tr["tools"].values()),
        "models": len(payload["models"]),
        "tools": len(payload["tools"]),
        "skills": skill_stats["total"],
        "workspaces": len(workspaces),
        "active_days": tk["active_days"],
        "questions": tr["questions"] or status.get("ok", 0),
    }

    # 今日
    td = next((d for d in daily_list if d["day"] == today), None)
    payload["today"] = {
        "date": today,
        "tokens": td["tokens"] if td else 0,
        "requests": td["requests"] if td else 0,
        "tool_calls": tr["tool_daily"].get(today, 0),
        "sessions": sum(1 for s in sessions if s["created"].startswith(today)),
    }

    db_mtime = ""
    if db_path:
        try:
            db_mtime = datetime.fromtimestamp(os.path.getmtime(db_path)).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass

    payload["meta"] = {
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_path": db_path, "db_mtime": db_mtime,
        "traces_dir": traces_dir, "trace_files": tr["trace_count"],
        "traces_age_ms": round(traces_age * 1000.0, 1),
        "uptime_sec": round(time.time() - START_TS, 1),
        "source": source, "is_demo": source == "demo",
        "app": APP_NAME, "version": VERSION,
    }

    if con:
        con.close()
    return payload


# ---------------------------------------------------------------- 缓存
_LOCK = threading.Lock()
_COMPUTE_LOCK = threading.Lock()
_CACHE = {"ts": 0.0, "payload": None}


def _cache_hit(age):
    with _LOCK:
        cached = _CACHE["payload"]
    if cached is None:
        return None
    out = dict(cached)
    meta = dict(cached.get("meta") or {})
    meta["cache_age_ms"] = round(age * 1000.0, 1)
    meta["server_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta["uptime_sec"] = round(time.time() - START_TS, 1)
    out["meta"] = meta
    return out


def get_stats():
    age = time.time() - _CACHE["ts"]
    hit = _cache_hit(age)
    if hit is not None and age <= CACHE_TTL:
        return hit
    with _COMPUTE_LOCK:
        age = time.time() - _CACHE["ts"]
        hit = _cache_hit(age)
        if hit is not None and age <= CACHE_TTL:
            return hit
        try:
            payload = compute_stats()
            with _LOCK:
                _CACHE["payload"] = payload
                _CACHE["ts"] = time.time()
            return _cache_hit(0.0) or payload
        except Exception:
            hit = _cache_hit(age)
            if hit is not None:
                return hit
            return {"error": traceback.format_exc(limit=2), "meta": {"app": APP_NAME,
                    "version": VERSION, "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}


def warm_cache():
    try:
        get_stats()
    except Exception:
        pass


# ---------------------------------------------------------------- HTTP
LOGIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(app)s · 登录</title><style>
:root{--bg:#090D18;--cyan:#38E8D4;--purple:#846BFF;--fg:#E8EDF7;--mut:#7D8AA8}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--fg);
font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;display:flex;align-items:center;justify-content:center}
.box{width:min(92vw,380px);background:#111831;border:1px solid #22305a;border-radius:16px;padding:34px;
box-shadow:0 20px 60px rgba(0,0,0,.5)}
.brand{font-size:12px;letter-spacing:3px;color:var(--cyan);font-weight:700}
h1{font-size:22px;margin:12px 0 6px}p{color:var(--mut);font-size:13px;margin:0 0 24px}
input{width:100%%;padding:13px 14px;border-radius:10px;border:1px solid #2a3964;background:#0b1122;
color:var(--fg);font-size:15px;outline:none}input:focus{border-color:var(--cyan)}
button{width:100%%;margin-top:14px;padding:13px;border:0;border-radius:10px;cursor:pointer;
background:linear-gradient(90deg,var(--cyan),var(--purple));color:#04121a;font-weight:800;font-size:15px}
.err{color:#ff7b7b;font-size:13px;margin-top:12px;min-height:18px}
.foot{margin-top:22px;font-size:11px;color:#556080;text-align:center}
</style></head><body><div class="box">
<div class="brand">◆ EVAN WORKBUDDY</div><h1>本地数据看板</h1>
<p>输入访问口令以查看你的本机使用数据</p>
<form method="get" action="/"><input type="hidden" name="login" value="1">
<input type="password" name="token" placeholder="访问口令" autofocus autocomplete="current-password">
<button type="submit">进入看板 →</button></form>
<div class="err">%(err)s</div>
<div class="foot">数据仅在本机流转 · 不上传 · 不联网</div>
</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "WorkBuddy Dashboard/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    def _authorized(self, qs):
        if not PANEL_TOKEN:
            return True
        t = (qs.get("token") or [""])[0]
        if t == PANEL_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == ("Bearer " + PANEL_TOKEN)

    def _login_page(self, bad=False):
        html = LOGIN_HTML % {"app": APP_NAME, "err": "口令错误，请重试" if bad else ""}
        self._send(200, html, "text/html; charset=utf-8")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/healthz":
            payload = {
                "ok": True, "app": APP_NAME, "version": VERSION,
                "source": _last_source[0], "is_demo": _last_source[0] == "demo",
                "uptime_sec": round(time.time() - START_TS, 1),
                "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return

        if path == "/api/stats":
            if not self._authorized(qs):
                self._send(401, json.dumps({"error": "unauthorized"}, ensure_ascii=False),
                           "application/json; charset=utf-8")
                return
            payload = get_stats()
            self._send(200, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")
            return

        if path in ("/", "/index.html"):
            if not self._authorized(qs):
                self._login_page(bad=(qs.get("login") or [""])[0] == "1")
                return
            try:
                with open(INDEX_FILE, "r", encoding="utf-8") as f:
                    html = f.read()
            except Exception:
                html = "<h3 style='color:red'>index.html 缺失</h3>"
            self._send(200, html, "text/html; charset=utf-8")
            return

        self._send(404, "Not Found", "text/plain; charset=utf-8")


def _make_server(host, port):
    return ThreadingHTTPServer((host, port), Handler)


def serve():
    warm_cache()
    port = None
    if FIXED_PORT is not None:
        try:
            srv = _make_server(BIND, FIXED_PORT)
            port = FIXED_PORT
        except OSError:
            log("端口 %d 已被占用（PANEL_PORT 固定模式），退出。" % FIXED_PORT)
            return
    else:
        for p in range(PORT_START, PORT_END + 1):
            try:
                srv = _make_server(BIND, p)
                port = p
                break
            except OSError:
                continue
        if port is None:
            log("端口 %d-%d 全部被占用，无法启动。" % (PORT_START, PORT_END))
            return

    url = "http://%s:%d/" % ("127.0.0.1" if BIND in ("0.0.0.0", "") else BIND, port)
    try:
        with open(PORT_FILE, "w") as f:
            f.write(url)
    except Exception:
        pass

    db_path, traces_dir, source = discover_sources()
    log("%s v%s 已启动：%s" % (APP_NAME, VERSION, url))
    log("数据源：%s | %s" % (source, db_path or "无"))
    log("traces：%s" % (traces_dir or "无"))
    log("访问口令：%s" % ("已启用（默认 admin）" if PANEL_TOKEN else "已关闭"))

    no_browser = _env("WORKBUDDY_NO_BROWSER", "").lower() in ("1", "true", "yes", "on")
    if not no_browser:
        try:
            threading.Timer(0.8, lambda: webbrowser.open(url + ("?token=" + PANEL_TOKEN if PANEL_TOKEN else ""))).start()
        except Exception:
            pass

    def _on_term(signum, frame):
        try:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        except Exception:
            pass

    signal.signal(signal.SIGINT, _on_term)
    signal.signal(signal.SIGTERM, _on_term)
    try:
        srv.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            srv.server_close()
        except Exception:
            pass
        try:
            os.remove(PORT_FILE)
        except Exception:
            pass
        log("已停止。")


if __name__ == "__main__":
    serve()
