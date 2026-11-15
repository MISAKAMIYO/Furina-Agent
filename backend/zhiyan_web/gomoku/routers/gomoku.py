"""
五子棋路由模块
提供五子棋游戏相关的API端点

端点:
- POST /api/gomoku/new - 创建新游戏
- POST /api/gomoku/move - 玩家落子
- GET /api/gomoku/status/{game_id} - 获取游戏状态
- POST /api/gomoku/cancel/{game_id} - 取消游戏
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zhiyan_web.gomoku.services.gomoku_service import (
    create_new_game,
    make_move,
    get_game_status,
    cancel_game,
    USER_ID,
)

router = APIRouter(prefix="/api/gomoku", tags=["gomoku"])


class MoveRequest(BaseModel):
    """落子请求模型"""
    game_id: str
    row: int
    col: int


@router.post("/new")
async def new_game():
    """
    创建新游戏
    
    返回:
        dict: 游戏信息
    """
    result = create_new_game(USER_ID)
    return result


@router.post("/move")
async def make_move_endpoint(request: MoveRequest):
    """
    玩家落子
    
    参数:
        request: 落子请求（包含game_id、row、col）
    
    返回:
        dict: 落子结果
    """
    if not request.game_id:
        raise HTTPException(status_code=400, detail="游戏ID不能为空")
    
    result = make_move(request.game_id, USER_ID, request.row, request.col)
    return result


@router.get("/status/{game_id}")
async def game_status(game_id: str):
    """
    获取游戏状态
    
    参数:
        game_id: 游戏ID
    
    返回:
        dict: 游戏状态信息
    """
    if not game_id:
        raise HTTPException(status_code=400, detail="游戏ID不能为空")
    
    result = get_game_status(game_id)
    return result


@router.post("/cancel/{game_id}")
async def cancel_game_endpoint(game_id: str):
    """
    取消游戏
    
    参数:
        game_id: 游戏ID
    
    返回:
        dict: 取消结果
    """
    if not game_id:
        raise HTTPException(status_code=400, detail="游戏ID不能为空")
    
    result = cancel_game(game_id, USER_ID)
    return result
