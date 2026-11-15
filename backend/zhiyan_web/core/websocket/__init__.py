"""
WebSocket 核心模块
提供 WebSocket 连接管理和消息广播功能

模块结构:
- __init__.py       - 公共接口导出
- connection.py     - 连接管理器
"""
from __future__ import annotations

from .connection import ConnectionManager

__all__ = ["ConnectionManager"]
