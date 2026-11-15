"""
QQ 平台接入 —— 包初始化
"""
from .manager import QQPlatformManager, get_qq_platform_manager
from .onebot_client import OneBotClient
from .qq_event import (
    QQMessageEvent,
    QQMessageEventAdapter,
    QQMessageEventCompat,
)

__all__ = [
    "QQPlatformManager",
    "get_qq_platform_manager",
    "OneBotClient",
    "QQMessageEvent",
    "QQMessageEventAdapter",
    "QQMessageEventCompat",
]