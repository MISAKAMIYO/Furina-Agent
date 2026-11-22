from __future__ import annotations

import enum
import logging
from typing import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar, overload

from .filter import HandlerFilter
from .star import star_map

logger = logging.getLogger("ZhiYanWeb")

T = TypeVar("T", bound="StarHandlerMetadata")


class StarHandlerRegistry(Generic[T]):
    def __init__(self) -> None:
        self.star_handlers_map: dict[str, StarHandlerMetadata] = {}
        self._handlers: list[StarHandlerMetadata] = []

    def append(self, handler: StarHandlerMetadata) -> None:
        if "priority" not in handler.extras_configs:
            handler.extras_configs["priority"] = 0
        self.star_handlers_map[handler.handler_full_name] = handler
        self._handlers.append(handler)
        self._handlers.sort(key=lambda h: -h.extras_configs["priority"])

    def get_handlers_by_event_type(
        self,
        event_type: EventType,
        only_activated=True,
        plugins_name: list[str] | None = None,
    ) -> list[StarHandlerMetadata]:
        if not isinstance(event_type, EventType):
            logger.warning(f"get_handlers_by_event_type: 无效的事件类型 {event_type}")
            return []
        handlers = []
        for handler in self._handlers:
            if handler.event_type != event_type:
                continue
            if not handler.enabled:
                continue
            if only_activated:
                plugin = star_map.get(handler.handler_module_path)
                if not (plugin and plugin.activated):
                    continue
            if plugins_name is not None and plugins_name != ["*"]:
                plugin = star_map.get(handler.handler_module_path)
                if not plugin:
                    continue
                if (
                    plugin.name not in plugins_name
                    and event_type
                    not in (
                        EventType.OnAstrBotLoadedEvent,
                        EventType.OnPluginLoadedEvent,
                        EventType.OnPluginUnloadedEvent,
                    )
                    and not plugin.reserved
                ):
                    continue
            handlers.append(handler)
        return handlers

    def get_handler_by_full_name(self, full_name: str) -> StarHandlerMetadata | None:
        return self.star_handlers_map.get(full_name, None)

    def get_handlers_by_module_name(self, module_name: str) -> list[StarHandlerMetadata]:
        return [
            handler for handler in self._handlers
            if handler.handler_module_path == module_name
        ]

    def clear(self) -> None:
        self.star_handlers_map.clear()
        self._handlers.clear()

    def remove(self, handler: StarHandlerMetadata) -> None:
        self.star_handlers_map.pop(handler.handler_full_name, None)
        self._handlers = [h for h in self._handlers if h != handler]

    def __iter__(self):
        return iter(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)


star_handlers_registry = StarHandlerRegistry()


class EventType(enum.Enum):
    OnAstrBotLoadedEvent = enum.auto()
    OnPlatformLoadedEvent = enum.auto()
    AdapterMessageEvent = enum.auto()
    # 向后兼容别名
    MessageEvent = AdapterMessageEvent
    OnWaitingLLMRequestEvent = enum.auto()
    OnLLMRequestEvent = enum.auto()
    OnLLMResponseEvent = enum.auto()
    OnAgentBeginEvent = enum.auto()
    OnAgentDoneEvent = enum.auto()
    OnDecoratingResultEvent = enum.auto()
    OnCallingFuncToolEvent = enum.auto()
    OnUsingLLMToolEvent = enum.auto()
    OnLLMToolRespondEvent = enum.auto()
    OnAfterMessageSentEvent = enum.auto()
    OnPluginErrorEvent = enum.auto()
    OnPluginLoadedEvent = enum.auto()
    OnPluginUnloadedEvent = enum.auto()
    OnAstrBotStartingEvent = enum.auto()
    OnAstrBotStopEvent = enum.auto()
    OnAstrBotReadyEvent = enum.auto()
    OnCommandErrorEvent = enum.auto()


H = TypeVar("H", bound=Callable[..., Any])


@dataclass
class StarHandlerMetadata(Generic[H]):
    event_type: EventType
    handler_full_name: str
    handler_name: str
    handler_module_path: str
    handler: H
    event_filters: list[HandlerFilter]
    desc: str = ""
    extras_configs: dict = field(default_factory=dict)
    enabled: bool = True

    def __lt__(self, other: StarHandlerMetadata):
        return self.extras_configs.get("priority", 0) < other.extras_configs.get("priority", 0)