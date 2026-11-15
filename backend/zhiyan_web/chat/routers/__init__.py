"""
聊天路由模块
提供聊天相关的 API 路由

导出:
- chat_router: 聊天 API 路由
- avatar_router: 头像 API 路由
- websocket_endpoint: WebSocket 端点
"""
from __future__ import annotations

from .chat import router as chat_router
from .avatar import router as avatar_router
from .websocket import websocket_endpoint

__all__ = [
    "chat_router",
    "avatar_router",
    "websocket_endpoint",
]
