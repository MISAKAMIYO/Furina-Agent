"""
文本转图片路由模块
提供文本转图片相关的API端点

端点:
- POST /api/imagegen/generate - 生成图片
- GET /api/imagegen/history - 获取生成历史
- DELETE /api/imagegen/history/{history_id} - 删除生成历史
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zhiyan_web.imagegen.services.imagegen_service import (
    generate_image,
    get_imagegen_history,
    delete_imagegen_history,
    USER_ID,
)

router = APIRouter(prefix="/api/imagegen", tags=["imagegen"])


class GenerateRequest(BaseModel):
    """生成图片请求模型"""
    prompt: str


@router.post("/generate")
async def generate_image_endpoint(request: GenerateRequest):
    """
    生成图片
    
    参数:
        request: 生成请求（包含prompt）
    
    返回:
        dict: 生成结果
    """
    result = generate_image(USER_ID, request.prompt)
    return result


@router.get("/history")
async def get_history_endpoint():
    """
    获取生成历史
    
    返回:
        dict: 生成历史列表
    """
    result = get_imagegen_history(USER_ID)
    return result


@router.delete("/history/{history_id}")
async def delete_history_endpoint(history_id: int):
    """
    删除生成历史
    
    参数:
        history_id: 历史记录ID
    
    返回:
        dict: 删除结果
    """
    if history_id <= 0:
        raise HTTPException(status_code=400, detail="记录ID无效")
    
    result = delete_imagegen_history(USER_ID, history_id)
    return result
