"""
统一安全控制系统 (Safety Manager)

为 AI Agent 提供多层安全控制：
1. 命令白名单/黑名单
2. 文件路径访问控制
3. 危险操作审核
4. 操作权限分级
5. 自动审批规则

所有 Agent 操作都应通过此系统进行安全检查。
"""
from __future__ import annotations

import os
import re
import asyncio
import logging
from enum import Enum
from typing import Dict, List, Optional, Set, Callable, Awaitable
from dataclasses import dataclass, field

logger = logging.getLogger("ZhiYanWeb.Computer.Safety")

# ─── 风险等级 ───────────────────────────────────────────

class RiskLevel(Enum):
    SAFE = "safe"           # 安全操作，自动执行
    LOW = "low"             # 低风险，记录日志即可
    NORMAL = "normal"       # 需要用户确认
    HIGH = "high"           # 高危，需要用户确认 + 警告
    FORBIDDEN = "forbidden" # 禁止执行

# ─── 操作分类 ───────────────────────────────────────────

@dataclass
class OperationPolicy:
    """操作策略"""
    operation: str
    risk_level: RiskLevel
    description: str
    require_approval: bool = True
    require_path_check: bool = False
    allowed_dirs: List[str] = field(default_factory=list)
    auto_approve_patterns: List[str] = field(default_factory=list)

# ─── 默认安全策略 ───────────────────────────────────────

