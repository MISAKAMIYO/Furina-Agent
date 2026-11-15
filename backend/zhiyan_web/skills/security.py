"""
Skills 多层安全管控模块

提供：
1. 代码自动扫描与安全检测（静态 AST 分析）
2. 细粒度权限控制与资源访问限制
3. 运行时行为监控与异常检测
4. 手动审核与白名单管理

安全等级:
  A - 完全安全（纯指令/无风险代码）
  B - 低风险（常规 I/O，如读文件、网络请求）
  C - 中风险（写文件、子进程等，需关注）
  D - 高风险（危险操作，如任意命令执行、eval、系统级操作）
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger("ZhiYanWeb.Skills.Security")

# ─────── 危险代码模式（静态扫描用） ───────

# 危险导入：绝对禁止级别
_DANGEROUS_IMPORTS = {
    "os": ["system", "popen", "spawnv", "spawnl", "walk", "remove", "rmdir", "unlink"],
    "subprocess": ["run", "call", "Popen", "check_output", "check_call"],
    "socket": ["socket", "create_connection"],
    "shutil": ["rmtree", "move", "copy", "copytree"],
    "tarfile": ["extractall", "extract"],
    "zipfile": ["extractall", "extract"],
    "shelve": None,
    "pickle": ["loads", "load"],
    "pty": None,
    "ctypes": None,
    "winreg": None,
    "sys": ["modules", "path"],
}

# 危险内置调用
_DANGEROUS_CALLS = ["eval", "exec", "__import__", "compile", "system", "popen",
                    "requests", "urllib", "curl", "wget"]

# 敏感 API Key / 密钥正则
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|authorization|apikey|access[_-]?key"
    r")\s*[=:\"]\s*['\"]?[A-Za-z0-9_\-]{12,}",
    re.IGNORECASE,
)

# 危险 URL 主机
_USER_DATA_PATTERN = re.compile(
    r"(?i)(\.ssh|\.aws|\.env\b|\.git/config|passwd|shadow|credentials|\.pem|id_rsa)"
)


def _scan_python_source(source: str, filename: str) -> List[Dict[str, Any]]:
    """对 Python 源码做静态扫描，返回 findings 列表"""
    findings = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return [{
            "type": "syntax", "severity": "high", "line": (e.lineno or 0),
            "code": "SYNTAX_ERR", "description": f"代码无法解析: {e.msg}",
        }]

    for node in ast.walk(tree):
        # 危险 import
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = (alias.name or "").split(".")[0].lower()
                if mod in _DANGEROUS_IMPORTS:
                    findings.append({
                        "type": "import", "severity": "high",
                        "line": node.lineno, "code": f"DANGEROUS_IMPORT_{mod.upper()}",
                        "description": f"导入了高风险模块 {mod}",
                        "node": mod,
                    })
        elif isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module.split(".")[0].lower()
            if mod in _DANGEROUS_IMPORTS:
                findings.append({
                    "type": "import", "severity": "high",
                    "line": node.lineno, "code": f"DANGEROUS_IMPORT_{mod.upper()}",
                    "description": f"导入了高风险模块 {mod}",
                    "node": mod,
                })

        # 危险函数调用
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in ("eval", "exec", "__import__", "compile", "system", "popen",
                        "requests", "curl", "wget", "pyautogui", "open"):
                findings.append({
                    "type": "call", "severity": "medium",
                    "line": node.lineno, "code": f"DANGEROUS_CALL_{name.upper()}",
                    "description": f"使用了敏感调用 {name}",
                    "node": name,
                })

    # 密钥检测
    for i, line in enumerate(source.splitlines(), 1):
        if _SECRET_PATTERN.search(line):
            findings.append({
                "type": "secret", "severity": "high",
                "line": i, "code": "HARDCODED_SECRET",
                "description": "代码中疑似硬编码密钥/口令",
            })
        if _USER_DATA_PATTERN.search(line):
            findings.append({
                "type": "dataleak", "severity": "middle",
                "line": i, "code": "SENSITIVE_PATH",
                "description": "访问了敏感路径（凭据/私钥/用户数据）",
            })

    return findings


def _severity_score(findings: List[Dict]) -> int:
    """根据 findings 计算风险分值（0-100，越高越危险）"""
    weight = {"low": 2, "middle": 6, "high": 15, "critical": 25}
    return min(100, sum(weight.get(f.get("severity", "low"), 3) for f in findings))


def _level_from_score(score: int) -> str:
    if score == 0:
        return "A"
    if score <= 15:
        return "B"
    if score <= 40:
        return "C"
    return "D"


def scan_skill_code(skill_path: str) -> Dict[str, Any]:
    """扫描技能目录内所有 Python 文件，返回安全评级

    Returns:
        {score, level, findings, scanned_files}
    """
    findings: List[Dict] = []
    scanned_files = []

    if not os.path.isdir(skill_path):
        return {"score": 0, "level": "A", "findings": [], "scanned_files": []}

    for root, _dirs, files in os.walk(skill_path):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    src = f.read()
            except Exception:
                continue
            rel = os.path.relpath(fpath, skill_path)
            scanned_files.append(rel)
            findings.extend(_scan_python_source(src, rel))

    score = _severity_score(findings)
    level = _level_from_score(score)
    return {
        "score": score,
        "level": level,
        "findings": findings,
        "scanned_files": scanned_files,
        "timestamp": datetime.now().isoformat(),
    }


class SkillPermit:
    """细粒度权限声明（基于 skill 元数据 requests，或由扫描推断）"""

    # 权限位
    NETWORK = "network"
    FILESYSTEM_READ = "fs_read"
    FILESYSTEM_WRITE = "fs_write"
    SUBPROCESS = "subprocess"
    BROWSER = "browser"
    SYSTEM = "system"
    NO_PERMISSION = "none"

    _DESC = {
        NETWORK: "网络访问", FILESYSTEM_READ: "文件读取",
        FILESYSTEM_WRITE: "文件写入", SUBPROCESS: "运行子进程",
        BROWSER: "浏览器操作", SYSTEM: "系统级操作", NO_PERMISSION: "无权限",
    }

    def __init__(self):
        self._granted: set = set()

    def grant(self, perm: str) -> None:
        self._granted.add(perm)

    def revoke(self, perm: str) -> None:
        self._granted.discard(perm)

    def has(self, perm: str) -> bool:
        return perm in self._granted

    def list(self) -> List[str]:
        return list(self._granted)

    def to_dict(self) -> List[str]:
        return self.list()

    @classmethod
    def from_scan(cls, findings: List[Dict]) -> "SkillPermit":
        """根据扫描 findings 推断需要申请的最低权限集合"""
        p = cls()
        for f in findings:
            node = (f.get("node") or "").lower()
            if node == "socket" or node == "requests" or f.get("code") in ("DANGEROUS_CALL_REQUESTS", "DANGEROUS_CALL_CURL", "DANGEROUS_CALL_WGET"):
                p.grant(cls.NETWORK)
            if node == "subprocess" or node == "os" or f.get("code") == "DANGEROUS_CALL_SYSTEM":
                p.grant(cls.SUBPROCESS)
            if node in ("shutil", "zipfile", "tarfile", "os") and f.get("type") == "import":
                p.grant(cls.FILESYSTEM_WRITE)
            if f.get("code") == "DANGEROUS_CALL_OPEN":
                p.grant(cls.FILESYSTEM_READ)
            if node in ("ctypes", "winreg", "pty") or f.get("severity") == "critical":
                p.grant(cls.SYSTEM)
        return p

    def describe(self) -> List[str]:
        return [self._DESC.get(p, p) for p in sorted(self._granted)]


# 社区技能「首次使用安全确认」状态标识（持久化到 skills_state.json 的 __meta__）
FIRST_USE_FLAG = "community_first_use_confirmed"


def level_color(level: str) -> str:
    """安全等级对应颜色（供前端）"""
    return {"A": "#22c55e", "B": "#3b82f6", "C": "#f59e0b", "D": "#ef4444"}.get(level, "#6b7280")


def level_label(level: str) -> str:
    return {"A": "完全安全", "B": "低风险", "C": "中风险", "D": "高风险"}.get(level, "未评估")