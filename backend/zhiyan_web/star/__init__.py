"""
Star 插件系统
基于 AstrBot 插件系统架构，保持与 AstrBot 插件完全兼容

模块结构:
- base.py           - Star 插件基类（__init_subclass__ 自动注册）
- star.py           - StarMetadata 元数据 + star_map/star_registry 全局注册表
- star_handler.py   - EventType 枚举 + StarHandlerRegistry + StarHandlerMetadata
- star_manager.py   - PluginManager 插件生命周期管理
- context.py        - Context 插件上下文 API
- filter/           - Handler 过滤器（Command/Regex/Permission 等）
- register/         - 装饰器系统（register_command, register_llm_tool 等）
- platform/         - AstrMessageEvent 消息事件模型
"""
from __future__ import annotations

from .base import Star
from .context import Context
from .star import StarMetadata, star_map, star_registry
from .star_handler import EventType, StarHandlerRegistry, StarHandlerMetadata, star_handlers_registry
from .star_manager import PluginManager
from .platform import AstrMessageEvent, MessageChain, MessageEventResult, MessageSession

# 导入装饰器系统
from . import register

__all__ = [
    "Context",
    "PluginManager",
    "Star",
    "StarMetadata",
    "star_map",
    "star_registry",
    "EventType",
    "StarHandlerRegistry",
    "StarHandlerMetadata",
    "star_handlers_registry",
    "AstrMessageEvent",
    "MessageChain",
    "MessageEventResult",
    "MessageSession",
    "register",
]