"""
系统操作工具 - 执行命令、获取系统信息
"""
from __future__ import annotations

import os
import asyncio
import logging
import platform
import subprocess
from typing import Dict

logger = logging.getLogger("ZhiYanWeb.Computer.SystemOps")


async def execute_command(command: str, timeout: int = 30) -> Dict:
    """执行系统命令（带超时和安全限制）

    Args:
        command: 要执行的命令
        timeout: 超时时间(秒)

    Returns:
        Dict: {stdout, stderr, returncode, duration}
    """
    import time
    start = time.time()

    try:
        kwargs = {}
        # Windows 下隐藏 cmd 控制台窗口，避免 AI 执行命令时闪现黑框
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.getcwd(),
            **kwargs,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {
                "stdout": "",
                "stderr": f"命令执行超时（{timeout}秒）",
                "returncode": -1,
                "duration": round(time.time() - start, 2),
            }

        duration = round(time.time() - start, 2)
        stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""

        # 截断过长输出
        MAX_OUTPUT = 10000
        if len(stdout_str) > MAX_OUTPUT:
            stdout_str = stdout_str[:MAX_OUTPUT] + f"\n... [输出已截断，共 {len(stdout_str)} 字符]"

        return {
            "stdout": stdout_str,
            "stderr": stderr_str,
            "returncode": process.returncode or 0,
            "duration": duration,
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
            "duration": round(time.time() - start, 2),
        }


def get_system_info() -> Dict:
    """获取系统信息"""
    info = {
        "os": platform.system(),
        "os_version": platform.version(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
    }

    try:
        import psutil
        info["cpu_count"] = psutil.cpu_count()
        info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        info["memory"] = {
            "total": psutil.virtual_memory().total,
            "available": psutil.virtual_memory().available,
            "percent": psutil.virtual_memory().percent,
        }
        info["disk"] = {
            "total": psutil.disk_usage("/").total,
            "free": psutil.disk_usage("/").free,
            "percent": psutil.disk_usage("/").percent,
        }
        info["boot_time"] = psutil.boot_time()
    except ImportError:
        info["_note"] = "安装 psutil 获取更详细的系统信息"

    return info


def get_environment() -> Dict:
    """获取环境变量（安全过滤）"""
    safe_vars = {}
    # 允许显示的环境变量
    allowed_prefixes = [
        "PATH", "HOME", "USER", "USERNAME", "COMPUTERNAME",
        "TEMP", "TMP", "APPDATA", "LOCALAPPDATA",
        "OS", "PROCESSOR", "SYSTEM", "NUMBER_OF_PROCESSORS",
        "PYTHON", "NODE", "JAVA",
    ]

    for key, value in os.environ.items():
        for prefix in allowed_prefixes:
            if key.upper().startswith(prefix):
                safe_vars[key] = value
                break

    return {
        "variables": safe_vars,
        "total_count": len(os.environ),
        "shown_count": len(safe_vars),
    }


def open_file_explorer(path: str = ".") -> Dict:
    """在文件资源管理器中打开路径"""
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"路径不存在: {abs_path}")

    if platform.system() == "Windows":
        os.startfile(abs_path)
    elif platform.system() == "Darwin":
        subprocess.run(["open", abs_path])
    else:
        subprocess.run(["xdg-open", abs_path])

    return {"path": abs_path, "message": f"已打开文件资源管理器: {abs_path}"}


def open_url(url: str) -> Dict:
    """在默认浏览器中打开 URL"""
    import webbrowser
    webbrowser.open(url)
    return {"url": url, "message": f"已打开 URL: {url}"}