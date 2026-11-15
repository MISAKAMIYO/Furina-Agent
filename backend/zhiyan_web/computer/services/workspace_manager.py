"""
Agent 工作区管理器

为 AI Agent 提供隔离的文件操作环境，所有文件读写操作
都被限制在指定的工作区目录内，防止 Agent 访问系统敏感文件。

使用方式：
    from zhiyan_web.computer.services.workspace_manager import (
        get_workspace, is_path_in_workspace, validate_path,
        ensure_workspace_exists, get_workspace_info
    )
"""
from __future__ import annotations

import os
import logging
from typing import Optional, Tuple

logger = logging.getLogger("ZhiYanWeb.Computer.Workspace")


def get_workspace() -> str:
    """从设置中获取工作区路径"""
    try:
        from zhiyan_web.config.settings import get_settings
        settings = get_settings()
        raw = settings.get("agent_workspace", "")
        if raw:
            abs_path = os.path.abspath(raw)
            return abs_path
        return ""
    except Exception as e:
        logger.debug(f"获取工作区设置失败: {e}")
        return ""


def get_workspace_dir() -> str:
    """获取工作区绝对路径，如果未设置则返回空字符串"""
    ws = get_workspace()
    if ws:
        return os.path.abspath(ws)
    return ""


def ensure_workspace_exists() -> Tuple[bool, str]:
    """确保工作区目录存在，不存在则创建

    Returns:
        (success, message)
    """
    ws = get_workspace_dir()
    if not ws:
        return False, "未设置工作区路径"
    try:
        os.makedirs(ws, exist_ok=True)
        # 创建 .gitkeep 文件确保目录不会消失
        gitkeep = os.path.join(ws, ".gitkeep")
        if not os.path.exists(gitkeep):
            with open(gitkeep, "w") as f:
                f.write("")
        return True, f"工作区已就绪: {ws}"
    except Exception as e:
        logger.error(f"创建工作区目录失败: {e}")
        return False, f"创建工作区失败: {e}"


def is_path_in_workspace(path: str) -> bool:
    """检查路径是否在工作区内

    Args:
        path: 要检查的路径（绝对或相对路径）

    Returns:
        True 如果路径在工作区内
    """
    ws = get_workspace_dir()
    if not ws:
        # 如果未设置工作区，默认不允许文件操作
        return False

    abs_path = os.path.abspath(path)
    ws_abs = os.path.abspath(ws)

    # 如果路径就是工作区目录，允许
    if abs_path == ws_abs:
        return True

    # 检查路径是否在工作区目录下
    # 添加 os.sep 确保是子目录而非前缀匹配（如 /workspace1 和 /workspace12）
    if abs_path.startswith(ws_abs + os.sep):
        return True

    return False


def validate_path(path: str, operation: str = "") -> Tuple[bool, str, str]:
    """验证路径是否在工作区内

    Args:
        path: 要验证的路径
        operation: 操作名称（用于错误提示）

    Returns:
        (allowed, normalized_path, error_message)
        - allowed: True 表示允许访问
        - normalized_path: 标准化后的绝对路径
        - error_message: 如果不允许访问时的错误描述
    """
    abs_path = os.path.abspath(path)
    ws = get_workspace_dir()

    if not ws:
        return False, abs_path, "未配置 Agent 工作区路径，请在设置中配置"

    if not os.path.exists(ws):
        return False, abs_path, f"工作区目录不存在: {ws}"

    if is_path_in_workspace(abs_path):
        return True, abs_path, ""

    op_note = f"（操作: {operation}）" if operation else ""
    return (
        False,
        abs_path,
        f"路径不在工作区内: {abs_path}{op_note}\n"
        f"工作区路径: {ws}\n"
        f"Agent 只能读写工作区内的文件。"
    )


def get_workspace_info() -> dict:
    """获取工作区信息"""
    ws = get_workspace_dir()
    if not ws:
        return {
            "configured": False,
            "path": "",
            "exists": False,
            "file_count": 0,
            "total_size": 0,
            "message": "未配置工作区",
        }

    exists = os.path.exists(ws)
    file_count = 0
    total_size = 0

    if exists:
        try:
            for root, dirs, files in os.walk(ws):
                # 跳过隐藏目录
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    file_count += 1
                    try:
                        fp = os.path.join(root, f)
                        total_size += os.path.getsize(fp)
                    except OSError:
                        pass
        except Exception:
            pass

    return {
        "configured": True,
        "path": ws,
        "exists": exists,
        "file_count": file_count,
        "total_size": total_size,
        "message": f"工作区: {ws} ({file_count} 个文件, {total_size:,} 字节)",
    }


def get_workspace_safe_dirs() -> list:
    """获取工作区安全目录列表（供 SafetyManager 使用）"""
    ws = get_workspace_dir()
    if ws:
        return [ws]
    return []


def set_workspace(path: str) -> Tuple[bool, str]:
    """设置工作区路径并保存到设置

    Args:
        path: 工作区目录路径

    Returns:
        (success, message)
    """
    if not path or not path.strip():
        return False, "工作区路径不能为空"

    abs_path = os.path.abspath(path.strip())

    try:
        from zhiyan_web.config.settings import get_settings, save_settings
        settings = get_settings()
        settings["agent_workspace"] = abs_path
        save_settings(settings)
        logger.info(f"工作区路径已更新: {abs_path}")
        return True, f"工作区路径已设置为: {abs_path}"
    except Exception as e:
        logger.error(f"保存工作区设置失败: {e}")
        return False, f"保存设置失败: {e}"