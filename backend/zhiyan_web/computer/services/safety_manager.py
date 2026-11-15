"""
安全管控管理器

管理 AI Agent 的三种安全管控模式：

1. normal (普通默认权限):
   - 写入操作：仅限工作区
   - 读取操作：全盘读取
   - 命令执行：受限（禁止危险命令）

2. full_control (完全控制权限):
   - 读写操作：整台电脑完全控制
   - 命令执行：完全允许
   - 高危模式，需用户确认，不能永久保持，每次重启软件需手动开启

3. high_security (高权限安全模式):
   - 读写操作：允许非关键系统位置
   - 命令执行：严格限制，禁止高敏感命令和危险操作（如C盘系统目录等）
"""
from __future__ import annotations

import contextvars
import os
import re
import logging
from typing import Optional

logger = logging.getLogger("ZhiYanWeb.Computer.Safety")

# 单次审批放行：只对当前工具调用的执行上下文有效，用完立刻复位
_approval_bypass = contextvars.ContextVar("zhiyan_approval_bypass", default=False)


def approval_bypass_active() -> bool:
    return bool(_approval_bypass.get())


def set_approval_bypass(active: bool = True):
    """开启/关闭一次性的审批放行，返回用于复位的 token。"""
    return _approval_bypass.set(bool(active))


def reset_approval_bypass(token) -> None:
    try:
        _approval_bypass.reset(token)
    except Exception:
        pass

# 安全模式常量
MODE_NORMAL = "normal"
MODE_FULL_CONTROL = "full_control"
MODE_HIGH_SECURITY = "high_security"

# 默认模式（每次启动默认值）
_DEFAULT_MODE = MODE_NORMAL

# 当前会话的安全模式（不持久化保存，重启后恢复默认）
_current_mode = _DEFAULT_MODE

# 完全控制模式标记 - 仅用于API返回
_full_control_activated = False


# ---- 高风险系统路径（高安全模式下禁止写入） ----

HIGH_RISK_PATHS = [
    # Windows 系统目录
    r"C:\\Windows",
    r"C:\\Windows\\System32",
    r"C:\\Windows\\SysWOW64",
    r"C:\\Windows\\System",
    r"C:\\Windows\\Temp",
    r"C:\\Windows\\Prefetch",
    r"C:\\Program Files",
    r"C:\\Program Files \(x86\)",
    r"C:\\ProgramData",
    r"C:\\Config\.Msi",
    r"C:\\Recovery",
    r"C:\\System Volume Information",
    r"C:\\$Recycle\.Bin",
    r"C:\\Boot",
    r"C:\\$WinRE",
    # Linux/Mac 系统目录（跨平台保护）
    "/etc",
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/var",
    "/boot",
    "/dev",
    "/proc",
    "/sys",
    "/root",
    "/System",
    "/Library",
    "/private",
]

# 高敏感路径（高安全模式下连读取都禁止 - 含个人隐私的系统文件）
HIGH_SENSITIVE_PATHS = [
    r"C:\\Windows\\System32\\config",
    r"C:\\Windows\\System32\\.*\\.dll$",
    r"C:\\Windows\\System32\\.*\\.exe$",
    r"C:\\Windows\\System32\\.*\\.sys$",
    r"C:\\Windows\\.*\\.dll$",
    r"C:\\Windows\\.*\\.exe$",
    r"C:\\Windows\\.*\\.sys$",
    r"C:\\Windows\\Microsoft\.NET",
    r"C:\\Windows\\Installer",
    r"C:\\Windows\\assembly",
    r"C:\\Windows\\WinSxS",
]

# 危险命令模式（高安全模式下禁止执行）
DANGEROUS_COMMANDS = [
    # 格式化和磁盘操作
    r'^format\s',
    r'^format\.exe\s',
    r'^diskpart\s',
    r'^diskpart\.exe\s',
    # 强制删除
    r'del\s+/[fF]',
    r'del\.exe\s+/[fF]',
    r'rd\s+/[sSqQ]',
    r'rmdir\s+/[sS]',
    r'rm\s+-[rRfF]',
    # 磁盘写入
    r'dd\s+if=',
    r'^fdisk\s',
    # 系统配置修改
    r'^reg\s+(delete|add|import)',
    r'reg\.exe\s+(delete|add|import)',
    r'^bcdedit\s',
    r'^bootrec\s',
    # 权限提升
    r'^takeown\s',
    r'^icacls\s+/grant',
    r'^cacls\s+/grant',
    # 危险操作
    r'^shutdown\s+/[rsp]',
    r'^shutdown\.exe\s+/[rsp]',
    r'^taskkill\s+/[f]',
    r'^taskkill\.exe\s+/[f]',
    r'^wmic\s+process\s+delete',
    r'^powershell\s+.*Remove-Item',
    r'^powershell\s+.*rm\s+-r',
    r'^powershell\s+.*Format-Volume',
    r'^powershell\s+.*Clear-Content',
    r'^powershell\s+.*Stop-Computer',
    r'^powershell\s+.*Restart-Computer',
    # 网络爆破
    r'^ping\s+.*-t\s',
    r'^netsh\s+winsock\s+reset',
    # 清空日志
    r'^wevtutil\s+cl\s',
    r'^wevtutil\.exe\s+cl\s',
    r'^powershell\s+.*Clear-EventLog',
    # 组策略修改
    r'^gpupdate\s+/force',
    r'^gpupdate\.exe\s+/force',
    r'^secedit\s+/configure',
]

