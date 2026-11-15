"""
插件上下文模块 - 兼容层
PluginContext 提供向后兼容，底层使用 star 的 Context
"""

from __future__ import annotations

import logging
from asyncio import Queue
from typing import Any, Awaitable, Callable

from zhiyan_web.star.context import Context as StarContext
from zhiyan_web.star.star import star_registry
from zhiyan_web.star.star_handler import EventType, StarHandlerMetadata, star_handlers_registry
from zhiyan_web.star.register.star_handler import get_handler_or_create
from zhiyan_web.star.filter.command import CommandFilter
from zhiyan_web.star.filter.regex import RegexFilter
from zhiyan_web.plugins.command_registry import get_command_registry

logger = logging.getLogger("ZhiYanWeb.plugins")


class PluginContext:
    """
    插件上下文 - 兼容层
    提供向后兼容的接口，底层使用 StarContext
    """

    registered_web_apis: list[tuple] = []
    _register_tasks: list[Awaitable] = []
    _plugin_manager = None

    def __init__(self, event_queue: Queue, config: dict) -> None:
        self._event_queue = event_queue
        self._config = config
        # 创建或使用已有的 StarContext 实例
        self._star_context = None
        try:
            from zhiyan_web.core import star_context
            if star_context:
                self._star_context = star_context
        except (ImportError, AttributeError):
            pass

    def _get_star_context(self) -> StarContext:
        """获取 StarContext 实例"""
        if self._star_context:
            return self._star_context
        # 延迟创建
        try:
            from zhiyan_web.core import star_context
            self._star_context = star_context
        except (ImportError, AttributeError):
            pass
        return self._star_context

    def get_registered_plugin(self, plugin_name: str) -> Any | None:
        for plugin in star_registry:
            if plugin.name == plugin_name:
                return plugin
        return None

    def get_all_plugins(self) -> list[Any]:
        return list(star_registry)

    def get_config(self) -> dict:
        return self._config

    def get_event_queue(self) -> Queue:
        return self._event_queue

    def get_db(self) -> None:
        return None

    def get_time(self) -> int:
        import time
        return int(time.time())

    def get_date(self) -> str:
        import datetime
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def get_datetime(self) -> str:
        import datetime
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async def send_message(self, session: str, content: str) -> bool:
        logger.info("send_message: session=%s, content=%s", session, content[:50])
        return True

    async def broadcast_message(self, content: str) -> bool:
        logger.info("broadcast_message: content=%s", content[:50])
        return True

    def register_web_api(self, route: str, view_handler: Callable, methods: list[str], desc: str) -> None:
        for idx, api in enumerate(self.registered_web_apis):
            if api[0] == route and methods == api[2]:
                self.registered_web_apis[idx] = (route, view_handler, methods, desc)
                return
        self.registered_web_apis.append((route, view_handler, methods, desc))
        logger.info("注册 Web API: %s %s - %s", methods, route, desc)

    def register_commands(
        self,
        plugin_name: str,
        command_name: str,
        desc: str,
        priority: int,
        awaitable: Callable[..., Awaitable[Any]],
        use_regex: bool = False,
    ) -> None:
        # 注册到命令注册中心（检测冲突）
        registry = get_command_registry()
        registry.register(
            command_name=command_name,
            plugin_name=plugin_name,
            plugin_display_name=plugin_name,
            description=desc,
            priority=priority,
            handler_path=getattr(awaitable, '__module__', ''),
        )

        # 每个命令使用独立的包装函数，避免 get_handler_or_create 复用同一个 handler
        # 导致多个 CommandFilter 堆叠到同一个 handler 上
        async def _command_wrapper(event):
            # 将 AstrMessageEvent 转换为 dict 以兼容旧版 on_message(message: dict)
            if hasattr(event, 'get_message_str'):
                msg_dict = {"content": event.get_message_str()}
            elif isinstance(event, dict):
                msg_dict = event
            else:
                msg_dict = {"content": str(event)}
            return await awaitable(msg_dict)

        # 给包装函数一个有意义的名称，便于调试
        _command_wrapper.__name__ = f"{awaitable.__name__}_{command_name}"
        _command_wrapper.__module__ = awaitable.__module__
        _command_wrapper.__qualname__ = f"{awaitable.__qualname__}_{command_name}"

        md = get_handler_or_create(_command_wrapper, EventType.AdapterMessageEvent, priority=priority, desc=desc)
        if use_regex:
            md.event_filters.append(RegexFilter(command_name))
        else:
            cmd_filter = CommandFilter(command_name=command_name, handler_md=md)
            cmd_filter.init_handler_md(md)
            md.event_filters.append(cmd_filter)
        logger.info("注册命令: %s (来自插件 %s)", command_name, plugin_name)

    def register_task(self, task: Awaitable, desc: str) -> None:
        self._register_tasks.append(task)
        logger.info("注册后台任务: %s", desc)

    def unregister_commands(self, plugin_name: str) -> None:
        for handler in list(star_handlers_registry):
            if plugin_name in handler.handler_full_name:
                star_handlers_registry.remove(handler)
                logger.info("卸载命令: %s (来自插件 %s)", handler.handler_name, plugin_name)

    def unregister_web_apis(self, plugin_name: str) -> None:
        self.registered_web_apis = [
            api for api in self.registered_web_apis
            if plugin_name not in str(api[1])
        ]