"""
决策轮盘模块
提供随机决策、预设选项功能
"""
from __future__ import annotations

from .services import (
    spin_wheel,
    get_preset_options,
    create_custom_wheel,
    get_custom_wheels,
    spin_custom_wheel,
    delete_custom_wheel,
)

__all__ = [
    "spin_wheel",
    "get_preset_options",
    "create_custom_wheel",
    "get_custom_wheels",
    "spin_custom_wheel",
    "delete_custom_wheel",
]
