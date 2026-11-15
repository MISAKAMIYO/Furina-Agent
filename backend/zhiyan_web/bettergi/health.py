"""
BetterGI 健康检查与自愈辅助

检查项（每项返回 key / name / ok / detail）：
- bettergi：BetterGI 进程是否存活
- game：游戏进程是否存活
- window：游戏窗口是否响应（IsHungAppWindow）
- log：BetterGI 日志是否仍在更新
- network：外网连通性

本模块只做「探测 + 轻量操作」，自愈顺序与重试策略由 service.py 调度：
重启 BetterGI → 拉起游戏 → 关闭无响应窗口 → 网络恢复 → 放弃

说明：
- 所有探测都是只读操作，失败只返回 ok=False，不抛异常
- 找不到 psutil / pywin32 时对应检查返回不可用，不阻塞其他项
"""
from __future__ import annotations

import logging
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ZhiYanWeb.bettergi.health")

# 游戏进程名（国服 / 国际服 / 云原神）
GAME_PROCESS_NAMES = (
    "yuanshen.exe",
    "genshinimpact.exe",
    "yuanshen_cloud.exe",
    "cloudgenshin.exe",
    "gengshinimpact.exe",
)

# 游戏窗口标题关键词（与 vision.WINDOW_KEYWORDS 保持一致）
GAME_WINDOW_KEYWORDS = ("原神", "genshin", "yuanshen", "云·原神")

# 网络探测目标（DNS 端口，成功一个即视为连通）
NETWORK_TARGETS: Tuple[Tuple[str, int], ...] = (("223.5.5.5", 53), ("119.29.29.29", 53))

CHECK_NAMES = {
    "bettergi": "BetterGI 进程",
    "game": "游戏进程",
    "window": "游戏窗口响应",
    "log": "日志活性",
    "network": "网络连通",
}


def _psutil():
    try:
        import psutil

        return psutil
    except Exception:
        return None


def check_bettergi(process_name: str = "BetterGI.exe") -> Dict[str, Any]:
    """检查 BetterGI 进程"""
    ps = _psutil()
    if ps is None:
        return {"key": "bettergi", "name": CHECK_NAMES["bettergi"], "ok": False, "detail": "缺少 psutil，无法检查"}
    target = (process_name or "BetterGI.exe").strip().lower()
    stem = target.rsplit(".", 1)[0]
    pids: List[int] = []
    for proc in ps.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if not name:
                continue
            if name == target or name.startswith(stem):
                pids.append(proc.info.get("pid"))
        except Exception:
            continue
    detail = f"{len(pids)} 个进程 {pids[:5]}" if pids else "未运行"
    return {"key": "bettergi", "name": CHECK_NAMES["bettergi"], "ok": bool(pids), "detail": detail}


def check_game() -> Dict[str, Any]:
    """检查游戏进程"""
    ps = _psutil()
    if ps is None:
        return {"key": "game", "name": CHECK_NAMES["game"], "ok": False, "detail": "缺少 psutil，无法检查"}
    pids: List[int] = []
    names: List[str] = []
    for proc in ps.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in GAME_PROCESS_NAMES or "yuanshen" in name or "genshin" in name:
                pids.append(proc.info.get("pid"))
                names.append(name)
        except Exception:
            continue
    detail = f"{len(pids)} 个进程 {names[:3]}" if pids else "未运行"
    return {"key": "game", "name": CHECK_NAMES["game"], "ok": bool(pids), "detail": detail}


def check_window() -> Dict[str, Any]:
    """检查游戏窗口是否响应（无响应即判定为卡死）"""
    try:
        import win32gui
    except Exception:
        return {"key": "window", "name": CHECK_NAMES["window"], "ok": False, "detail": "缺少 pywin32，无法检查"}

    found: List[Dict[str, Any]] = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            lower = title.lower()
            if not any(k in lower for k in GAME_WINDOW_KEYWORDS):
                return
            hung = False
            is_hung = getattr(win32gui, "IsHungAppWindow", None)
            if callable(is_hung):
                hung = bool(is_hung(hwnd))
            found.append({"hwnd": hwnd, "title": title, "hung": hung})
        except Exception:
            return

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception as e:
        return {"key": "window", "name": CHECK_NAMES["window"], "ok": False, "detail": f"枚举窗口失败: {e}"}

    if not found:
        return {"key": "window", "name": CHECK_NAMES["window"], "ok": False, "detail": "未找到游戏窗口"}
    hung_items = [w for w in found if w["hung"]]
    if hung_items:
        return {
            "key": "window",
            "name": CHECK_NAMES["window"],
            "ok": False,
            "detail": "窗口无响应: " + "，".join(f"{w['title']}(hwnd={w['hwnd']})" for w in hung_items[:3]),
            "hung": [w["hwnd"] for w in hung_items],
        }
    return {"key": "window", "name": CHECK_NAMES["window"], "ok": True, "detail": f"窗口正常: {found[0]['title']}"}


