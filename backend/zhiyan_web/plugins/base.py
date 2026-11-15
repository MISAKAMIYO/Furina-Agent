"""
插件基类模块 - 兼容层
PluginBase 提供向后兼容，底层使用 Star 基类
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Awaitable

from zhiyan_web.star import Star
from zhiyan_web.star.star import StarMetadata, star_map, star_registry

if TYPE_CHECKING:
    from .context import PluginContext

logger = logging.getLogger("ZhiYanWeb.plugins")


class CommandParserMixin:
    """命令解析混入类"""

    def parse_command_args(self, message: str) -> list[str]:
        result = []
        current = ""
        in_quotes = False
        quote_char = ""

        for char in message:
            if char in ('"', "'"):
                if in_quotes and char == quote_char:
                    in_quotes = False
                else:
                    in_quotes = True
                    quote_char = char
            elif char == " " and not in_quotes:
                if current:
                    result.append(current)
                    current = ""
            else:
                current += char

        if current:
            result.append(current)

        return result


class PluginKVStoreMixin:
    """插件键值存储混入类"""

    def __init__(self):
        self._kv_store: dict[str, Any] = {}

    def kv_set(self, key: str, value: Any) -> None:
        self._kv_store[key] = value

    def kv_get(self, key: str, default: Any = None) -> Any:
        return self._kv_store.get(key, default)

    def kv_delete(self, key: str) -> None:
        self._kv_store.pop(key, None)

    def kv_clear(self) -> None:
        self._kv_store.clear()


class PluginBase(CommandParserMixin, PluginKVStoreMixin):
    """
    插件基类 - 兼容层
    继承此类编写旧版插件，新插件请直接继承 Star

    底层使用 Star 的 __init_subclass__ 自动注册机制
    """

    author: str = "unknown"
    name: str = "unknown"
    context: PluginContext | None = None

    def __init__(self, context: PluginContext, config: dict | None = None) -> None:
        super().__init__()
        self.context = context
        self._config = config or {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # 同时注册到旧版 plugin_map 以保持兼容
        from .metadata import PluginMetadata, plugin_map, plugin_registry
        if not plugin_map.get(cls.__module__):
            metadata = PluginMetadata(
                plugin_cls_type=cls,
                module_path=cls.__module__,
            )
            plugin_map[cls.__module__] = metadata
            plugin_registry.append(metadata)
        else:
            plugin_map[cls.__module__].plugin_cls_type = cls
            plugin_map[cls.__module__].module_path = cls.__module__

    # === 生命周期钩子 ===

    async def initialize(self) -> None:
        """插件初始化时调用"""
        pass

    async def on_message(self, message: dict) -> str | None:
        """收到消息时调用"""
        return None

    async def on_event(self, event_type: str, event_data: dict) -> None:
        """收到事件时调用"""
        pass

    async def on_tick(self) -> None:
        """定时心跳"""
        pass

    async def terminate(self) -> None:
        """插件卸载时调用"""
        pass

    def __del__(self) -> None:
        pass

    # === 消息发送 ===

    async def reply_message(self, message_id: str, content: str) -> bool:
        return await self.context.send_message(message_id, content)

    async def send_message(self, session: str | Any, content: str) -> bool:
        return await self.context.send_message(session, content)

    async def broadcast(self, content: str) -> bool:
        return await self.context.broadcast_message(content)

    # === 配置管理 ===

    def get_config(self) -> dict:
        return self._config

    def get_context_config(self) -> dict:
        ctx_config = getattr(self.context, "get_config", None)
        if callable(ctx_config):
            try:
                return ctx_config()
            except Exception:
                pass
        return {}

    # === 事件队列 ===

    def get_event_queue(self):
        return self.context.get_event_queue()

    # === 注册器 ===

    def register_command(self, command_name: str, desc: str = "", priority: int = 0):
        """注册命令处理器（直接注册，不需要装饰器语法）"""
        self.context.register_commands(
            plugin_name=self.name,
            command_name=command_name,
            desc=desc,
            priority=priority,
            awaitable=self.on_message,
        )

    def register_regex(self, regex: str, desc: str = "", priority: int = 0):
        """注册正则表达式处理器（直接注册，不需要装饰器语法）"""
        self.context.register_commands(
            plugin_name=self.name,
            command_name=regex,
            desc=desc,
            priority=priority,
            awaitable=self.on_message,
            use_regex=True,
        )

    def register_web_api(self, route: str, methods: list[str] = None, desc: str = ""):
        methods = methods or ["GET"]
        def decorator(func):
            self.context.register_web_api(route, func, methods, desc)
            return func
        return decorator

    def register_task(self, task: Awaitable, desc: str = "") -> None:
        self.context.register_task(task, desc)

    def get_plugin(self, plugin_name: str) -> Any | None:
        return self.context.get_registered_plugin(plugin_name)

    def get_all_plugins(self) -> list[Any]:
        return self.context.get_all_plugins()

    def get_db(self):
        return self.context.get_db()

    def get_time(self) -> int:
        return self.context.get_time()

    def get_date(self) -> str:
        return self.context.get_date()

    def get_datetime(self) -> str:
        return self.context.get_datetime()