DEFAULT_OPERATION_POLICIES: Dict[str, OperationPolicy] = {
    # ── 文件操作 ──
    "read_file": OperationPolicy(
        operation="read_file", risk_level=RiskLevel.SAFE,
        description="读取文件内容", require_approval=False,
        require_path_check=True,
    ),
    "write_file": OperationPolicy(
        operation="write_file", risk_level=RiskLevel.NORMAL,
        description="写入文件内容", require_approval=True,
        require_path_check=True,
        auto_approve_patterns=[r"\.(txt|md|json|py|js|ts|html|css|yaml|yml|toml|ini|cfg|log|csv|xml)$"],
    ),
    "edit_file": OperationPolicy(
        operation="edit_file", risk_level=RiskLevel.NORMAL,
        description="编辑文件内容", require_approval=True,
        require_path_check=True,
        auto_approve_patterns=[r"\.(txt|md|json|py|js|ts|html|css|yaml|yml|toml|ini|cfg|log|csv|xml)$"],
    ),
    "delete_file": OperationPolicy(
        operation="delete_file", risk_level=RiskLevel.HIGH,
        description="删除文件", require_approval=True,
        require_path_check=True,
    ),
    "create_directory": OperationPolicy(
        operation="create_directory", risk_level=RiskLevel.LOW,
        description="创建目录", require_approval=False,
        require_path_check=True,
    ),
    "list_files": OperationPolicy(
        operation="list_files", risk_level=RiskLevel.SAFE,
        description="列出目录文件", require_approval=False,
    ),
    "search_files": OperationPolicy(
        operation="search_files", risk_level=RiskLevel.SAFE,
        description="搜索文件内容", require_approval=False,
    ),
    "get_file_info": OperationPolicy(
        operation="get_file_info", risk_level=RiskLevel.SAFE,
        description="获取文件信息", require_approval=False,
    ),
    
    # ── 命令执行 ──
    "execute_command": OperationPolicy(
        operation="execute_command", risk_level=RiskLevel.NORMAL,
        description="执行系统命令", require_approval=True,
    ),
    "command_prompt_execute": OperationPolicy(
        operation="command_prompt_execute", risk_level=RiskLevel.NORMAL,
        description="通过命令提示符执行命令", require_approval=True,
    ),
    
    # ── 进程操作 ──
    "list_processes": OperationPolicy(
        operation="list_processes", risk_level=RiskLevel.SAFE,
        description="列出进程", require_approval=False,
    ),
    "launch_process": OperationPolicy(
        operation="launch_process", risk_level=RiskLevel.HIGH,
        description="启动新进程", require_approval=True,
    ),
    "kill_process": OperationPolicy(
        operation="kill_process", risk_level=RiskLevel.HIGH,
        description="终止进程", require_approval=True,
    ),
    
    # ── 系统操作 ──
    "get_system_info": OperationPolicy(
        operation="get_system_info", risk_level=RiskLevel.SAFE,
        description="获取系统信息", require_approval=False,
    ),
    "open_url": OperationPolicy(
        operation="open_url", risk_level=RiskLevel.LOW,
        description="打开 URL", require_approval=False,
    ),
    
    # ── 桌面操作 ──
    "take_screenshot": OperationPolicy(
        operation="take_screenshot", risk_level=RiskLevel.LOW,
        description="截取屏幕截图", require_approval=False,
    ),
    "get_mouse_position": OperationPolicy(
        operation="get_mouse_position", risk_level=RiskLevel.SAFE,
        description="获取鼠标位置", require_approval=False,
    ),
    "move_mouse": OperationPolicy(
        operation="move_mouse", risk_level=RiskLevel.NORMAL,
        description="移动鼠标", require_approval=True,
    ),
    "click_mouse": OperationPolicy(
        operation="click_mouse", risk_level=RiskLevel.NORMAL,
        description="点击鼠标", require_approval=True,
    ),
    "type_text": OperationPolicy(
        operation="type_text", risk_level=RiskLevel.NORMAL,
        description="输入文本", require_approval=True,
    ),
    "press_key": OperationPolicy(
        operation="press_key", risk_level=RiskLevel.NORMAL,
        description="按下按键", require_approval=True,
    ),
    "get_screen_size": OperationPolicy(
        operation="get_screen_size", risk_level=RiskLevel.SAFE,
        description="获取屏幕分辨率", require_approval=False,
    ),
    
    # ── Web 操作 ──
    "web_fetch": OperationPolicy(
        operation="web_fetch", risk_level=RiskLevel.SAFE,
        description="获取网页内容", require_approval=False,
    ),
    "web_search": OperationPolicy(
        operation="web_search", risk_level=RiskLevel.SAFE,
        description="执行网页搜索", require_approval=False,
    ),
    "web_screenshot": OperationPolicy(
        operation="web_screenshot", risk_level=RiskLevel.LOW,
        description="获取网页截图", require_approval=False,
    ),
    "browser_navigate": OperationPolicy(
        operation="browser_navigate", risk_level=RiskLevel.LOW,
        description="浏览器导航到 URL", require_approval=False,
    ),
    "browser_click": OperationPolicy(
        operation="browser_click", risk_level=RiskLevel.NORMAL,
        description="点击浏览器元素", require_approval=True,
    ),
    "browser_type": OperationPolicy(
        operation="browser_type", risk_level=RiskLevel.NORMAL,
        description="在浏览器中输入文字", require_approval=True,
    ),
    "browser_screenshot": OperationPolicy(
        operation="browser_screenshot", risk_level=RiskLevel.LOW,
        description="截取浏览器页面截图", require_approval=False,
    ),
    "browser_evaluate": OperationPolicy(
        operation="browser_evaluate", risk_level=RiskLevel.HIGH,
        description="在浏览器中执行 JavaScript", require_approval=True,
    ),
    
    # ── Skills 操作 ──
    "execute_skill": OperationPolicy(
        operation="execute_skill", risk_level=RiskLevel.NORMAL,
        description="执行技能", require_approval=True,
    ),
}

# 允许的命令前缀（白名单）
ALLOWED_COMMAND_PREFIXES: List[str] = [
    "python", "python3", "pip", "pip3", "npm", "npx", "node",
    "git", "dir", "ls", "pwd", "cd", "echo", "type", "cat",
    "mkdir", "copy", "move", "ren", "del",
    "where", "which", "findstr", "find", "sort",
    "systeminfo", "tasklist", "netstat", "ipconfig", "ping",
    "chcp", "date", "time", "ver", "whoami",
    "powershell", "pwsh",
    "curl", "wget",
]

