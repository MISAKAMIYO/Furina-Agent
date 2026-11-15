"""
命令提示符管理器 (Command Prompt Manager)

为 AI Agent 提供安全的命令执行能力：
1. 命令执行需用户确认（请求 → 确认/取消）
2. 实时输出流式推送（stdout/stderr）
3. 白名单 + 权限分级 + 超时控制
4. 会话管理（闲置清理、最大并发限制）
5. 执行历史审计
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import AsyncIterator, Dict, List, Optional, Set

logger = logging.getLogger("ZhiYanWeb.Computer.CommandPrompt")

# ─── 配置常量 ───────────────────────────────────────────

# 最大并发命令会话数
MAX_CONCURRENT_SESSIONS = 5

# 会话闲置超时（秒）
SESSION_IDLE_TIMEOUT = 300  # 5 分钟

# 命令默认超时
DEFAULT_TIMEOUT = 60

# 命令最大超时
MAX_TIMEOUT = 600

# 输出最大缓冲行数
MAX_OUTPUT_LINES = 5000

# 白名单 - 默认允许的命令前缀
DEFAULT_ALLOWED_PREFIXES: List[str] = [
    "python", "python3", "pip", "pip3", "npm", "npx", "node",
    "git", "dir", "ls", "pwd", "cd", "echo", "type", "cat",
    "mkdir", "copy", "move", "ren", "del",
    "where", "which", "findstr", "find", "sort",
    "systeminfo", "tasklist", "netstat", "ipconfig", "ping",
    "chcp", "date", "time", "ver", "whoami",
    "powershell", "pwsh",
]

# 高危命令 - 始终需要用户确认
HIGH_RISK_COMMANDS: List[str] = [
    "format", "fdisk", "diskpart", "reg delete",
    "shutdown", "restart", "reboot",
    "del /f", "rm -rf", "rmdir /s", "rd /s",
    "net user", "net localgroup",
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


class CommandRiskLevel(Enum):
    """命令风险等级"""
    SAFE = "safe"        # 白名单内，自动执行
    NORMAL = "normal"    # 需要用户确认
    HIGH = "high"        # 高危命令，需要用户确认 + 警告
    FORBIDDEN = "forbidden"  # 禁止执行


class SessionStatus(Enum):
    """会话状态"""
    PENDING = "pending"        # 等待用户确认
    RUNNING = "running"        # 执行中
    COMPLETED = "completed"    # 已完成
    FAILED = "failed"          # 失败
    CANCELLED = "cancelled"    # 用户取消
    TIMEOUT = "timeout"        # 超时
    CLOSED = "closed"          # 手动关闭


@dataclass
class CommandSession:
    """命令执行会话"""
    session_id: str
    command: str
    description: str = ""
    risk_level: CommandRiskLevel = CommandRiskLevel.NORMAL
    status: SessionStatus = SessionStatus.PENDING
    requester: str = "agent"     # 谁发起的（agent / user）
    timeout: int = DEFAULT_TIMEOUT
    cwd: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    confirmed_at: Optional[float] = None
    confirmed_by: str = ""
    stdout: List[str] = field(default_factory=list)
    stderr: List[str] = field(default_factory=list)
    returncode: Optional[int] = None
    duration: float = 0.0
    pid: Optional[int] = None
    process: Optional[asyncio.subprocess.Process] = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    error: str = ""

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "command": self.command,
            "description": self.description,
            "risk_level": self.risk_level.value,
            "status": self.status.value,
            "requester": self.requester,
            "timeout": self.timeout,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "confirmed_at": self.confirmed_at,
            "confirmed_by": self.confirmed_by,
            "stdout": self.stdout[-100:],  # 最近 100 行
            "stderr": self.stderr[-50:],
            "returncode": self.returncode,
            "duration": self.duration,
            "pid": self.pid,
            "error": self.error,
        }

    def to_audit(self) -> Dict:
        """审计日志格式"""
        return {
            "session_id": self.session_id,
            "command": self.command,
            "risk_level": self.risk_level.value,
            "status": self.status.value,
            "confirmed": self.confirmed_at is not None,
            "confirmed_by": self.confirmed_by,
            "returncode": self.returncode,
            "duration": round(self.duration, 2),
            "created_at": self.created_at,
            "output_size": sum(len(l) for l in self.stdout) + sum(len(l) for l in self.stderr),
        }


class CommandPromptManager:
    """
    命令提示符管理器（单例）

    管理所有命令执行会话，提供安全审批、实时输出、历史审计等功能。
    """

    _instance: Optional['CommandPromptManager'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self._sessions: Dict[str, CommandSession] = {}
        self._allowed_prefixes: List[str] = list(DEFAULT_ALLOWED_PREFIXES)
        self._high_risk_commands: List[str] = list(HIGH_RISK_COMMANDS)
        self._safe_dirs: List[str] = list(SAFE_DIRS)
        self._history: List[Dict] = []
        self._max_history = 500
        self._lock = asyncio.Lock()
        # 闲置检查任务
        self._cleanup_task: Optional[asyncio.Task] = None
        self._cleanup_interval = 30  # 每 30 秒检查一次

    # ──────── 启动/停止 ────────

    def start(self):
        """启动闲置清理任务"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    def stop(self):
        """停止闲置清理任务"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()

    # ──────── 请求执行 ────────

    async def request_execution(
        self,
        command: str,
        description: str = "",
        requester: str = "agent",
        timeout: int = DEFAULT_TIMEOUT,
        cwd: str = "",
    ) -> Dict:
        """
        AI 请求执行命令，创建待审批会话

        Returns:
            Dict: {session_id, risk_level, status, message}
        """
        # 1. 并发限制检查
        running_count = sum(1 for s in self._sessions.values() if s.status == SessionStatus.RUNNING)
        if running_count >= MAX_CONCURRENT_SESSIONS:
            return {
                "success": False,
                "error": f"当前已有 {running_count} 个命令在执行，已达到最大并发限制 ({MAX_CONCURRENT_SESSIONS})",
                "session_id": "",
            }

        # 2. 风险等级评估
        risk_level = self._evaluate_risk(command)

        # 3. 白名单检查 - 安全命令自动执行
        if risk_level == CommandRiskLevel.SAFE:
            return await self._execute_direct(command, description, requester, timeout, cwd)

        # 4. 禁止命令
        if risk_level == CommandRiskLevel.FORBIDDEN:
            return {
                "success": False,
                "error": f"命令 '{command}' 被禁止执行（高危命令）",
                "session_id": "",
                "risk_level": "forbidden",
            }

        # 5. 创建待审批会话
        session_id = uuid.uuid4().hex[:12]
        session = CommandSession(
            session_id=session_id,
            command=command,
            description=description,
            risk_level=risk_level,
            status=SessionStatus.PENDING,
            requester=requester,
            timeout=min(timeout, MAX_TIMEOUT),
            cwd=cwd or os.getcwd(),
            created_at=time.time(),
            updated_at=time.time(),
        )
        self._sessions[session_id] = session

        logger.info(
            f"命令执行请求 [{risk_level.value}]: {command[:80]}"
            f" (session={session_id}, requester={requester})"
        )

        return {
            "success": True,
            "session_id": session_id,
            "risk_level": risk_level.value,
            "status": "pending",
            "message": "命令已提交，等待用户确认",
            "command": command,
            "description": description,
        }

    async def confirm_execution(self, session_id: str, user: str = "user") -> Dict:
        """用户确认执行命令"""
        session = self._sessions.get(session_id)
        if not session:
            return {"success": False, "error": "会话不存在"}

        if session.status != SessionStatus.PENDING:
            return {"success": False, "error": f"会话状态为 {session.status.value}，无法确认"}

        session.status = SessionStatus.RUNNING
        session.confirmed_at = time.time()
        session.confirmed_by = user
        session.updated_at = time.time()

        # 异步执行
        asyncio.create_task(self._execute_session(session))

        return {"success": True, "message": "命令已确认执行"}

    async def cancel_execution(self, session_id: str, user: str = "user") -> Dict:
        """用户取消执行命令"""
        session = self._sessions.get(session_id)
        if not session:
            return {"success": False, "error": "会话不存在"}

        if session.status == SessionStatus.RUNNING:
            # 正在运行的命令，发送取消信号
            session.cancel_event.set()
            if session.process:
                try:
                    session.process.kill()
                except Exception:
                    pass
            session.status = SessionStatus.CANCELLED
        elif session.status == SessionStatus.PENDING:
            session.status = SessionStatus.CANCELLED
        else:
            return {"success": False, "error": f"会话状态为 {session.status.value}，无法取消"}

        session.updated_at = time.time()
        self._add_to_history(session)
        return {"success": True, "message": "命令已取消"}

    async def close_session(self, session_id: str) -> Dict:
        """手动关闭会话"""
        session = self._sessions.get(session_id)
        if not session:
            return {"success": False, "error": "会话不存在"}

        if session.status == SessionStatus.RUNNING:
            session.cancel_event.set()
            if session.process:
                try:
                    session.process.kill()
                except Exception:
                    pass

        session.status = SessionStatus.CLOSED
        session.updated_at = time.time()
        self._add_to_history(session)
        self._sessions.pop(session_id, None)
        return {"success": True, "message": "会话已关闭"}

    # ──────── 查询 ────────

    def get_session(self, session_id: str) -> Optional[Dict]:
        session = self._sessions.get(session_id)
        return session.to_dict() if session else None

    def get_pending_sessions(self) -> List[Dict]:
        return [
            s.to_dict() for s in self._sessions.values()
            if s.status == SessionStatus.PENDING
        ]

    def get_active_sessions(self) -> List[Dict]:
        return [
            s.to_dict() for s in self._sessions.values()
            if s.status in (SessionStatus.PENDING, SessionStatus.RUNNING)
        ]

    def get_history(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        return self._history[offset:offset + limit]

    # ──────── 实时输出流 ────────

    async def stream_output(self, session_id: str) -> AsyncIterator[Dict]:
        """
        流式推送命令输出

        Yields:
            Dict: {type, data, ...}
                type: "stdout" | "stderr" | "status" | "done" | "error"
        """
        session = self._sessions.get(session_id)
        if not session:
            yield {"type": "error", "message": "会话不存在"}
            return

        # 会话已结束（完成/失败/超时/取消）：回放已缓冲的输出后推送结束事件。
        # 这样安全命令自动执行完成后，前端也能在 cmd 风格终端中看到完整输出。
        if session.status not in (SessionStatus.PENDING, SessionStatus.RUNNING):
            for line in session.stdout:
                yield {"type": "stdout", "data": line}
            for line in session.stderr:
                yield {"type": "stderr", "data": line}
            yield {
                "type": "done",
                "status": session.status.value,
                "returncode": session.returncode,
                "duration": session.duration,
            }
            return

        last_stdout_len = 0
        last_stderr_len = 0

        while session.status in (SessionStatus.PENDING, SessionStatus.RUNNING):
            # 推送新的 stdout
            if len(session.stdout) > last_stdout_len:
                new_lines = session.stdout[last_stdout_len:]
                for line in new_lines:
                    yield {"type": "stdout", "data": line}
                last_stdout_len = len(session.stdout)

            # 推送新的 stderr
            if len(session.stderr) > last_stderr_len:
                new_lines = session.stderr[last_stderr_len:]
                for line in new_lines:
                    yield {"type": "stderr", "data": line}
                last_stderr_len = len(session.stderr)

            if session.status != SessionStatus.RUNNING:
                break

            await asyncio.sleep(0.05)

        # 推送最终状态
        yield {
            "type": "done",
            "status": session.status.value,
            "returncode": session.returncode,
            "duration": session.duration,
        }

    # ──────── 白名单管理 ────────

    def get_allowed_prefixes(self) -> List[str]:
        return list(self._allowed_prefixes)

    def set_allowed_prefixes(self, prefixes: List[str]) -> None:
        self._allowed_prefixes = list(prefixes)

    def add_allowed_prefix(self, prefix: str) -> None:
        if prefix not in self._allowed_prefixes:
            self._allowed_prefixes.append(prefix)

    def remove_allowed_prefix(self, prefix: str) -> None:
        if prefix in self._allowed_prefixes:
            self._allowed_prefixes.remove(prefix)

    # ──────── 内部方法 ────────

    def _evaluate_risk(self, command: str) -> CommandRiskLevel:
        """评估命令风险等级"""
        cmd_lower = command.strip().lower()

        # 检查是否在禁止列表
        for forbidden in self._high_risk_commands:
            if cmd_lower.startswith(forbidden):
                return CommandRiskLevel.HIGH

        # 检查是否在白名单
        for prefix in self._allowed_prefixes:
            if cmd_lower.startswith(prefix.lower()):
                return CommandRiskLevel.SAFE

        # 默认需要确认
        return CommandRiskLevel.NORMAL

    async def _execute_direct(
        self, command: str, description: str, requester: str,
        timeout: int, cwd: str
    ) -> Dict:
        """白名单命令直接执行（无需用户确认）"""
        session_id = uuid.uuid4().hex[:12]
        session = CommandSession(
            session_id=session_id,
            command=command,
            description=description,
            risk_level=CommandRiskLevel.SAFE,
            status=SessionStatus.RUNNING,
            requester=requester,
            timeout=min(timeout, MAX_TIMEOUT),
            cwd=cwd or os.getcwd(),
            created_at=time.time(),
            updated_at=time.time(),
            confirmed_at=time.time(),
            confirmed_by="whitelist",
        )
        self._sessions[session_id] = session

        asyncio.create_task(self._execute_session(session))

        return {
            "success": True,
            "session_id": session_id,
            "risk_level": "safe",
            "status": "running",
            "message": "命令已在白名单中，自动执行",
            "command": command,
        }

    async def _execute_session(self, session: CommandSession) -> None:
        """执行命令会话"""
        start = time.time()
        try:
            kwargs: Dict = {}
            # Windows 下隐藏 cmd 控制台窗口，避免 AI 执行命令时闪现黑框
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            process = await asyncio.create_subprocess_shell(
                session.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=session.cwd or os.getcwd(),
                **kwargs,
            )
            session.process = process
            session.pid = process.pid

            # 异步读取 stdout 和 stderr
            async def _read_stream(stream, is_stderr: bool):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if text:
                        if is_stderr:
                            session.stderr.append(text)
                            if len(session.stderr) > MAX_OUTPUT_LINES:
                                session.stderr = session.stderr[-MAX_OUTPUT_LINES:]
                        else:
                            session.stdout.append(text)
                            if len(session.stdout) > MAX_OUTPUT_LINES:
                                session.stdout = session.stdout[-MAX_OUTPUT_LINES:]

            stdout_task = asyncio.create_task(_read_stream(process.stdout, False))
            stderr_task = asyncio.create_task(_read_stream(process.stderr, True))

            # 等待完成或超时
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, process.wait()),
                    timeout=session.timeout,
                )
                session.returncode = process.returncode
                # 根据 returncode 判断是否失败
                if process.returncode != 0:
                    session.status = SessionStatus.FAILED
                else:
                    session.status = SessionStatus.COMPLETED
            except asyncio.TimeoutError:
                session.status = SessionStatus.TIMEOUT
                session.stderr.append(f"错误: 命令执行超时（{session.timeout}秒）")
                try:
                    process.kill()
                except Exception:
                    pass

            # 检查是否被取消
            if session.cancel_event.is_set():
                session.status = SessionStatus.CANCELLED
                session.stderr.append("命令已被用户取消")
                try:
                    process.kill()
                except Exception:
                    pass

        except Exception as e:
            session.status = SessionStatus.FAILED
            session.error = str(e)
            session.stderr.append(f"执行错误: {e}")

        session.duration = round(time.time() - start, 2)
        session.updated_at = time.time()

        # 记录到历史
        self._add_to_history(session)

        # 不在活跃列表中保留已完成/取消的会话（但保留一段时间供前端查看）
        # 5 分钟后自动清理
        async def _delayed_cleanup():
            await asyncio.sleep(300)
            self._sessions.pop(session.session_id, None)

        asyncio.create_task(_delayed_cleanup())

    def _add_to_history(self, session: CommandSession) -> None:
        """添加到审计历史"""
        entry = session.to_audit()
        entry["timestamp"] = time.time()
        self._history.insert(0, entry)
        if len(self._history) > self._max_history:
            self._history = self._history[:self._max_history]

    async def _cleanup_loop(self):
        """闲置会话清理循环"""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                now = time.time()
                to_remove = []
                for sid, session in self._sessions.items():
                    # 清理已完成超过 5 分钟的会话
                    if session.status in (
                        SessionStatus.COMPLETED, SessionStatus.FAILED,
                        SessionStatus.TIMEOUT, SessionStatus.CANCELLED, SessionStatus.CLOSED
                    ):
                        if now - session.updated_at > SESSION_IDLE_TIMEOUT:
                            to_remove.append(sid)
                    # 清理待审批超过 10 分钟的会话
                    elif session.status == SessionStatus.PENDING:
                        if now - session.created_at > 600:
                            session.status = SessionStatus.CANCELLED
                            self._add_to_history(session)
                            to_remove.append(sid)

                for sid in to_remove:
                    self._sessions.pop(sid, None)

                if to_remove:
                    logger.debug(f"清理了 {len(to_remove)} 个闲置命令会话")
            except asyncio.CancelledError:
                break
            except Exception:
                pass


# 单例
_command_prompt_manager = CommandPromptManager()


def get_command_prompt_manager() -> CommandPromptManager:
    return _command_prompt_manager