"""
命令提示符 API 路由

提供命令执行请求、确认、取消、流式输出、历史查询等接口。
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services.command_prompt import get_command_prompt_manager, MAX_CONCURRENT_SESSIONS

logger = logging.getLogger("ZhiYanWeb.Computer.CommandPromptAPI")
router = APIRouter(prefix="/api/command-prompt", tags=["command-prompt"])

cpm = get_command_prompt_manager()


# ─── 请求模型 ───────────────────────────────────────────

class ExecuteRequest(BaseModel):
    command: str
    description: str = ""
    requester: str = "agent"
    timeout: int = 60
    cwd: str = ""


class ConfirmRequest(BaseModel):
    session_id: str
    user: str = "user"


class CancelRequest(BaseModel):
    session_id: str
    user: str = "user"


class CloseRequest(BaseModel):
    session_id: str


class WhitelistRequest(BaseModel):
    prefixes: List[str]


# ─── API 端点 ───────────────────────────────────────────

@router.post("/request")
async def request_execution(req: ExecuteRequest):
    """AI 请求执行命令，返回待审批会话"""
    result = await cpm.request_execution(
        command=req.command,
        description=req.description,
        requester=req.requester,
        timeout=req.timeout,
        cwd=req.cwd,
    )
    return result


@router.post("/confirm")
async def confirm_execution(req: ConfirmRequest):
    """用户确认执行命令"""
    result = await cpm.confirm_execution(req.session_id, req.user)
    return result


@router.post("/cancel")
async def cancel_execution(req: CancelRequest):
    """用户取消命令"""
    result = await cpm.cancel_execution(req.session_id, req.user)
    return result


@router.post("/close")
async def close_session(req: CloseRequest):
    """手动关闭会话"""
    result = await cpm.close_session(req.session_id)
    return result


@router.get("/session/{session_id}")
async def get_session(session_id: str):
    """获取会话详情"""
    session = cpm.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"success": True, "data": session}


@router.get("/pending")
async def get_pending_sessions():
    """获取所有待审批的命令"""
    return {"success": True, "data": cpm.get_pending_sessions()}


@router.get("/active")
async def get_active_sessions():
    """获取所有活跃会话"""
    return {"success": True, "data": cpm.get_active_sessions()}


@router.get("/stream/{session_id}")
async def stream_output(session_id: str):
    """SSE 流式推送命令输出"""
    return StreamingResponse(
        _stream_events(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_events(session_id: str):
    """生成 SSE 事件流"""
    try:
        async for event in cpm.stream_output(session_id):
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
    except Exception as e:
        yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"


@router.get("/history")
async def get_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """获取命令执行历史"""
    return {"success": True, "data": cpm.get_history(limit, offset)}


@router.get("/whitelist")
async def get_whitelist():
    """获取白名单"""
    return {"success": True, "data": cpm.get_allowed_prefixes()}


@router.post("/whitelist")
async def set_whitelist(req: WhitelistRequest):
    """设置白名单"""
    cpm.set_allowed_prefixes(req.prefixes)
    return {"success": True, "message": "白名单已更新"}


@router.get("/limits")
async def get_limits():
    """获取限制信息"""
    return {
        "success": True,
        "data": {
            "max_concurrent": MAX_CONCURRENT_SESSIONS,
            "active_count": len(cpm.get_active_sessions()),
            "pending_count": len(cpm.get_pending_sessions()),
        },
    }