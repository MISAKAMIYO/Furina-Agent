"""
TRSS-Yunzai 原生进程管理与 HTTP 探活。

当 miao_plugin.mode == "yunzai" 时，ZhiYan 不再拦截 # 前缀命令，改由真实
TRSS-Yunzai 原生处理（最接近原版 miao-plugin 工作流）。Yunzai 通过反向 WS
（监听 2536 端口 /OneBotv11 路径）接收 NapCat 推送，NapCat 侧需配置
websocketClients 指向 Yunzai；ZhiYan 则继续通过 NapCat 3001 处理非 # 消息。

本模块提供：
  - yunzai_status(): 健康检查（HTTP /status 探活 + 进程存活）
  - ensure_yunzai(): 确保 Yunzai 在运行（必要时先拉起 Redis 再启动子进程）
  - stop_yunzai(): 停止 Yunzai 子进程
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
import urllib.request

from zhiyan_web.config import get_settings, PROJECT_ROOT

logger = logging.getLogger("ZhiYanWeb.Yunzai")

# Yunzai 子进程句柄（限制单实例）
_process: subprocess.Popen | None = None
_redis_process: subprocess.Popen | None = None
_last_error: str | None = None

# Windows 隐藏控制台窗口标志（避免弹出系统黑框）
CREATE_NO_WINDOW = 0x08000000


def _config() -> dict:
    try:
        mp = get_settings().get("miao_plugin") or {}
        return mp.get("yunzai") or {}
    except Exception:
        return {}


def resolve_dir() -> str:
    """解析 Yunzai-main 目录（支持绝对路径或相对软件根目录）。"""
    cfg = _config()
    raw = (cfg.get("path") or "").strip()
    if not raw:
        return os.path.join(PROJECT_ROOT, "Yunzai-main")
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(PROJECT_ROOT, raw))


def _port() -> int:
    return int(_config().get("port") or 2536)


def _status_url() -> str:
    return f"http://127.0.0.1:{_port()}/status"


def _http_alive() -> bool:
    """通过 Yunzai 的 HTTP /status 端点探活。"""
    try:
        with urllib.request.urlopen(_status_url(), timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _redis_port_busy() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 6379), timeout=0.5):
            return True
    except OSError:
        return False


def _find_redis() -> str:
    """定位 redis-server.exe：配置项 > PATH > 软件根 redis/ 目录。"""
    cfg = _config()
    raw = (cfg.get("redis_path") or "").strip()
    if raw:
        candidates = [raw]
        if not os.path.isabs(raw):
            candidates.insert(0, os.path.normpath(os.path.join(PROJECT_ROOT, raw)))
    else:
        candidates = []
    candidates.append(shutil.which("redis-server") or "")
    candidates.append(os.path.join(PROJECT_ROOT, "redis", "redis-server.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


def _find_node() -> str:
    node = shutil.which("node")
    if node:
        return node
    # 兜底：Trae 内置 Node
    for p in (
        r"C:\Users\22811\.trae-cn\binaries\node\versions\24.19.0\node.exe",
    ):
        if os.path.isfile(p):
            return p
    return "node"


def yunzai_status() -> dict:
    """检查 Yunzai 是否存活。"""
    global _process
    alive = _http_alive()
    proc_alive = bool(_process and _process.poll() is None)
    return {
        "running": alive or proc_alive,
        "http_ok": alive,
        "pid": _process.pid if (proc_alive) else None,
        "port": _port(),
        "dir": resolve_dir(),
        "redis_path": _find_redis(),
    }


def ensure_redis() -> tuple[bool, str]:
    """确保 Redis 在运行（若已监听 6379 则跳过）。"""
    global _redis_process
    if _redis_port_busy():
        return True, "Redis 已在运行"
    redis_exe = _find_redis()
    if not redis_exe:
        return False, "未找到 redis-server.exe（可配置 miao_plugin.yunzai.redis_path）"
    try:
        _redis_process = subprocess.Popen(
            [redis_exe, "--port", "6379"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        logger.info(f"[yunzai] 已启动 Redis pid={_redis_process.pid}")
    except Exception as e:
        logger.warning(f"[yunzai] 启动 Redis 失败: {e}")
        return False, f"启动 Redis 失败: {e}"
    deadline = time.time() + 8
    while time.time() < deadline:
        if _redis_port_busy():
            return True, "Redis 已启动"
        time.sleep(0.5)
    return False, "Redis 启动超时"


def ensure_yunzai() -> tuple[bool, str]:
    """确保 TRSS-Yunzai 在运行；未运行则启动 Redis 与 Yunzai 子进程并等待就绪。"""
    global _process, _last_error
    if _http_alive():
        return True, "Yunzai 运行中"

    if _process and _process.poll() is None:
        time.sleep(3)
        if _http_alive():
            return True, "Yunzai 运行中"

    yz_dir = resolve_dir()
    if not os.path.isfile(os.path.join(yz_dir, "app.js")):
        _last_error = f"未找到 TRSS-Yunzai（app.js 缺失）: {yz_dir}"
        logger.error(_last_error)
        return False, _last_error

    redis_ok, redis_msg = ensure_redis()
    if not redis_ok:
        logger.warning(f"[yunzai] {redis_msg}（继续尝试启动 Yunzai，其可能自行拉起 Redis）")

    node = _find_node()
    cmd = [node, "app.js"]
    try:
        _process = subprocess.Popen(
            cmd,
            cwd=yz_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        logger.info(f"[yunzai] 已启动 TRSS-Yunzai pid={_process.pid} cwd={yz_dir}")
    except FileNotFoundError:
        _last_error = "未找到 node 命令，请确认本机已安装 Node.js"
        logger.error(_last_error)
        return False, _last_error
    except Exception as e:
        _last_error = f"启动 Yunzai 失败: {e}"
        logger.error(_last_error)
        return False, _last_error

    wait = float((get_settings().get("miao_plugin") or {}).get("startup_wait") or 30)
    deadline = time.time() + wait
    while time.time() < deadline:
        if _http_alive():
            return True, "TRSS-Yunzai 已启动"
        if _process.poll() is not None:
            _last_error = "TRSS-Yunzai 进程提前退出"
            logger.error(_last_error)
            return False, _last_error
        time.sleep(1)
    _last_error = f"TRSS-Yunzai 启动超时（{wait}s）"
    logger.error(_last_error)
    return False, _last_error


def stop_yunzai() -> tuple[bool, str]:
    """停止 Yunzai（以及由其拉起的 Redis）子进程。"""
    global _process, _redis_process
    killed = False
    if _process and _process.poll() is None:
        try:
            _process.terminate()
            try:
                _process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _process.kill()
        except Exception as e:
            logger.warning(f"[yunzai] 停止失败: {e}")
        _process = None
        killed = True
    if _redis_process and _redis_process.poll() is None:
        try:
            _redis_process.terminate()
            try:
                _redis_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _redis_process.kill()
        except Exception:
            pass
        _redis_process = None
    return True, "Yunzai 已停止" if killed else "Yunzai 未在运行"
