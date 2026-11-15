"""
AstrBot PlatformAdapterType 兼容模块
"""

from enum import Enum


class PlatformAdapterType(Enum):
    """平台适配器类型枚举（兼容 AstrBot）"""
    AIOCQHTTP = "aiocqhttp"
    WEBCHAT = "webchat"
    ALL = "all"


__all__ = ["PlatformAdapterType"]