"""
AstrBot api.all 兼容模块 - 通过 from astrbot.api.all import *
"""

from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api.event.filter import EventMessageType, event_message_type, command, command_group, llm_tool
from astrbot.api.message_components import Plain, Image, At, Face, Reply, Node, Nodes, Poke, Share, Record
from astrbot.core.message.components import BaseMessageComponent
from astrbot.api.platform import AstrBotMessage, Platform
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.api import logger

# 兼容 AstrBot 的 MessageChain / MessageType
from zhiyan_web.star.platform.message_chain import MessageChain
from enum import Enum

# AstrBot 旧版别名：CommandResult 已并入 MessageEventResult
CommandResult = MessageEventResult


class MessageType(Enum):
    """消息类型枚举"""
    FRIEND_MESSAGE = "friend"
    GROUP_MESSAGE = "group"
    ALL = "all"


class MessageMember:
    """消息成员（兼容 AstrBot）"""
    def __init__(self, **kwargs) -> None:
        self.user_id = kwargs.get("user_id", "")
        self.nickname = kwargs.get("nickname", "")
        for k, v in kwargs.items():
            setattr(self, k, v)


class PlatformMetadata:
    """平台元数据（兼容 AstrBot）"""
    def __init__(self, **kwargs) -> None:
        self.type = kwargs.get("type", "")
        for k, v in kwargs.items():
            setattr(self, k, v)


__all__ = [
    "AstrMessageEvent",
    "MessageEventResult",
    "CommandResult",
    "Plain",
    "Image",
    "At",
    "Face",
    "Reply",
    "Node",
    "Nodes",
    "Poke",
    "Share",
    "Record",
    "Context",
    "Star",
    "register",
    "ProviderRequest",
    "AstrBotConfig",
    "MessageChain",
    "MessageType",
    "EventMessageType",
    "event_message_type",
    "command",
    "MessageMember",
    "PlatformMetadata",
    "BaseMessageComponent",
    "LLMResponse",
    "ProviderRequest",
    "command_group",
    "llm_tool",
    "logger",
    "AstrBotMessage",
    "Platform",
]