"""交互式审批：Agent 越权操作前的挂起 / 放行协调器。

工具循环在真正执行一个「会超出当前安全模式」的操作前，先在这里登记一个
待审批请求，把 `approval_request` 事件推给前端；前端弹出「等待审批」卡片，
用户点「允许一次 / 拒绝」后调用 `/api/chat/approval-decision` 落到 decide()，
挂起的协程随即恢复：

- allow：在同一次工具调用里临时放开安全校验（见 safety_manager.approval_bypass），
  操作完成后立刻失效，不改变全局安全模式。
- deny / timeout：不执行该工具，把拒绝原因作为工具结果回灌给模型，让它自行改道。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger("ZhiYanWeb.Approval")

DEFAULT_TIMEOUT = 300  # 秒；用户长时间不处理就按拒绝处理，避免协程一直挂着

_pending: Dict[str, Dict[str, Any]] = {}

# 写入类工具 → 需要检查的目标路径参数
_WRITE_TOOLS = {
    "write_file", "edit_file", "create_directory", "append_file",
    "copy_file", "move_file", "rename_file", "delete_file",
    "unzip_file",
}
_PATH_KEYS = ("path", "file_path", "new_path", "destination", "dest", "target")


def _target_path(args: Dict[str, Any]) -> str:
    for key in _PATH_KEYS:
        value = (args or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def evaluate(tool_name: str, args: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """判断这次工具调用是否需要用户审批。

    Returns:
        None 表示无需审批；否则返回 {permission, target, detail}。
    """
    name = (tool_name or "").lower()
    if name not in _WRITE_TOOLS:
        return None
    path = _target_path(args or {})
    if not path:
        return None

    try:
        from zhiyan_web.computer.services.safety_manager import (
            MODE_FULL_CONTROL,
            MODE_HIGH_SECURITY,
            get_security_mode,
            is_path_allowed_for_write,
        )
    except Exception as e:  # 安全模块不可用时保持旧行为
        logger.debug(f"审批策略加载失败: {e}")
        return None

    mode = get_security_mode()
    if mode == MODE_FULL_CONTROL:
        return None
    # 高权限安全模式下的拦截（系统关键路径、可执行文件）属于硬禁止，不给「允许一次」
    if mode == MODE_HIGH_SECURITY:
        return None

    # 删除是破坏性操作：即使在工作区内也要用户点一次「允许一次」（可还原的回收站删除）
    if name == "delete_file":
        import os as _os
        try:
            target = _os.path.abspath(path)
        except Exception:
            target = path
        return {
            "permission": "danger-full-access",
            "target": target,
            "detail": json.dumps(args, ensure_ascii=False)
                      + "\n删除会移入回收站（可还原）。本次放行只作用于这一次操作。",
            "reason": "删除文件属于破坏性操作，需要你确认",
        }

    allowed, reason = is_path_allowed_for_write(path)
    if allowed:
        return None

    detail = json.dumps(args, ensure_ascii=False)
    detail += (
        "\n本次放行只作用于这一次操作，不会改变全局安全模式；"
        "操作完成后写入权限立即收回。"
    )
    return {
        "permission": "danger-full-access",
        "target": path,
        "detail": detail,
        "reason": reason or "目标路径超出当前安全模式允许的范围",
    }


def open_request(
    conv_id: str,
    tool_name: str,
    args: Dict[str, Any],
    permission: str,
    target: str,
    detail: str,
    reason: str = "",
) -> Dict[str, Any]:
    """登记待审批请求，返回可直接推给前端的公开字段。"""
    req_id = uuid.uuid4().hex[:12]
    loop = asyncio.get_running_loop()
    _pending[req_id] = {
        "id": req_id,
        "conv_id": conv_id or "",
        "tool": tool_name,
        "args": args,
        "future": loop.create_future(),
        "created": time.time(),
    }
    logger.info(f"等待审批 [{req_id}] {tool_name}: {target}")
    return {
        "id": req_id,
        "tool": tool_name,
        "permission": permission,
        "target": target,
        "detail": detail,
        "reason": reason,
    }


async def wait(request_id: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """等待用户决定，返回 allow / deny / timeout / expired。"""
    item = _pending.get(request_id)
    if not item:
        return "expired"
    try:
        return await asyncio.wait_for(asyncio.shield(item["future"]), timeout)
    except asyncio.TimeoutError:
        logger.warning(f"审批超时 [{request_id}]")
        return "timeout"
    finally:
        _pending.pop(request_id, None)


def decide(request_id: str, decision: str) -> bool:
    """前端提交审批结果；返回是否命中一个仍在等待的请求。"""
    item = _pending.get(request_id)
    if not item:
        return False
    future: asyncio.Future = item["future"]
    if future.done():
        return False
    allow = str(decision or "").lower() in ("allow", "allow_once", "approve", "yes")
    future.set_result("allow" if allow else "deny")
    logger.info(f"审批结果 [{request_id}]: {'allow' if allow else 'deny'}")
    return True


def pending_for_conv(conv_id: str) -> list:
    """某会话里尚未处理的审批（供排查 / 前端重连时兜底）。"""
    return [
        {"id": rid, "tool": item["tool"], "created": item["created"]}
        for rid, item in _pending.items()
        if item.get("conv_id") == (conv_id or "")
    ]
