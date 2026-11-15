"""
AiocqhttpMessageEvent 兼容模块

提供兼容 AstrBot 的 AiocqhttpMessageEvent 类，
用于 astrbot_plugin_buttons 等插件的类型检查。
"""

from __future__ import annotations

from zhiyan_web.star import AstrMessageEvent


class AiocqhttpMessageEvent(AstrMessageEvent):
    """兼容 AstrBot 的 AiocqhttpMessageEvent
    
    提供 bot 属性等 NapCat/OneBot 相关功能。
    """
    
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.bot = None
        """CQHttp 机器人实例"""
        self.self_id: str = ""
        """机器人自身 QQ 号"""
        self.message_id: str = ""
        """消息 ID"""
        self.raw_message: dict = {}
        """原始消息数据"""

    def get_group_id(self) -> str:
        """获取群 ID"""
        return self.session.group_id if hasattr(self.session, 'group_id') else ""

    def get_self_id(self) -> str:
        return self.self_id

    def get_message_id(self) -> str:
        return self.message_id


__all__ = ["AiocqhttpMessageEvent"]