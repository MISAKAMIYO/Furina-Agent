# -*- coding: utf-8 -*-
"""
launch.py — Win7 启动器（Python 3.8.10）。

做四件事：
1. 应用 3.8 兼容补丁（win7.compat38）；
2. 选一个空闲端口（或读 ZHIYAN_PORT / --port）；
3. 起 uvicorn（单进程，无 reload，Win7 上更稳）；
4. 用默认浏览器打开界面（Win7 没有 WebView2/pywebview 的新版本，直接用浏览器最稳）。

用法：
    python win7/launch.py
    python win7/launch.py --port 8001 --no-browser
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_BACKEND = os.path.join(_ROOT, "backend")
for p in (_ROOT, _BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)


def _utf8_stdio() -> None:
    """Win7 的 cmd 默认 GBK，中文日志会抛 UnicodeEncodeError；统一成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _free_port(host: str = "127.0.0.1") -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Furina Agent · Win7 agent-only launcher")
    ap.add_argument("--port", type=int, default=None, help="固定端口；默认自动选空闲端口")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    ap.add_argument("--no-browser", action="store_true", help="不要自动打开浏览器")
    args = ap.parse_args()

    _utf8_stdio()

    # 1) 兼容补丁必须最先应用
    import win7.compat38 as compat

    patches = compat.install(verbose=True)
    if patches:
        print("[win7] compat:", ", ".join(patches))
    print(f"[win7] python {sys.version.split()[0]} on {sys.platform}")

    # 2) 端口
    port = args.port or int(os.environ.get("ZHIYAN_PORT") or 0) or _free_port()
    os.environ["ZHIYAN_PORT"] = str(port)

    # 3) 启动 uvicorn
    import uvicorn

    from win7.agent_app import app

    url = f"http://{args.host}:{port}/"
    print(f"[win7] serving {url}")
    if not args.no_browser:
        threading.Thread(
            target=lambda: (time.sleep(2.0), webbrowser.open(url)),
            daemon=True,
        ).start()

    uvicorn.run(
        app,
        host=args.host,
        port=port,
        log_level="info",
        access_log=False,
        loop="asyncio",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
