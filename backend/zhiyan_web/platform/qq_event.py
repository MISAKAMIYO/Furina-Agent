"""
QQ 平台事件模型与 AstrMessageEvent 适配
======================================

将 NapCat / OneBot 11 的服务端事件（message / notice / request / meta_event）
转换为统一的内部数据结构，并提供与 AstrBot 兼容的 QQMessageEvent
（继承 zhiyan_web 的 AstrMessageEvent，写法对齐 aiocqhttp 兼容 shim）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from zhiyan_web.star.platform import AstrMessageEvent, MessageSession

# 非文本消息片段的占位标记（作为纯文本进入对话）
_NON_TEXT_PLACEHOLDERS = {
    "image": "[图片]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "at": "@",
    "face": "[表情]",
    "anon": "[匿名消息]",
}


@dataclass
class QQSegment:
    """OneBot 11 消息段（segment）"""
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QQMessageEvent:
    """解析后的 QQ 消息事件（OneBot post_type=message）"""
    message_id: str = ""
    message_type: str = ""          # private / group
    sub_type: str = ""
    user_id: str = ""
    group_id: str = ""
    self_id: str = ""
    nickname: str = ""
    card: str = ""
    role: str = "member"
    segments: List[QQSegment] = field(default_factory=list)
    raw_message: str = ""
    post_type: str = "message"
    time: float = field(default_factory=time.time)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_onebot(cls, raw: Dict[str, Any], self_id: str = "") -> "QQMessageEvent":
        ev = cls()
        ev.raw = raw
        ev.post_type = raw.get("post_type", "message")
        ev.message_id = str(raw.get("message_id", ""))
        ev.message_type = raw.get("message_type", "")
        ev.sub_type = raw.get("sub_type", "")
        ev.user_id = str(raw.get("user_id", ""))
        ev.self_id = str(raw.get("self_id", "") or self_id)
        ev.group_id = str(raw.get("group_id", "")) if raw.get("group_id") is not None else ""
        ev.time = raw.get("time", time.time())

        sender = raw.get("sender") or {}
        ev.nickname = sender.get("nickname", "") or ""
        ev.card = sender.get("card", "") or ""
        ev.role = sender.get("role", "member") or "member"

        message = raw.get("message")
        if isinstance(message, str):
            ev.segments = [QQSegment(type="text", data={"text": message})]
            ev.raw_message = message
        elif isinstance(message, list):
            ev.segments = [QQSegment(type=s.get("type", ""), data=s.get("data") or {}) for s in message]
            ev.raw_message = cls._segments_to_text(ev.segments, self_id=ev.self_id)
        else:
            ev.raw_message = raw.get("raw_message", "") or ""
        return ev

    @staticmethod
    def _segments_to_text(segments: List[QQSegment], self_id: str = "", card_mode: bool = True) -> str:
        """将消息段转为纯文本（非文本段用占位标记表示；at 目标机器人时保留 @）"""
        parts: List[str] = []
        for seg in segments:
            data = seg.data
            if seg.type == "text":
                t = (data.get("text") or "") if isinstance(data, dict) else str(data or "")
                parts.append(t)
            elif seg.type == "at":
                qq = data.get("qq")
                if self_id and str(qq) == str(self_id):
                    parts.append("@机器人")
                elif qq in (0, "0", "all"):
                    parts.append("@全体成员")
                else:
                    parts.append(f"@{qq}")
            else:
                # 其余非文本段使用占位标记，保证会话文本可读
                parts.append(_NON_TEXT_PLACEHOLDERS.get(seg.type, f"[{seg.type}]"))
        return "".join(parts)

    def to_text(self, self_id: str = "") -> str:
        """获取消息的纯文本内容"""
        return self._segments_to_text(self.segments, self_id=self_id or self.self_id)

    def contains_at_self(self, self_id: str = "") -> bool:
        """群消息中是否 @ 了机器人自身（用于唤醒判定）"""
        target = str(self_id or self.self_id)
        for seg in self.segments:
            if seg.type == "at":
                qq = (seg.data or {}).get("qq")
                if target and str(qq) == target:
                    return True
        return False

    def contains_at_all(self) -> bool:
        """群消息中是否 @ 了全体成员（用于截停判定）"""
        for seg in self.segments:
            if seg.type == "at":
                qq = str((seg.data or {}).get("qq", ""))
                if qq in ("all", "0"):
                    return True
        return False

    @property
    def is_group(self) -> bool:
        return self.message_type == "group"

    @property
    def is_private(self) -> bool:
        return self.message_type == "private"

    @property
    def session_id(self) -> str:
        """会话标识：群聊按 group_id，私聊按 user_id"""
        if self.is_group:
            return f"g_{self.group_id}"
        return f"p_{self.user_id}"


class QQMessageEventAdapter:
    """把 QQMessageEvent 转成管道所需的 kwargs / AstrMessageEvent"""

    @staticmethod
    def to_pipeline_kwargs(ev: QQMessageEvent, wake_prefixes: List[str]) -> Dict[str, Any]:
        """构造 process_via_pipeline 的 kwargs（这些键会进入 PipelineEvent.data → 过滤器 cfg）"""
        return {
            "qq": True,
            "platform_name": "qq",
            "message_type": ev.message_type,
            "group_id": ev.group_id,
            "user_id": ev.user_id,
            "self_id": ev.self_id,
            "message_id": ev.message_id,
            "sender_id": ev.user_id,
            "sender_name": ev.nickname or ev.card,
            "sender_role": ev.role,
            "is_at_or_wake_command": True,
            "wake_prefixes": wake_prefixes,
        }

    @staticmethod
    def to_astr_event(ev: QQMessageEvent) -> "QQMessageEventCompat":
        """构造与 AstrBot 插件兼容的 AstrMessageEvent 子类对象"""
        event = QQMessageEventCompat(
            message_str=ev.to_text(self_id=ev.self_id),
            session_id=ev.session_id,
            sender_id=ev.user_id,
            sender_name=ev.nickname or ev.card,
        )
        event.session = MessageSession(
            platform_name="qq",
            message_type=ev.message_type,
            session_id=ev.session_id,
            sender_id=ev.user_id,
            group_id=ev.group_id,
        )
        event.message_obj.group_id = ev.group_id
        event.message_obj.user_id = ev.user_id
        event.message_obj.self_id = ev.self_id
        event.message_obj.message_id = ev.message_id
        event.message_obj.message_type = ev.message_type
        event.message_obj.post_type = ev.post_type
        event.self_id = ev.self_id
        event.message_id = ev.message_id
        event.raw_message = ev.raw
        event.owner = ev
        return event


class QQMessageEventCompat(AstrMessageEvent):
    """与 AstrBot 插件兼容的 QQ 消息事件

    提供 bot / self_id / message_id / raw_message 等字段，
    以及 get_group_id/get_self_id/get_message_id 等方法，
    写法对齐 backend/astrbot ... aiocqhttp_message_event.py。

    注意：管道在处理插件 Handler 时会自行构造 AstrMessageEvent，
    此对象用于希望直接拿到 QQ 上下文、或对齐 AstrBot 插件实例类型的场景。
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.bot = None                 # OneBotClient 实例（由 manager 回填）
        self.self_id: str = ""
        self.message_id: str = ""
        self.raw_message: dict = {}
        self.owner: Optional[QQMessageEvent] = None

    def get_platform_name(self) -> str:
        return "qq"

    def get_group_id(self) -> str:
        return self.session.group_id if hasattr(self.session, "group_id") else ""

    def get_self_id(self) -> str:
        return self.self_id

    def get_message_id(self) -> str:
        return self.message_id

    def is_private_chat(self) -> bool:
        return self.get_group_id() == ""


__all__ = [
    "QQSegment",
    "QQMessageEvent",
    "QQMessageEventAdapter",
    "QQMessageEventCompat",
]