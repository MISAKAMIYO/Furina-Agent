"""
AstrBot platform 兼容模块
"""

from zhiyan_web.star import AstrMessageEvent
from astrbot.api.platform import AstrBotMessage

__all__ = ["AstrMessageEvent", "AstrBotMessage"]