# 高敏感命令（full_control 时也需要确认）
HIGH_SENSITIVE_COMMANDS = [
    r'^format\s',
    r'^format\.exe\s',
    r'^diskpart\s',
    r'^diskpart\.exe\s',
    r'^shutdown\s',
    r'^shutdown\.exe\s',
    r'^reg\s+(delete|add)',
    r'^reg\.exe\s+(delete|add)',
    r'^bcdedit\s',
    r'^takeown\s',
    r'^icacls\s+/grant',
    r'^cacls\s+/grant',
    r'^powershell\s+.*Remove-Item',
    r'^powershell\s+.*Format-Volume',
    r'^powershell\s+.*Stop-Computer',
    r'^powershell\s+.*Restart-Computer',
    r'^powershell\s+.*Clear-Content',
    r'^powershell\s+.*Clear-EventLog',
    r'^wevtutil\s+cl\s',
    r'^wevtutil\.exe\s+cl\s',
]


def get_security_mode() -> str:
    """获取当前安全管控模式"""
    return _current_mode


def set_security_mode(mode: str) -> tuple:
    """
    设置安全管控模式

    Args:
        mode: 模式名称 (normal/full_control/high_security)

    Returns:
        (success, message, warning)
    """
    global _current_mode, _full_control_activated

    if mode not in [MODE_NORMAL, MODE_FULL_CONTROL, MODE_HIGH_SECURITY]:
        return False, f"无效的安全模式: {mode}", ""

    old_mode = _current_mode
    _current_mode = mode

    if mode == MODE_FULL_CONTROL:
        _full_control_activated = True
        logger.warning("*** 安全管控模式已切换为「完全控制」- 高危！AI 拥有整机读写权限 ***")
        return (
            True,
            "已切换为「完全控制」模式，AI 拥有整机读写权限。此模式不会永久保存，重启后将恢复默认。",
            "⚠️ 高危警告：AI 现在拥有对您电脑的完全控制权限！可以对整台电脑进行任何读写操作。"
            "请在使用完毕后立即切换回普通模式。此模式不会永久保存，重启软件后自动恢复为普通模式。"
        )

    if mode == MODE_HIGH_SECURITY:
        logger.info("安全管控模式已切换为「高权限安全」")
        return (
            True,
            "已切换为「高权限安全」模式，AI 仅能读写非关键系统位置，危险命令已被禁止。",
            ""
        )

    # normal mode
    logger.info("安全管控模式已切换为「普通默认」")
    # 如果从 full_control 切回，关闭标记
    if old_mode == MODE_FULL_CONTROL:
        _full_control_activated = False
    return (
        True,
        "已切换为「普通默认」模式，AI 仅能在工作区写入，全盘读取。",
        ""
    )


def is_path_allowed_for_read(path: str) -> tuple:
    """
    检查路径是否允许读取

    Args:
        path: 要检查的路径

    Returns:
        (allowed, reason)
    """
    abs_path = os.path.abspath(path)

    if _current_mode == MODE_FULL_CONTROL or approval_bypass_active():
        return True, ""

    if _current_mode == MODE_HIGH_SECURITY:
        # 高安全模式：禁止读取高敏感路径
        for sensitive_pattern in HIGH_SENSITIVE_PATHS:
            try:
                if re.search(sensitive_pattern, abs_path, re.IGNORECASE):
                    return False, f"高安全模式下禁止读取系统敏感路径: {abs_path}"
            except re.error:
                if sensitive_pattern.lower() in abs_path.lower():
                    return False, f"高安全模式下禁止读取系统敏感路径: {abs_path}"

        # 也检查高风险路径（禁止读取系统关键文件）
        for risk_path in HIGH_RISK_PATHS:
            risk_norm = os.path.normpath(risk_path).lower()
            if abs_path.lower().startswith(risk_norm):
                return False, f"高安全模式下禁止访问系统关键路径: {abs_path}"

    # normal 模式：允许全盘读取
    return True, ""


