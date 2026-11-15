"""
盲盒系统路由模块
提供盲盒相关的API端点

端点:
- POST /api/blindbox/open - 开启盲盒
- GET /api/blindbox/collection - 获取收藏列表
- GET /api/blindbox/history - 获取开启历史
"""
from __future__ import annotations

from fastapi import APIRouter

from zhiyan_web.blindbox.services.blindbox_service import (
    open_blindbox,
    get_collection,
    get_history,
    USER_ID,
)

router = APIRouter(prefix="/api/blindbox", tags=["blindbox"])


@router.post("/open")
async def open_blindbox_endpoint():
    """
    开启盲盒
    
    返回:
        dict: 开启结果
    """
    result = open_blindbox(USER_ID)
    return result


@router.get("/collection")
async def get_collection_endpoint():
    """
    获取收藏列表
    
    返回:
        dict: 收藏列表
    """
    result = get_collection(USER_ID)
    return result


@router.get("/history")
async def get_history_endpoint():
    """
    获取开启历史
    
    返回:
        dict: 开启历史列表
    """
    result = get_history(USER_ID)
    return result
