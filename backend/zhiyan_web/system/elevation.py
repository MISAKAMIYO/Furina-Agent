# -*- coding: utf-8 -*-
"""以管理员身份重启本程序（用于 BetterGI 更新流程能正常点击）

Windows 的 UIPI 会丢弃低完整性进程发往高完整性窗口的输入；
BetterGI 常以管理员运行，所以本程序也需要提权，更新流程才能点得动。
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, List

logger = logging.getLogger("ZhiYanWeb.system.elevation")


def is_admin() -> bool:
    """当前进程是否管理员权限"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _entry_script() -> str:
    """要重启的入口脚本（desktop.py 优先，其次 backend/main.py）"""
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if argv0.lower().endswith(("desktop.py", "desktop.exe")):
        return argv0
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    desktop = os.path.join(root, "desktop.py")
    if os.path.isfile(desktop):
        return desktop
    return argv0


def build_restart_command() -> Dict[str, Any]:
    """返回将要执行的提权命令（不执行）"""
    script = _entry_script()
    exe = sys.executable
    params = '"%s"' % script
    return {
        "python": exe,
        "script": script,
        "params": params,
        "cwd": os.getcwd(),
        "command": 'ShellExecuteW(None, "runas", "%s", \'%s\', "%s", 1)' % (exe, params, os.getcwd()),
    }


def restart_as_admin(dry_run: bool = False, delay_exit: float = 2.0) -> Dict[str, Any]:
    """请求以管理员身份重启当前程序

    - 已经管理员：直接返回，不做任何事
    - dry_run=True：只返回将要执行的命令（测试/预览用）
    - 否则：弹出 UAC 并启动新实例，随后退出当前进程
    """
    info = build_restart_command()
    if is_admin():
        return {"ok": False, "already_admin": True, "message": "当前已经是管理员权限", **info}
    if dry_run:
        return {"ok": True, "dry_run": True, "message": "预览：将弹出 UAC 并重启", **info}

    try:
        # SW_SHOWNORMAL=1；lpVerb=runas 触发 UAC
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", info["python"], info["params"], info["cwd"], 1
        )
        if int(rc) <= 32:
            logger.warning("提权重启失败，ShellExecuteW 返回 %s", rc)
            return {"ok": False, "message": "提权重启失败（ShellExecuteW=%s），请手动右键「以管理员身份运行」" % rc, **info}
    except Exception as e:
        logger.error("提权重启异常: %s", e)
        return {"ok": False, "message": "提权重启异常: %s" % e, **info}

    def _exit_later() -> None:
        time.sleep(max(0.5, delay_exit))
        logger.info("提权重启：退出当前进程")
        os._exit(0)

    threading.Thread(target=_exit_later, daemon=True).start()
    return {
        "ok": True,
        "message": "已请求管理员权限，请在 UAC 弹窗中确认；确认后本程序会自动重启",
        **info,
    }
