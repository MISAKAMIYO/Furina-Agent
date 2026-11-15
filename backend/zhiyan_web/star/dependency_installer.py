"""插件依赖管理 - 解析、检测、自动安装 AstrBot 插件依赖

AstrBot 插件通常通过两种方式声明第三方依赖：
1. requirements.txt（逐行一个 pip 包，支持 ==/>=/<=/< 版本约束与行内注释）
2. _deps.json（AstrBot 官方格式，如 {"dependencies":[{"pipName": "...", "version": "..."}]}）

原版 AstrBot 安装插件时会自动读取上述依赖并 pip 安装。
本模块为自研内核补齐同样能力：
- 解析插件目录中的依赖声明
- 检测哪些依赖已安装、哪些缺失（通过 importlib.metadata，按 pip 名精确匹配）
- 调用 pip 自动安装缺失依赖（复用当前 Python 解释器，隐藏 Windows 黑框控制台）
- 记录已安装结果，支持查询依赖状态与手动触发安装
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import importlib.util
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.star")

# AstrBot 依赖声明文件名
REQUIREMENTS_FILENAME = "requirements.txt"
DEPS_JSON_FILENAME = "_deps.json"
# 记录文件：当前插件已完成安装的依赖（缓存，避免每次加载重复探测）
DEPS_INSTALLED_RECORD = ".deps_installed.json"


class DependencyInstaller:
    """插件依赖安装器（进程级单例）

    负责解析插件依赖、检测缺失项，并通过 pip 自动安装。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # plugin_dir -> asyncio.Task 记录进行中的安装任务
        self._running: Dict[str, asyncio.Task] = {}
        # 最近一次各插件的安装记录（含失败信息），供查询
        self._history: Dict[str, Dict[str, Any]] = {}

    # ────────── 依赖解析 ──────────

    @staticmethod
    def parse_dependencies(plugin_dir: str) -> List[Dict[str, str]]:
        """解析插件目录声明的依赖。

        Returns:
            有序依赖列表：[{"name": 包名, "spec": 完整 pip 规格(用于安装), "version": 约束或 ""}, ...]
            仅返回 requirements.txt 与 _deps.json 中有效声明的依赖。
        """
        deps: List[Dict[str, str]] = []

        # 1. requirements.txt
        req_path = os.path.join(plugin_dir, REQUIREMENTS_FILENAME)
        seen = set()
        if os.path.exists(req_path):
            try:
                with open(req_path, "r", encoding="utf-8", errors="ignore") as f:
                    for raw in f.readlines():
                        line = raw.strip()
                        if not line or line.startswith("#"):
                            continue
                        # 剥离行内注释（# 之前有效）
                        spec = line.split("#", 1)[0].strip()
                        if not spec:
                            continue
                        name, version = _split_name_version(spec)
                        if name and name.lower() not in seen:
                            seen.add(name.lower())
                            deps.append({
                                "name": name,
                                "spec": spec,
                                "version": version,
                            })
            except Exception as e:
                logger.warning("解析 %s 失败: %s", req_path, e)

        # 2. _deps.json（AstrBot 官方格式）
        deps_path = os.path.join(plugin_dir, DEPS_JSON_FILENAME)
        if os.path.exists(deps_path):
            try:
                with open(deps_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries = []
                if isinstance(data, dict):
                    entries = data.get("dependencies", []) or []
                elif isinstance(data, list):
                    entries = data
                for item in entries:
                    if isinstance(item, str):
                        name = item.strip()
                        version = ""
                    elif isinstance(item, dict):
                        name = (item.get("pipName") or item.get("name") or "").strip()
                        version = (item.get("version") or "").strip()
                    else:
                        continue
                    if name and name.lower() not in seen:
                        seen.add(name.lower())
                        spec = f"{name}=={version}" if version else name
                        deps.append({"name": name, "spec": spec, "version": version})
            except Exception as e:
                logger.warning("解析 %s 失败: %s", deps_path, e)

        return deps

    # ────────── 依赖检测 ──────────

    @staticmethod
    def is_installed(name: str) -> bool:
        """检测 pip 包是否已安装（按 metadata 名精确匹配，忽略大小写）。"""
        try:
            importlib.metadata.version(name)
            return True
        except importlib.metadata.PackageNotFoundError:
            return False
        except Exception:
            return False

    def get_dependency_status(self, plugin_dir: str) -> Dict[str, Any]:
        """查询插件依赖状态：已安装/缺失/进行中/最近结果。

        Returns:
            {
                "requirements_file": bool, "_deps_file": bool,
                "total": int, "installed": int, "missing": [{"name","spec","version"}],
                "running": bool, "last": None | dict,   # 最近一次安装结果
            }
        """
        deps = self.parse_dependencies(plugin_dir)
        missing = []
        installed = 0
        for dep in deps:
            if self.is_installed(dep["name"]):
                installed += 1
            else:
                missing.append(dep)

        last = self._history.get(plugin_dir)
        return {
            "requirements_file": os.path.exists(os.path.join(plugin_dir, REQUIREMENTS_FILENAME)),
            "_deps_file": os.path.exists(os.path.join(plugin_dir, DEPS_JSON_FILENAME)),
            "total": len(deps),
            "installed": installed,
            "missing": missing,
            "running": plugin_dir in self._running,
            "last": last,
        }

    # ────────── 自动安装 ──────────

    async def ensure_dependencies(
        self,
        plugin_name: str,
        plugin_dir: str,
        auto: bool = True,
    ) -> Dict[str, Any]:
        """确保插件依赖已安装。

        若存在进行中的安装任务则等待其完成；否则过滤缺失依赖并安装。

        Returns:
            {"status": "installed"|"skip"|"running"|"failed",
             "installed_count": int, "missing": [...], "message": str,
             "detail": [{"name","spec","success","output"}]}
        """
        # 已存在进行中任务 -> 等待
        if plugin_dir in self._running:
            task = self._running[plugin_dir]
            try:
                await task
            except Exception as e:
                logger.error("等待插件 %s 依赖安装任务失败: %s", plugin_name, e)
            return {
                "status": "running",
                "installed_count": 0,
                "missing": self.parse_dependencies(plugin_dir),
                "message": f"依赖安装仍在进行中: {plugin_name}",
                "detail": [],
            }

        deps = self.parse_dependencies(plugin_dir)
        missing = [d for d in deps if not self.is_installed(d["name"])]
        if not missing:
            self._history[plugin_dir] = {
                "time": _now_iso(),
                "status": "skip",
                "installed_count": 0,
                "message": "插件依赖均已安装，无需处理",
            }
            return {
                "status": "skip" if not missing else "installed",
                "installed_count": 0,
                "missing": [],
                "message": "插件依赖均已安装",
                "detail": [],
            }

        if not auto:
            return {
                "status": "skip",
                "installed_count": 0,
                "missing": missing,
                "message": f"有 {len(missing)} 个依赖未安装（未自动安装模式）",
                "detail": [],
            }

        # 后台安装（异步执行，不阻塞加载），供 UI 轮询进度
        task = asyncio.create_task(self._install_missing(plugin_name, plugin_dir, missing))
        self._running[plugin_dir] = task
        task.add_done_callback(lambda t: self._running.pop(plugin_dir, None))
        try:
            return await task
        except Exception as e:
            logger.error("插件 %s 依赖安装异常: %s", plugin_name, e)
            self._running.pop(plugin_dir, None)
            return {
                "status": "failed",
                "installed_count": 0,
                "missing": missing,
                "message": f"依赖安装异常: {e}",
                "detail": [],
            }

    async def install_missing(self, plugin_name: str, plugin_dir: str) -> Dict[str, Any]:
        """手动触发安装缺失依赖（await 直到完成）。"""
        deps = self.parse_dependencies(plugin_dir)
        missing = [d for d in deps if not self.is_installed(d["name"])]
        if not missing:
            return {
                "status": "skip",
                "installed_count": 0,
                "missing": [],
                "message": "插件依赖均已安装",
                "detail": [],
            }
        task = asyncio.create_task(self._install_missing(plugin_name, plugin_dir, missing))
        self._running[plugin_dir] = task
        task.add_done_callback(lambda t: self._running.pop(plugin_dir, None))
        result = await task
        self._history[plugin_dir] = {
            "time": _now_iso(),
            "status": result["status"],
            "installed_count": result["installed_count"],
            "message": result["message"],
        }
        return result

    async def _install_missing(
        self,
        plugin_name: str,
        plugin_dir: str,
        missing: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """逐项 pip 安装缺失依赖。"""
        async with self._lock:
            detail = []
            installed_count = 0
            failed_any = False
            for dep in missing:
                name = dep["name"]
                spec = dep["spec"]
                ok, output = await self._pip_install(spec)
                detail.append({
                    "name": name,
                    "spec": spec,
                    "success": ok,
                    "output": _truncate_output(output),
                })
                if ok:
                    installed_count += 1
                else:
                    failed_any = True
                    logger.warning("插件 %s 依赖 %s 安装失败: %s", plugin_name, spec, output.strip()[:300])

            if failed_any:
                failed_names = [d["name"] for d in detail if not d["success"]]
                return {
                    "status": "failed",
                    "installed_count": installed_count,
                    "missing": [d for d in missing if not self.is_installed(d["name"])],
                    "message": f"部分依赖安装失败: {', '.join(failed_names)}",
                    "detail": detail,
                }
            return {
                "status": "installed",
                "installed_count": installed_count,
                "missing": [],
                "message": f"已安装 {installed_count} 个依赖",
                "detail": detail,
            }

    @staticmethod
    async def _pip_install(spec: str) -> tuple:
        """调用 pip 安装单个包，隐藏 Windows 控制台黑框。

        Returns:
            (ok, output_text)
        """
        python_exe = sys.executable or "python"
        cmd = [python_exe, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", spec]
        extra_createflags = 0
        if os.name == "nt":
            extra_createflags = 0x08000000  # CREATE_NO_WINDOW

        for _retry in range(2):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    creationflags=extra_createflags,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
                text = out.decode("utf-8", errors="replace") if out else ""
                return (proc.returncode == 0, text)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                return (False, f"安装 {spec} 超时（300 秒）")
            except FileNotFoundError:
                # pip 模块缺失，尝试 pip3
                if cmd[1] == "-m":
                    cmd = [python_exe, "-m", "pip3", "install", "--disable-pip-version-check", "--no-input", spec]
                    continue
                return (False, "找不到 pip，请先安装 pip")
            except Exception as e:
                return (False, f"pip 执行异常: {e}")
        return (False, "pip 安装重试后仍失败")


# ────────── 辅助函数 ──────────

def _split_name_version(spec: str) -> tuple:
    """从 pip 规格中拆出基础包名与版本约束。

    支持：numpy / numpy==1.26.0 / numpy>=1.20 / numpy~=1.0 / numpy[extra]==1.0
    返回 (base_name, version_constraint_or_empty)
    """
    name_part = spec
    version = ""
    for sep in ("==", ">=", "<=", "~=", ">", "<", "!="):
        idx = name_part.find(sep)
        if idx != -1:
            name_part = name_part[:idx].strip()
            version = spec[idx:].strip()
            break
    # 去掉 extras 标记 [xxx]
    if "[" in name_part:
        name_part = name_part.split("[", 1)[0].strip()
    return name_part, version


def _truncate_output(text: str, limit: int = 500) -> str:
    if not text:
        return ""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[-limit:]


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# 进程级单例
_installer: Optional[DependencyInstaller] = None


def get_dependency_installer() -> DependencyInstaller:
    """获取依赖安装器单例。"""
    global _installer
    if _installer is None:
        _installer = DependencyInstaller()
    return _installer