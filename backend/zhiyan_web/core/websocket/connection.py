"""
WebSocket 连接管理模块
提供 WebSocket 连接的管理、广播和断开功能

核心功能:
- 管理多个活跃的 WebSocket 连接
- 支持消息广播到所有连接
- 支持连接的建立和断开
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import WebSocket

logger = logging.getLogger("ZhiYanWeb")


class ConnectionManager:
    """
    WebSocket 连接管理器
    
    管理所有活跃的 WebSocket 连接，提供广播和连接管理功能
    """
    
    def __init__(self):
        """初始化连接管理器"""
        self.active_connections: List[WebSocket] = []
    
    async def connect(self, websocket: WebSocket) -> None:
        """
        建立新的 WebSocket 连接
        
        参数:
            websocket: WebSocket 连接对象
        """
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket 连接已建立，当前连接数: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket) -> None:
        """
        断开 WebSocket 连接
        
        参数:
            websocket: WebSocket 连接对象
        """
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket 连接已断开，当前连接数: {len(self.active_connections)}")
    
    async def broadcast(self, message: dict) -> None:
        """
        广播消息到所有活跃连接
        
        参数:
            message: 要广播的消息字典
        """
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"广播消息失败: {e}")
                self.disconnect(connection)
    
    async def send_to(self, websocket: WebSocket, message: dict) -> None:
        """
        发送消息到指定连接
        
        参数:
            websocket: 目标 WebSocket 连接
            message: 要发送的消息字典
        """
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            self.disconnect(websocket)
    
    def get_connection_count(self) -> int:
        """
        获取当前活跃连接数
        
        返回:
            int: 活跃连接数量
        """
        return len(self.active_connections)
