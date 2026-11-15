"""
漂流瓶路由模块
提供漂流瓶相关的API端点

端点:
- POST /api/bottle/throw - 扔瓶子
- GET /api/bottle/pick - 捡瓶子
- GET /api/bottle/replies/{bottle_id} - 获取瓶子回复
- POST /api/bottle/reply - 回复瓶子
- GET /api/bottle/my_bottles - 获取我的瓶子
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zhiyan_web.bottle.services.bottle_service import (
    throw_bottle,
    pick_bottle,
    get_bottle_replies,
    reply_bottle,
    get_my_bottles,
    USER_ID,
)

router = APIRouter(prefix="/api/bottle", tags=["bottle"])


class ThrowRequest(BaseModel):
    """扔瓶子请求模型"""
    content: str
    is_anonymous: bool = True


class ReplyRequest(BaseModel):
    """回复瓶子请求模型"""
    bottle_id: str
    content: str
    is_anonymous: bool = True


@router.post("/throw")
async def throw_bottle_endpoint(request: ThrowRequest):
    """
    扔瓶子
    
    参数:
        request: 扔瓶子请求（包含content和is_anonymous）
    
    返回:
        dict: 扔瓶子结果
    """
    result = throw_bottle(USER_ID, request.content, request.is_anonymous)
    return result


@router.get("/pick")
async def pick_bottle_endpoint():
    """
    捡瓶子
    
    返回:
        dict: 捡到的瓶子信息
    """
    result = pick_bottle(USER_ID)
    return result


@router.get("/replies/{bottle_id}")
async def get_replies_endpoint(bottle_id: str):
    """
    获取瓶子回复
    
    参数:
        bottle_id: 瓶子ID
    
    返回:
        dict: 回复列表
    """
    if not bottle_id:
        raise HTTPException(status_code=400, detail="瓶子ID不能为空")
    
    result = get_bottle_replies(bottle_id)
    return result


@router.post("/reply")
async def reply_bottle_endpoint(request: ReplyRequest):
    """
    回复瓶子
    
    参数:
        request: 回复请求（包含bottle_id、content和is_anonymous）
    
    返回:
        dict: 回复结果
    """
    if not request.bottle_id:
        raise HTTPException(status_code=400, detail="瓶子ID不能为空")
    
    result = reply_bottle(USER_ID, request.bottle_id, request.content, request.is_anonymous)
    return result


@router.get("/my_bottles")
async def my_bottles_endpoint():
    """
    获取我的瓶子
    
    返回:
        dict: 瓶子列表
    """
    result = get_my_bottles(USER_ID)
    return result
