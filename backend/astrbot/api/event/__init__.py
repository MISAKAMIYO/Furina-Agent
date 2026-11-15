"""
AstrBot event 兼容模块
"""

from __future__ import annotations

from zhiyan_web.star import AstrMessageEvent, MessageEventResult

# 不主动从 filter 子模块导入，避免包初始化问题
# filter 子模块通过 from astrbot.api.event import filter 直接访问

class ResultContentType:
    """结果内容类型（兼容 AstrBot）"""
    PLAIN = "plain"
    IMAGE = "image"
    CHAIN = "chain"


# MessageChain 别名
from zhiyan_web.star.platform.message_chain import MessageChain


__all__ = [
    "AstrMessageEvent",
    "MessageEventResult",
    "ResultContentType",
    "MessageChain",
]