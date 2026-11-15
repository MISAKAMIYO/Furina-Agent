"""
代码执行器路由模块
提供代码执行相关的API端点

端点:
- POST /api/code/run - 执行代码
- GET /api/code/history - 获取执行历史
- DELETE /api/code/history/{history_id} - 删除执行历史
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zhiyan_web.codeexec.services.codeexec_service import (
    execute_code,
    get_code_history,
    delete_code_history,
    USER_ID,
)

router = APIRouter(prefix="/api/code", tags=["code"])


class RunRequest(BaseModel):
    """执行代码请求模型"""
    code: str


@router.post("/run")
async def run_code_endpoint(request: RunRequest):
    """
    执行代码
    
    参数:
        request: 执行请求（包含code）
    
    返回:
        dict: 执行结果
    """
    result = execute_code(USER_ID, request.code)
    return result


@router.get("/history")
async def get_history_endpoint():
    """
    获取执行历史
    
    返回:
        dict: 执行历史列表
    """
    result = get_code_history(USER_ID)
    return result


@router.delete("/history/{history_id}")
async def delete_history_endpoint(history_id: int):
    """
    删除执行历史
    
    参数:
        history_id: 历史记录ID
    
    返回:
        dict: 删除结果
    """
    if history_id <= 0:
        raise HTTPException(status_code=400, detail="记录ID无效")
    
    result = delete_code_history(USER_ID, history_id)
    return result
