"""
进程操作工具 - 启动终止列出进程
"""
from __future__ import annotations

import os
import signal
import logging
import platform
import subprocess
from typing import List, Dict

logger = logging.getLogger("ZhiYanWeb.Computer.ProcessOps")


def list_processes() -> Dict:
    """列出所有运行中的进程"""
    processes = []
    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                pinfo = proc.info
                processes.append({
                    "pid": pinfo["pid"],
                    "name": pinfo["name"],
                    "cpu": round(pinfo["cpu_percent"] or 0, 1),
                    "memory": round(pinfo["memory_percent"] or 0, 1),
                    "status": pinfo["status"],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        processes.sort(key=lambda p: p["cpu"], reverse=True)
    except ImportError:
        # psutil 不可用时使用 tasklist（仅 Windows）
        if os.name != 'nt':
            return {
                "total": 0,
                "processes": [],
                "note": "未安装 psutil，无法在 %s 上列出进程" % platform.system(),
            }
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().split("\n"):
            parts = line.strip('"').split('","')
            if len(parts) >= 2:
                processes.append({
                    "pid": parts[1] if len(parts) > 1 else "?",
                    "name": parts[0] if parts else "?",
                    "cpu": "?",
                    "memory": "?",
                    "status": "running",
                })

    return {
        "total": len(processes),
        "processes": processes[:100],  # 最多返回 100 条
    }


def launch_process(command: str, args: str = "") -> Dict:
    """启动新进程"""
    if not os.path.exists(command) and not any(
        command.lower().endswith(ext) for ext in [".exe", ".bat", ".cmd", ".ps1"]
    ):
        # 尝试在 PATH 中查找
        import shutil
        found = shutil.which(command)
        if found:
            command = found
        else:
            # 尝试直接运行
            pass

    full_cmd = f'"{command}" {args}' if " " in command else f"{command} {args}"
    try:
        process = subprocess.Popen(
            full_cmd.strip(),
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {
            "pid": process.pid,
            "command": command,
            "args": args,
            "message": f"进程已启动 (PID: {process.pid})",
        }
    except Exception as e:
        raise RuntimeError(f"启动进程失败: {e}")


def kill_process(pid: int) -> Dict:
    """终止进程"""
    try:
        import psutil
        proc = psutil.Process(pid)
        proc_name = proc.name()
        proc.terminate()
        proc.wait(timeout=5)
        return {"pid": pid, "name": proc_name, "message": f"进程已终止 (PID: {pid})"}
    except ImportError:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        return {"pid": pid, "message": f"进程已终止 (PID: {pid})"}
    except psutil.NoSuchProcess:
        raise ValueError(f"进程不存在 (PID: {pid})")
    except psutil.AccessDenied:
        raise PermissionError(f"没有权限终止进程 (PID: {pid})")


def get_process_info(pid: int) -> Dict:
    """获取进程详细信息"""
    try:
        import psutil
        proc = psutil.Process(pid)
        with proc.oneshot():
            return {
                "pid": pid,
                "name": proc.name(),
                "exe": proc.exe(),
                "cwd": proc.cwd(),
                "status": proc.status(),
                "cpu_percent": proc.cpu_percent(),
                "memory_percent": proc.memory_percent(),
                "memory_rss": proc.memory_info().rss,
                "create_time": proc.create_time(),
                "num_threads": proc.num_threads(),
                "connections": len(proc.connections()),
                "open_files": len(proc.open_files()),
            }
    except ImportError:
        raise RuntimeError("需要安装 psutil 库: pip install psutil")
    except psutil.NoSuchProcess:
        raise ValueError(f"进程不存在 (PID: {pid})")
    except psutil.AccessDenied:
        raise PermissionError(f"没有权限访问进程 (PID: {pid})")


# ────────────────────────────────────────────────────────
# 打开软件（Agent「打开软件」工具）
# ────────────────────────────────────────────────────────

def is_executable_file(path: str) -> bool:
    """是否为可执行/可启动的程序入口文件"""
    if not os.path.isfile(path):
        return False
    exts = {".exe", ".bat", ".cmd", ".com", ".msc", ".lnk", ".ps1", ".vbs", ".jar"}
    return os.path.splitext(path)[1].lower() in exts


def open_application(path: str, args: str = "") -> Dict:
    """打开指定软件 / 应用 / 文档 / 文件夹

    流程：
    1. 接收软件路径参数，并展开环境变量 / 用户目录
    2. 验证软件路径与可执行性（路径必须存在）
    3. 执行打开操作（Windows 走系统关联；可执行文件带参数时用子进程启动）
    4. 返回结构化执行结果

    Args:
        path: 软件 / 应用 / 文档 / 目录的路径
        args: 可选，附加给可执行程序启动参数（仅对可执行文件生效）

    Returns:
        Dict: {success, path, message, [pid], [args_used]}
    """
    if not path or not str(path).strip():
        raise ValueError("缺少软件路径参数: path")

    raw_path = str(path).strip()
    abs_path = os.path.abspath(os.path.expandvars(os.path.expanduser(raw_path)))

    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"软件路径不存在: {abs_path}")

    info = {
        "success": True,
        "path": abs_path,
        "is_dir": os.path.isdir(abs_path),
    }

    # 目录 → 打开文件资源管理器
    if os.path.isdir(abs_path):
        if os.name == "nt":
            os.startfile(abs_path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", abs_path])
        else:
            subprocess.Popen(["xdg-open", abs_path])
        return {**info, "message": f"已打开目录: {abs_path}"}

    # 可执行文件带启动参数 → 通过子进程启动（进程独立于本软件）
    if args and is_executable_file(abs_path):
        import shlex
        cmd = [abs_path] + (shlex.split(args) if isinstance(args, str) else [])
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {
            **info,
            "pid": process.pid,
            "args_used": args,
            "message": f"软件已启动 (PID: {process.pid})",
        }

    # 默认打开（可执行 / 文档 / 快捷方式，使用系统关联程序，进程生命周期独立于本软件）
    try:
        if os.name == "nt":
            os.startfile(abs_path)
            message = "软件已通过系统默认方式打开"
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", abs_path])
            message = "软件已通过系统默认方式打开"
        else:
            subprocess.Popen(["xdg-open", abs_path])
            message = "软件已通过系统默认方式打开"
        return {**info, "message": message}
    except OSError as e:
        raise RuntimeError(f"打开软件失败: {e}")