def is_path_allowed_for_write(path: str) -> tuple:
    """
    检查路径是否允许写入

    Args:
        path: 要检查的路径

    Returns:
        (allowed, reason)
    """
    abs_path = os.path.abspath(path)

    if _current_mode == MODE_FULL_CONTROL or approval_bypass_active():
        return True, ""

    if _current_mode == MODE_HIGH_SECURITY:
        # 高安全模式：禁止写入高风险路径
        for risk_path in HIGH_RISK_PATHS:
            risk_norm = os.path.normpath(risk_path).lower()
            if abs_path.lower().startswith(risk_norm):
                return False, f"高安全模式下禁止写入系统关键路径: {abs_path}"

        # 禁止写入 .exe, .dll, .sys, .bat, .ps1, .vbs, .cmd 等可执行文件
        dangerous_extensions = {'.exe', '.dll', '.sys', '.bat', '.ps1', '.vbs', '.cmd', '.com', '.scr', '.pif'}
        ext = os.path.splitext(abs_path)[1].lower()
        if ext in dangerous_extensions:
            return False, f"高安全模式下禁止写入可执行文件: {abs_path}"

        # 正常模式：检查工作区
        from zhiyan_web.computer.services.workspace_manager import is_path_in_workspace
        if is_path_in_workspace(abs_path):
            return True, ""
        return False, f"高安全模式下写入操作仅限工作区: {abs_path}"

    # normal 模式：写入必须在工作区内
    from zhiyan_web.computer.services.workspace_manager import is_path_in_workspace
    if is_path_in_workspace(abs_path):
        return True, ""
    return False, f"普通模式下写入操作仅限工作区"


def is_command_allowed(command: str, need_confirm: bool = False) -> tuple:
    """
    检查命令是否允许执行

    Args:
        command: 要执行的命令
        need_confirm: 是否需要额外确认（用于 full_control 模式下的高敏感命令）

    Returns:
        (allowed, need_confirm, reason)
    """
    if not command:
        return True, False, ""

    cmd_trimmed = command.strip()

    if _current_mode == MODE_FULL_CONTROL:
        # 完全控制模式下，高敏感命令仍需要额外警告
        for pattern in HIGH_SENSITIVE_COMMANDS:
            try:
                if re.search(pattern, cmd_trimmed, re.IGNORECASE):
                    return True, True, "该命令属于高敏感操作，请确认是否执行"
            except re.error:
                pass
        return True, False, ""

    if _current_mode == MODE_HIGH_SECURITY:
        # 高安全模式：禁止所有危险命令
        for pattern in DANGEROUS_COMMANDS:
            try:
                if re.search(pattern, cmd_trimmed, re.IGNORECASE):
                    return False, False, f"高安全模式下禁止执行危险命令: {pattern}"
            except re.error:
                pass

        # 检查是否涉及 C 盘系统目录操作
        c_drive_dangerous = re.search(
            r'(C:\\|c:\\)(Windows|System32|Program\s*Files|ProgramData|Boot|Recovery)',
            cmd_trimmed, re.IGNORECASE
        )
        if c_drive_dangerous:
            matched = c_drive_dangerous.group(0)
            return False, False, f"高安全模式下禁止操作系统关键目录: {matched}"

    # normal 模式：检查危险命令
    for pattern in DANGEROUS_COMMANDS:
        try:
            if re.search(pattern, cmd_trimmed, re.IGNORECASE):
                return False, False, f"普通模式下禁止执行危险命令"
        except re.error:
            pass

    return True, False, ""


def get_security_info() -> dict:
    """获取安全管控信息"""
    mode_names = {
        MODE_NORMAL: "普通默认",
        MODE_FULL_CONTROL: "完全控制",
        MODE_HIGH_SECURITY: "高权限安全",
    }
    mode_descriptions = {
        MODE_NORMAL: "AI 仅能在工作区写入，但可全盘读取文件",
        MODE_FULL_CONTROL: "⚠️ AI 拥有整机读写权限，可执行任意操作",
        MODE_HIGH_SECURITY: "AI 仅读写非关键位置，危险命令已被禁止",
    }

    return {
        "mode": _current_mode,
        "mode_name": mode_names.get(_current_mode, "未知"),
        "description": mode_descriptions.get(_current_mode, ""),
        "is_full_control": _current_mode == MODE_FULL_CONTROL,
        "is_default": _current_mode == _DEFAULT_MODE,
        "available_modes": [
            {
                "id": MODE_NORMAL,
                "name": "普通默认",
                "description": "写入仅限工作区，全盘可读取",
                "icon": "fa-shield-halved",
                "risk_level": "low",
            },
            {
                "id": MODE_FULL_CONTROL,
                "name": "完全控制",
                "description": "整机读写权限，高危操作需确认",
                "icon": "fa-unlock",
                "risk_level": "critical",
            },
            {
                "id": MODE_HIGH_SECURITY,
                "name": "高权限安全",
                "description": "禁止系统关键位置操作，危险命令拦截",
                "icon": "fa-shield",
                "risk_level": "high",
            },
        ],
        "full_control_warning": (
            "⚠️ 高危操作：您正在开启完全控制模式！\n\n"
            "在此模式下，AI 将拥有对您电脑的完全读写权限，\n"
            "可以读取、修改、删除任何文件，执行任意命令。\n\n"
            "此模式不会永久保存，重启后将自动恢复为普通模式。\n"
            "请在使用完毕后立即切换回普通模式。"
        ),
    }


def is_full_control_activated_this_session() -> bool:
    """检查完全控制模式是否在本会话中被激活过"""
    return _full_control_activated