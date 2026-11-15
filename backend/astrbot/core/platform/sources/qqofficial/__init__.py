"""
astrbot.core.platform.sources.qqofficial 兼容子包

ZhiYan 的 QQ 平台基于 OneBot(aiocqhttp)，并非 QQ 官方 bot(botpy)。
为兼容依赖该模块做 isinstance 类型判别的第三方插件（如 astrbot_plugin_music），
提供轻量占位类，使插件可正常加载；QQ官方分支在实际运行中不会命中。
"""

from __future__ import annotations

from .qqofficial_message_event import QQOfficialMessageEvent
from .qqofficial_platform_adapter import QQOfficialPlatformAdapter

__all__ = ["QQOfficialMessageEvent", "QQOfficialPlatformAdapter"]