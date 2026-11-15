"""
经济系统模块
提供签到、打工、商店、背包等功能
"""
from __future__ import annotations

from .services import (
    init_economy_db,
    get_economy_status,
    checkin,
    work,
    get_shop_items,
    buy_item,
    get_inventory,
    use_item,
)

__all__ = [
    "init_economy_db",
    "get_economy_status",
    "checkin",
    "work",
    "get_shop_items",
    "buy_item",
    "get_inventory",
    "use_item",
]
