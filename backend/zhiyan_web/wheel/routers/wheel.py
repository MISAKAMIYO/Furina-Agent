"""
决策轮盘路由模块
提供决策轮盘相关的API端点

端点:
- POST /api/wheel/spin - 转动轮盘（自定义选项）
- GET /api/wheel/preset - 获取预设选项
- POST /api/wheel/custom - 创建自定义轮盘
- GET /api/wheel/custom/list - 获取自定义轮盘列表
- POST /api/wheel/custom/spin/{wheel_id} - 转动自定义轮盘
- DELETE /api/wheel/custom/{wheel_id} - 删除自定义轮盘
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zhiyan_web.wheel.services.wheel_service import (
    spin_wheel,
    get_preset_options,
    create_custom_wheel,
    get_custom_wheels,
    spin_custom_wheel,
    delete_custom_wheel,
    USER_ID,
)

router = APIRouter(prefix="/api/wheel", tags=["wheel"])


class SpinRequest(BaseModel):
    """转动轮盘请求模型"""
    options: list


class CustomWheelRequest(BaseModel):
    """创建自定义轮盘请求模型"""
    name: str
    options: list


@router.post("/spin")
async def spin_wheel_endpoint(request: SpinRequest):
    """
    转动轮盘（自定义选项）
    
    参数:
        request: 转动请求（包含options列表）
    
    返回:
        dict: 转动结果
    """
    result = spin_wheel(request.options)
    return result


@router.get("/preset")
async def get_preset_endpoint(category: str = None):
    """
    获取预设选项
    
    参数:
        category: 分类名称（可选）
    
    返回:
        dict: 预设选项列表
    """
    result = get_preset_options(category)
    return result


@router.post("/custom")
async def create_custom_wheel_endpoint(request: CustomWheelRequest):
    """
    创建自定义轮盘
    
    参数:
        request: 创建请求（包含name和options）
    
    返回:
        dict: 创建结果
    """
    result = create_custom_wheel(USER_ID, request.name, request.options)
    return result


@router.get("/custom/list")
async def get_custom_wheels_endpoint():
    """
    获取自定义轮盘列表
    
    返回:
        dict: 自定义轮盘列表
    """
    result = get_custom_wheels(USER_ID)
    return result


@router.post("/custom/spin/{wheel_id}")
async def spin_custom_wheel_endpoint(wheel_id: int):
    """
    转动自定义轮盘
    
    参数:
        wheel_id: 轮盘ID
    
    返回:
        dict: 转动结果
    """
    if wheel_id <= 0:
        raise HTTPException(status_code=400, detail="轮盘ID无效")
    
    result = spin_custom_wheel(USER_ID, wheel_id)
    return result


@router.delete("/custom/{wheel_id}")
async def delete_custom_wheel_endpoint(wheel_id: int):
    """
    删除自定义轮盘
    
    参数:
        wheel_id: 轮盘ID
    
    返回:
        dict: 删除结果
    """
    if wheel_id <= 0:
        raise HTTPException(status_code=400, detail="轮盘ID无效")
    
    result = delete_custom_wheel(USER_ID, wheel_id)
    return result
