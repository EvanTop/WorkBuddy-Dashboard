#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy Dashboard 控制器：start / stop / restart / status / url。

后台启动看板服务并跨 shell 存活；所有状态以 JSON 输出，便于 Agent 解析。
"""

import os
import sys
import json
import time
import signal
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DASH = os.path.join(HERE, "dashboard.py")
PID_FILE = os.path.join(HERE, ".ctl.pid")
LOG_FILE = os.path.join(HERE, ".ctl.log")
PORT_FILE = os.path.join(HERE, ".port")
PY = sys.executable
_INDEX_TOKEN = os.environ.get("PANEL_TOKEN", "admin")


def _port_url():
    try:
        with open(PORT_FILE) as f:
            return f.read().strip()
    except Exception:
        return ""


def _read_pid():
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _health(url):
    if not url:
        return False
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _index_ok(url):
    """首页必须返回真正的 HTML，而不是 'index.html 缺失' 之类的错误页。"""
    if not url:
        return False
    try:
        suffix = ("?token=" + _INDEX_TOKEN) if _INDEX_TOKEN else ""
        req = urllib.request.Request(
            url.rstrip("/") + "/" + suffix, headers={"User-Agent": "ctl"})
        with urllib.request.urlopen(req, timeout=2) as r:
            body = r.read(4096).decode("utf-8", "ignore").lower()
            return "<html" in body and "缺失" not in body
    except Exception:
        return False


def status():
    url = _port_url()
    pid = _read_pid()
    healthy = _health(url)
    index_ok = _index_ok(url)
    info = {
        "running": bool(pid and _alive(pid) and healthy and index_ok),
        "pid": pid,
        "url": url,
        "healthy": healthy,
        "index_ok": index_ok,
    }
    print(json.dumps(info, ensure_ascii=False))
    return 0 if info["running"] else 1


def start(demo=False, no_auth=False, port=None):
    cur = _port_url()
    env_token = os.environ.get("PANEL_TOKEN", "admin")
    if no_auth:
        env_token = ""
    global _INDEX_TOKEN
    _INDEX_TOKEN = env_token
    if _health(cur) and _index_ok(cur):
        print(json.dumps({
            "action": "start", "started": False, "already_running": True,
            "url": cur, "pid": _read_pid(),
        }, ensure_ascii=False))
        return 0
    if _health(cur) and not _index_ok(cur):
        try:
            with open(PORT_FILE, "w") as f:
                f.write("")
        except Exception:
            pass
        if _read_pid() and _alive(_read_pid()):
            try:
                os.kill(_read_pid(), signal.SIGTERM)
            except Exception:
                pass
            time.sleep(0.6)

    if _read_pid() and _alive(_read_pid()):
        try:
            os.kill(_read_pid(), signal.SIGTERM)
        except Exception:
            pass
        time.sleep(0.6)

    # 清理陈旧字节码缓存：避免改过源码后仍加载旧 .pyc（曾导致误报 index.html 缺失）
    try:
        cache = os.path.join(HERE, "__pycache__")
        import shutil
        if os.path.isdir(cache):
            shutil.rmtree(cache, ignore_errors=True)
    except Exception:
        pass

    env = os.environ.copy()
    env["WORKBUDDY_NO_BROWSER"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if demo:
        env["WORKBUDDY_DEMO"] = "1"
    if no_auth:
        env["WORKBUDDY_NO_AUTH"] = "1"
    if port:
        env["PANEL_PORT"] = str(port)

    log = open(LOG_FILE, "ab")
    kw = {"cwd": HERE, "env": env, "stdout": log,
          "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "posix":
        kw["start_new_session"] = True
    else:
        kw["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                               | subprocess.DETACHED_PROCESS)
    try:
        p = subprocess.Popen([PY, "-B", DASH], **kw)
    except Exception as e:
        print(json.dumps({"action": "start", "started": False,
                          "error": str(e)}, ensure_ascii=False))
        return 2
    with open(PID_FILE, "w") as f:
        f.write(str(p.pid))

    url = ""
    for _ in range(60):
        time.sleep(0.25)
        url = _port_url()
        if url and _health(url) and _index_ok(url):
            break
    ok = bool(url and _health(url) and _index_ok(url))
    print(json.dumps({
        "action": "start", "started": ok, "already_running": False,
        "url": url, "pid": p.pid, "source": "demo" if demo else "auto",
        "log": LOG_FILE,
    }, ensure_ascii=False))
    return 0 if ok else 3


def stop():
    pid = _read_pid()
    killed = False
    if pid and _alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
        except Exception:
            pass
        for _ in range(20):
            time.sleep(0.15)
            if not _alive(pid):
                break
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
    for fp in (PID_FILE, PORT_FILE):
        try:
            os.remove(fp)
        except Exception:
            pass
    print(json.dumps({"action": "stop", "stopped": killed, "pid": pid},
                     ensure_ascii=False))
    return 0


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    rest = args[1:]
    demo = "--demo" in rest
    no_auth = "--no-auth" in rest
    port = None
    if "--port" in rest:
        try:
            port = int(rest[rest.index("--port") + 1])
        except Exception:
            pass

    if cmd == "start":
        return start(demo, no_auth, port)
    if cmd == "stop":
        return stop()
    if cmd == "restart":
        stop()
        time.sleep(0.4)
        return start(demo, no_auth, port)
    if cmd == "status":
        return status()
    if cmd == "url":
        url = _port_url()
        print(url if _health(url) else "")
        return 0 if url else 1
    print(json.dumps({"error": "unknown command: %s" % cmd,
                      "usage": "start|stop|restart|status|url"},
                     ensure_ascii=False))
    return 2


if __name__ == "__main__":
    sys.exit(main())
