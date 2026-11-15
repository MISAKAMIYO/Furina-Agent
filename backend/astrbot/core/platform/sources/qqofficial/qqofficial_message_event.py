"""
QQOfficialMessageEvent 兼容占位类

ZhiYan 不使用 QQ 官方 bot(botpy) 平台，此占位类仅供第三方插件做 isinstance
类型检查，使其能通过加载；实际事件均由 ZhiYan 的 OneBot 事件产生，
isinstance(ev, QQOfficialMessageEvent) 恒为 False，插件会退化到 aiocqhttp 分支。
"""

from __future__ import annotations

from zhiyan_web.star import AstrMessageEvent


class QQOfficialMessageEvent(AstrMessageEvent):
    """兼容 AstrBot 的 QQ官方(频道)消息事件（占位，不用于实际消息）。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.bot = None
        """QQ官方 bot 客户端实例（占位为空）"""
        self.message_obj = None
        """原始消息对象，结构同 botpy.message.Message"""
        self.message_id: str = ""
        """消息 ID"""

    def get_group_id(self) -> str:
        return getattr(getattr(self, "session", None), "group_id", "") or ""

    def get_self_id(self) -> str:
        return getattr(self, "self_id", "") or ""

    def get_message_id(self) -> str:
        return self.message_id


__all__ = ["QQOfficialMessageEvent"]