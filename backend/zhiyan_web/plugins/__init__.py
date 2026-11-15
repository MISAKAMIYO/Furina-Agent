"""
插件系统模块 - 兼容层
此模块为旧版代码提供向后兼容，所有功能委托给 star/ 模块实现

插件系统核心模块位于 zhiyan_web.star，使用 AstrBot 完全兼容的插件格式。
"""
from __future__ import annotations

# 从 star 模块重新导出核心类型
from zhiyan_web.star import (
    Star, StarMetadata, Context, PluginManager,
    EventType, StarHandlerRegistry, StarHandlerMetadata,
    star_map, star_registry, star_handlers_registry,
    AstrMessageEvent, MessageChain, MessageEventResult, MessageSession,
    register,
)
# 兼容别名
StarEventType = EventType

# 向后兼容别名
from .base import PluginBase, CommandParserMixin, PluginKVStoreMixin
from .metadata import PluginMetadata, plugin_registry, plugin_map
from .context import PluginContext
from .manager import PluginManager as LegacyPluginManager
from .event_dispatcher import EventDispatcher, EventType, event_handler_registry, EventHandlerMetadata, EventHandlerRegistry

__all__ = [
    # Star 核心类型
    "Star",
    "StarMetadata",
    "Context",
    "PluginManager",
    "EventType",
    "StarHandlerRegistry",
    "StarHandlerMetadata",
    "star_map",
    "star_registry",
    "star_handlers_registry",
    "AstrMessageEvent",
    "MessageChain",
    "MessageEventResult",
    "MessageSession",
    "register",
    # 兼容别名
    "StarEventType",
    # 旧版兼容类型
    "PluginBase",
    "PluginMetadata",
    "PluginContext",
    "LegacyPluginManager",
    "EventDispatcher",
    "event_handler_registry",
    "EventHandlerMetadata",
    "EventHandlerRegistry",
    "CommandParserMixin",
    "PluginKVStoreMixin",
    "plugin_registry",
    "plugin_map",
]