def check_log(log_mtime: float, stall_seconds: int, stall_timeout: int = 600) -> Dict[str, Any]:
    """检查日志活性：超过阈值未更新视为异常（任务未运行时只看日志本身是否可读）"""
    if not log_mtime:
        return {"key": "log", "name": CHECK_NAMES["log"], "ok": False, "detail": "未找到 BetterGI 日志文件"}
    age = max(0, int(time.time() - float(log_mtime)))
    threshold = max(60, int(stall_timeout))
    if stall_seconds <= 0:
        return {"key": "log", "name": CHECK_NAMES["log"], "ok": True, "detail": f"最近更新 {age} 秒前（无运行中任务）"}
    ok = age <= threshold
    detail = f"最近更新 {age} 秒前" if ok else f"日志已 {age} 秒未更新（阈值 {threshold} 秒）"
    return {"key": "log", "name": CHECK_NAMES["log"], "ok": ok, "detail": detail}


def check_network(timeout: float = 3.0) -> Dict[str, Any]:
    """检查外网连通性"""
    errors: List[str] = []
    for host, port in NETWORK_TARGETS:
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.close()
            return {"key": "network", "name": CHECK_NAMES["network"], "ok": True, "detail": f"{host}:{port} 可连通"}
        except Exception as e:
            errors.append(f"{host}:{port} {e}")
    return {
        "key": "network",
        "name": CHECK_NAMES["network"],
        "ok": False,
        "detail": "所有探测目标均不可达: " + "；".join(errors[:2]),
    }


def run_checks(
    log_mtime: float = 0.0,
    stall_seconds: int = 0,
    stall_timeout: int = 600,
    process_name: str = "BetterGI.exe",
    network_timeout: float = 3.0,
) -> List[Dict[str, Any]]:
    """执行全部检查项（阻塞，调用方应放到线程里）"""
    checks: List[Dict[str, Any]] = []
    for fn in (
        lambda: check_bettergi(process_name),
        check_game,
        check_window,
        lambda: check_log(log_mtime, stall_seconds, stall_timeout),
        lambda: check_network(network_timeout),
    ):
        try:
            checks.append(fn())
        except Exception as e:  # 单项异常不影响整条流水线
            logger.warning(f"健康检查项异常: {e}")
    return checks


def summarize(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总检查结果"""
    problems = [c.get("key") for c in checks if not c.get("ok")]
    return {
        "ok": not problems,
        "problems": problems,
        "problem_names": [CHECK_NAMES.get(k, k) for k in problems],
        "checks": checks,
        "ts": time.time(),
    }


def game_process_info() -> Dict[str, Any]:
    """游戏进程详情：pids、最早启动时间、已存活小时数（用于 12 小时规则）"""
    ps = _psutil()
    if ps is None:
        return {"pids": [], "oldest": 0.0, "alive_hours": 0.0, "message": "缺少 psutil"}
    pids: List[int] = []
    oldest = 0.0
    names: List[str] = []
    for proc in ps.process_iter(["pid", "name", "create_time"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in GAME_PROCESS_NAMES or "yuanshen" in name or "genshin" in name:
                pids.append(proc.info.get("pid"))
                names.append(name)
                created = float(proc.info.get("create_time") or 0)
                if created and (not oldest or created < oldest):
                    oldest = created
        except Exception:
            continue
    alive_hours = (time.time() - oldest) / 3600.0 if oldest else 0.0
    return {"pids": pids, "names": names[:3], "oldest": oldest, "alive_hours": alive_hours}


# ============= 轻量自愈动作 =============

def close_hung_windows(hwnds: Optional[List[int]] = None) -> Dict[str, Any]:
    """向无响应的游戏窗口发送 WM_CLOSE（不强制杀进程）"""
    try:
        import win32con
        import win32gui
    except Exception:
        return {"ok": False, "message": "缺少 pywin32，无法发送关闭指令"}

    targets = list(hwnds or [])
    if not targets:
        # 没传 hwnd 就现场再找一遍
        probe = check_window()
        targets = list(probe.get("hung") or [])
    if not targets:
        return {"ok": True, "message": "没有需要关闭的无响应窗口", "closed": []}

    closed: List[int] = []
    for hwnd in targets:
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            closed.append(hwnd)
        except Exception as e:
            logger.warning(f"关闭窗口 {hwnd} 失败: {e}")
    return {"ok": bool(closed), "message": f"已向 {len(closed)} 个窗口发送关闭指令", "closed": closed}


def flush_dns() -> Dict[str, Any]:
    """刷新 DNS 缓存（网络恢复里最安全的一步）"""
    import subprocess

    try:
        proc = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=15)
    except Exception as e:
        return {"ok": False, "message": f"刷新 DNS 失败: {e}"}
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "message": "DNS 缓存已刷新" if ok else f"刷新 DNS 返回码 {proc.returncode}",
    }
