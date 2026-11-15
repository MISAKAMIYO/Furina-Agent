"""
事件分发器模块 - 兼容层
所有功能委托给 zhiyan_web.star.star_handler 实现
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from zhiyan_web.star.star_handler import EventType, StarHandlerRegistry, StarHandlerMetadata, star_handlers_registry

logger = logging.getLogger("ZhiYanWeb.plugins")


# 向后兼容别名
EventHandlerMetadata = StarHandlerMetadata
EventHandlerRegistry = StarHandlerRegistry
event_handler_registry = star_handlers_registry


class EventDispatcher:
    """事件分发器 - 兼容层"""

    def __init__(self, config: dict = None):
        self._config = config or {}

    async def dispatch(self, event_type: EventType, event_data: dict = None) -> list[Any]:
        handlers = star_handlers_registry.get_handlers_by_event_type(event_type)
        results = []
        for handler in handlers:
            try:
                result = await self._call_handler(handler, event_data or {})
                if result is not None:
                    results.append(result)
            except Exception as e:
                logger.error("执行事件处理器 %s 时出错: %s", handler.handler_name, e)
        return results

    async def _call_handler(self, handler: StarHandlerMetadata, event_data: dict) -> Any:
        try:
            if handler.handler is None:
                return None
            if asyncio.iscoroutinefunction(handler.handler):
                return await handler.handler(event_data)
            else:
                return handler.handler(event_data)
        except TypeError:
            try:
                if asyncio.iscoroutinefunction(handler.handler):
                    return await handler.handler(event_data)
                else:
                    return handler.handler(event_data)
            except Exception as e:
                logger.error("调用处理器 %s 失败: %s", handler.handler_name, e)
                return None