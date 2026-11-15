"""
AstrBot api.platform 兼容模块
"""

from zhiyan_web.star import AstrMessageEvent, MessageSession


class AstrBotMessage:
    """AstrBot 消息对象（兼容）"""
    def __init__(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class Platform:
    """平台适配器（兼容 AstrBot）"""
    def __init__(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


__all__ = ["AstrMessageEvent", "MessageSession", "AstrBotMessage", "Platform"]