# 禁止执行的命令/模式
FORBIDDEN_COMMAND_PATTERNS: List[str] = [
    r"^format\s", r"^fdisk\s", r"^diskpart\s", r"^reg\s+delete\s",
    r"^shutdown\s", r"^restart\s", r"^reboot\s",
    r"^del\s+/f", r"^rm\s+-rf", r"^rmdir\s+/s", r"^rd\s+/s",
    r"^net\s+user\s", r"^net\s+localgroup\s",
    r"^dd\s+", r"^mkfs\s", r"^fsck\s",
    r"^pvcreate\s", r"^lvcreate\s",
    r"^chmod\s+777", r"^chown\s",
    r"^sudo\s+rm", r"^sudo\s+shutdown",
]

# 安全目录白名单
SAFE_DIRS: List[str] = [
    os.path.expanduser("~\\Desktop"),
    os.path.expanduser("~\\Documents"),
    os.path.expanduser("~\\Downloads"),
    os.path.expanduser("~\\Pictures"),
    os.path.expanduser("~\\Music"),
    os.path.expanduser("~\\Videos"),
    os.path.expanduser("~\\AppData\\Local\\Temp"),
]


class SafetyManager:
    """统一安全控制器（单例）"""

    _instance: Optional['SafetyManager'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self._policies: Dict[str, OperationPolicy] = dict(DEFAULT_OPERATION_POLICIES)
        self._allowed_prefixes: List[str] = list(ALLOWED_COMMAND_PREFIXES)
        self._forbidden_patterns: List[str] = list(FORBIDDEN_COMMAND_PATTERNS)
        self._safe_dirs: List[str] = list(SAFE_DIRS)
        self._session_authorized_paths: Dict[str, Set[str]] = {}
        self._auto_approve_operations: Set[str] = set()
        self._approval_callbacks: List[Callable] = []
        self._audit_log: List[Dict] = []
        self._max_audit = 1000

    # ──── 策略管理 ────

    def get_policy(self, operation: str) -> OperationPolicy:
        """获取操作策略"""
        return self._policies.get(operation, OperationPolicy(
            operation=operation, risk_level=RiskLevel.NORMAL,
            description="未知操作", require_approval=True,
        ))

    def set_policy(self, policy: OperationPolicy) -> None:
        """设置操作策略"""
        self._policies[policy.operation] = policy

    def get_risk_level(self, operation: str, params: Dict = None) -> RiskLevel:
        """获取操作的风险等级"""
        policy = self.get_policy(operation)
        risk = policy.risk_level

        # 命令执行特殊检查
        if operation == "execute_command" and params:
            command = params.get("command", "")
            risk = self._evaluate_command_risk(command)

        # 文件操作路径检查
        if operation in ("write_file", "edit_file", "delete_file") and params:
            path = params.get("path", "")
            if self._is_safe_path(path):
                # 安全目录内的文件操作降低风险
                if risk == RiskLevel.NORMAL:
                    risk = RiskLevel.LOW

        return risk

    def requires_approval(self, operation: str, params: Dict = None) -> bool:
        """检查操作是否需要审批"""
        if operation in self._auto_approve_operations:
            return False

        policy = self.get_policy(operation)
        if not policy.require_approval:
            return False

        # 检查自动审批模式
        risk = self.get_risk_level(operation, params)
        if risk == RiskLevel.SAFE:
            return False

        # 文件操作检查扩展名自动审批
        if policy.require_path_check and params:
            path = params.get("path", "")
            for pattern in policy.auto_approve_patterns:
                if re.search(pattern, path, re.IGNORECASE):
                    return False

        return risk in (RiskLevel.NORMAL, RiskLevel.HIGH)

    # ──── 命令安全检查 ────

    def _evaluate_command_risk(self, command: str) -> RiskLevel:
        """评估命令风险等级"""
        cmd_lower = command.strip().lower()

        # 检查禁止模式
        for pattern in self._forbidden_patterns:
            if re.match(pattern, cmd_lower):
                return RiskLevel.FORBIDDEN

        # 检查白名单
        for prefix in self._allowed_prefixes:
            if cmd_lower.startswith(prefix.lower()):
                return RiskLevel.LOW

        # 不在白名单中
        return RiskLevel.NORMAL

    def check_command_safety(self, command: str) -> Dict:
        """检查命令安全性

        Returns:
            Dict: {safe, risk_level, message}
        """
        risk = self._evaluate_command_risk(command)
        if risk == RiskLevel.FORBIDDEN:
            return {"safe": False, "risk_level": "forbidden", "message": "该命令被禁止执行"}
        elif risk == RiskLevel.NORMAL:
            return {"safe": True, "risk_level": "normal", "message": "命令不在白名单，需要用户确认"}
        else:
            return {"safe": True, "risk_level": "safe", "message": "命令安全"}

    # ──── 路径安全检查 ────

    def authorize_path(self, path: str, session_id: str = "default") -> None:
        """授权访问特定路径"""
        if session_id not in self._session_authorized_paths:
            self._session_authorized_paths[session_id] = set()
        self._session_authorized_paths[session_id].add(os.path.abspath(path))

    def _is_safe_path(self, path: str) -> bool:
        """检查路径是否在安全目录中"""
        abs_path = os.path.abspath(path)
        for safe_dir in self._safe_dirs:
            if abs_path.startswith(safe_dir):
                return True
        return False

    def is_path_allowed(self, path: str, session_id: str = "default") -> bool:
        """检查路径是否允许访问"""
        abs_path = os.path.abspath(path)
        # 安全目录
        if self._is_safe_path(path):
            return True
        # 会话授权路径
        for authorized in self._session_authorized_paths.get(session_id, set()):
            if abs_path.startswith(authorized):
                return True
        return False

    # ──── 审批管理 ────

    def set_auto_approve(self, operation: str, auto: bool = True) -> None:
        """设置操作自动审批"""
        if auto:
            self._auto_approve_operations.add(operation)
        else:
            self._auto_approve_operations.discard(operation)

    def register_approval_callback(self, callback: Callable) -> None:
        """注册审批回调"""
        self._approval_callbacks.append(callback)

    async def request_approval(self, operation: str, params: Dict, requester: str = "agent") -> Dict:
        """请求操作审批

        Returns:
            Dict: {approved, message, session_id}
        """
        risk = self.get_risk_level(operation, params)
        policy = self.get_policy(operation)

        if not self.requires_approval(operation, params):
            return {"approved": True, "message": "自动批准", "risk_level": risk.value}

        # 通知所有审批回调
        for callback in self._approval_callbacks:
            try:
                if hasattr(callback, '__call__'):
                    result = callback(operation, params, risk, requester)
                    if asyncio.iscoroutine(result):
                        await result
            except Exception as e:
                logger.warning(f"审批回调异常: {e}")

        return {
            "approved": False,
            "message": f"等待用户确认: {policy.description}",
            "risk_level": risk.value,
            "operation": operation,
            "params": params,
        }

    # ──── 审计日志 ────

    def log_audit(self, operation: str, params: Dict, result: any,
                  user: str = "agent", success: bool = True,
                  risk_level: str = "safe") -> None:
        """记录审计日志"""
        import time
        entry = {
            "timestamp": time.time(),
            "operation": operation,
            "params": params,
            "success": success,
            "user": user,
            "risk_level": risk_level,
        }
        self._audit_log.insert(0, entry)
        if len(self._audit_log) > self._max_audit:
            self._audit_log = self._audit_log[:self._max_audit]

    def get_audit_log(self, limit: int = 50, risk_level: Optional[str] = None) -> List[Dict]:
        """获取审计日志"""
        logs = self._audit_log
        if risk_level:
            logs = [l for l in logs if l.get("risk_level") == risk_level]
        return logs[:limit]

    # ──── 工具列表 ────

    def get_safe_operations(self) -> List[Dict]:
        """获取安全操作列表（供 LLM 使用）"""
        operations = []
        for name, policy in self._policies.items():
            risk = policy.risk_level
            operations.append({
                "name": name,
                "description": policy.description,
                "risk_level": risk.value,
                "require_approval": self.requires_approval(name),
            })
        return operations


# 全局单例
_safety_manager: Optional[SafetyManager] = None

def get_safety_manager() -> SafetyManager:
    global _safety_manager
    if _safety_manager is None:
        _safety_manager = SafetyManager()
    return _safety_manager