"""
经济系统路由模块
提供经济系统相关的API端点

端点:
- GET /api/economy/status - 获取经济状态
- POST /api/economy/checkin - 每日签到
- POST /api/economy/work - 打工赚取金币
- GET /api/economy/shop/list - 获取商店物品列表
- POST /api/economy/shop/buy - 购买物品
- GET /api/economy/inventory - 获取背包物品
- POST /api/economy/use_item - 使用物品
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zhiyan_web.economy.services.economy_service import (
    get_economy_status,
    checkin,
    work,
    get_shop_items,
    buy_item,
    get_inventory,
    use_item,
    USER_ID,
)

router = APIRouter(prefix="/api/economy", tags=["economy"])


class BuyRequest(BaseModel):
    """购买请求模型"""
    item_id: int


class UseItemRequest(BaseModel):
    """使用物品请求模型"""
    item_id: int


@router.get("/status")
async def economy_status():
    """
    获取经济状态（金币、等级）
    
    返回:
        dict: 经济状态信息
    """
    result = get_economy_status(USER_ID)
    return {"success": True, "data": result}


@router.post("/checkin")
async def daily_checkin():
    """
    每日签到
    
    返回:
        dict: 签到结果
    """
    result = checkin(USER_ID)
    return result


@router.post("/work")
async def do_work():
    """
    打工赚取金币
    
    返回:
        dict: 打工结果
    """
    result = work(USER_ID)
    return result


@router.get("/shop/list")
async def shop_list():
    """
    获取商店物品列表
    
    返回:
        dict: 物品列表
    """
    result = get_shop_items()
    return result


@router.post("/shop/buy")
async def buy_item_endpoint(request: BuyRequest):
    """
    购买物品
    
    参数:
        request: 购买请求（包含item_id）
    
    返回:
        dict: 购买结果
    """
    if request.item_id <= 0:
        raise HTTPException(status_code=400, detail="物品ID无效")
    
    result = buy_item(USER_ID, request.item_id)
    return result


@router.get("/inventory")
async def inventory_list():
    """
    获取背包物品
    
    返回:
        dict: 背包物品列表
    """
    result = get_inventory(USER_ID)
    return result


@router.post("/use_item")
async def use_item_endpoint(request: UseItemRequest):
    """
    使用物品
    
    参数:
        request: 使用请求（包含item_id）
    
    返回:
        dict: 使用结果
    """
    if request.item_id <= 0:
        raise HTTPException(status_code=400, detail="物品ID无效")
    
    result = use_item(USER_ID, request.item_id)
    return result
