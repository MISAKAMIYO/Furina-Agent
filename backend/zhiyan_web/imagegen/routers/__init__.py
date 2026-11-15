"""
文本转图片路由模块
"""
from __future__ import annotations

from .imagegen import router as imagegen_router

__all__ = ["imagegen_router"]
