"""
AstrBot filter 兼容模块 - 作为 from astrbot.api.event import filter 的目标

提供 filter.command(), filter.event_message_type(), filter.on_decorating_result(),
filter.on_llm_request(), filter.permission_type() 等装饰器。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable

from zhiyan_web.star import (
    EventType,
    star_handlers_registry,
    StarHandlerMetadata,
)
from zhiyan_web.star.register import (
    register_command,
    register_on_llm_request,
    register_on_decorating_result,
)
from zhiyan_web.star.filter.permission import PermissionType, PermissionTypeFilter


class EventMessageType(Enum):
    """消息事件类型枚举，兼容 AstrBot"""
    ALL = "all"
    GROUP_MESSAGE = "group"
    FRIEND_MESSAGE = "friend"
    PRIVATE_MESSAGE = "private"


def command(command_name: str, alias: set | None = None, **kwargs) -> Callable:
    """注册命令处理器
    
    用法: @filter.command("命令名", alias={"别名1", "别名2"})
    """
    return register_command(command_name=command_name, alias=alias, **kwargs)


def regex(pattern: str | "re.Pattern", **kwargs) -> Callable:
    """注册正则表达式处理器（匹配消息即触发）

    用法: @filter.regex(r"^你好$")
    """
    from zhiyan_web.star.register import register_regex
    return register_regex(pattern, **kwargs)


class _EventMessageTypeFilter:
    """事件消息类型过滤器"""
    def __init__(self, event_type: EventMessageType):
        self.event_type = event_type

    def filter(self, event, cfg: dict) -> bool:
        if self.event_type == EventMessageType.ALL:
            return True
        msg_type = getattr(getattr(event, 'session', None), 'message_type', '')
        if self.event_type == EventMessageType.GROUP_MESSAGE:
            return msg_type == 'group'
        if self.event_type in (EventMessageType.FRIEND_MESSAGE, EventMessageType.PRIVATE_MESSAGE):
            return msg_type == 'friend'
        return True


def event_message_type(event_type: EventMessageType, priority: int = 0) -> Callable:
    """注册一个处理所有消息的处理器（不限定命令）
    
    用法: @filter.event_message_type(filter.EventMessageType.ALL, priority=10)
    """
    def decorator(func: Callable) -> Callable:
        handler_full_name = f"{func.__module__}_{func.__name__}"
        md = star_handlers_registry.get_handler_by_full_name(handler_full_name)
        if not md:
            md = StarHandlerMetadata(
                event_type=EventType.AdapterMessageEvent,
                handler_full_name=handler_full_name,
                handler_name=func.__name__,
                handler_module_path=func.__module__,
                handler=func,
                event_filters=[],
            )
            md.extras_configs["priority"] = priority
            star_handlers_registry.append(md)
        # 添加事件消息类型过滤器
        md.event_filters.append(_EventMessageTypeFilter(event_type))
        return func
    return decorator


def on_decorating_result(priority: int = 0) -> Callable:
    """注册结果装饰事件处理器"""
    return register_on_decorating_result(**{"priority": priority})


def on_llm_request(priority: int = 0) -> Callable:
    """注册 LLM 请求事件处理器"""
    return register_on_llm_request(**{"priority": priority})


class CommandGroup:
    """命令组对象，支持 .command() 注册子命令"""
    def __init__(self, name: str, alias: set | None = None, **kwargs) -> None:
        self.name = name
        # 字符串 alias 视为单个别名，避免拆成字符
        if isinstance(alias, str):
            alias = {alias}
        self.alias = alias or set()
        self._handlers: list = []

    def __call__(self, func: Callable) -> "CommandGroup":
        """作为装饰器使用时，注册命令组处理器并返回自身
        
        返回自身实例，以便后续可以 .command() 注册子命令。
        """
        from zhiyan_web.star import (
            EventType,
            star_handlers_registry,
            StarHandlerMetadata,
        )
        handler_full_name = f"{func.__module__}_{func.__name__}"
        md = star_handlers_registry.get_handler_by_full_name(handler_full_name)
        if not md:
            md = StarHandlerMetadata(
                event_type=EventType.AdapterMessageEvent,
                handler_full_name=handler_full_name,
                handler_name=func.__name__,
                handler_module_path=func.__module__,
                handler=func,
                event_filters=[],
            )
            md.extras_configs["command_group"] = self.name
            md.extras_configs["alias"] = self.alias
            star_handlers_registry.append(md)
        return self

    def group(self, name: str, alias: set | None = None, **kwargs) -> "CommandGroup":
        """创建命令组下的子组"""
        sub_group = CommandGroup(name=name, alias=alias, **kwargs)
        self._handlers.append(sub_group)
        return sub_group

    def command(self, command_name: str, alias: set | None = None, **kwargs) -> Callable:
        """注册命令组下的子命令"""
        from zhiyan_web.star import (
            EventType,
            star_handlers_registry,
            StarHandlerMetadata,
        )
        from zhiyan_web.star.filter.command import CommandFilter

        def decorator(func: Callable) -> Callable:
            handler_full_name = f"{func.__module__}_{func.__name__}"
            md = star_handlers_registry.get_handler_by_full_name(handler_full_name)
            if not md:
                md = StarHandlerMetadata(
                    event_type=EventType.AdapterMessageEvent,
                    handler_full_name=handler_full_name,
                    handler_name=func.__name__,
                    handler_module_path=func.__module__,
                    handler=func,
                    event_filters=[],
                )
                md.extras_configs["command_name"] = command_name
                md.extras_configs["group_name"] = self.name
                star_handlers_registry.append(md)

            # 添加 CommandFilter 确保只有匹配的消息才会触发
            parent_names = [self.name] + list(self.alias)
            cmd_filter = CommandFilter(
                command_name=command_name,
                alias=alias,
                handler_md=md,
                parent_command_names=parent_names,
            )
            md.event_filters.append(cmd_filter)

            return func
        return decorator


def command_group(parent_command_names: str | list[str] | None = None, alias: set | None = None, **kwargs) -> CommandGroup:
    """注册命令组
    
    用法: @filter.command_group(["父命令"])
         @filter.command_group("cmdmgr", alias={"指令管理"})
    
    返回 CommandGroup 对象，支持 .command() 注册子命令。
    """
    if isinstance(parent_command_names, str):
        group_name = parent_command_names
    elif isinstance(parent_command_names, list) and parent_command_names:
        group_name = parent_command_names[0]
    else:
        group_name = ""

    return CommandGroup(name=group_name, alias=alias, **kwargs)


def llm_tool(name: str = "", **kwargs) -> Callable:
    """注册 LLM 函数工具
    
    用法: @filter.llm_tool(name="tool_name")
    """
    def decorator(func: Callable) -> Callable:
        handler_full_name = f"{func.__module__}_{func.__name__}"
        md = star_handlers_registry.get_handler_by_full_name(handler_full_name)
        if not md:
            md = StarHandlerMetadata(
                event_type=EventType.OnCallingFuncToolEvent,
                handler_full_name=handler_full_name,
                handler_name=func.__name__,
                handler_module_path=func.__module__,
                handler=func,
                event_filters=[],
            )
            md.extras_configs["name"] = name
            md.extras_configs.update(kwargs)
            star_handlers_registry.append(md)
        return func
    return decorator


def on_llm_response(**kwargs) -> Callable:
    """注册 LLM 响应事件处理器"""
    def decorator(func: Callable) -> Callable:
        return func
    return decorator


def after_message_sent(**kwargs) -> Callable:
    """注册消息发送后事件处理器"""
    def decorator(func: Callable) -> Callable:
        return func
    return decorator


def on_astrbot_loaded(**kwargs) -> Callable:
    """注册 AstrBot 加载完成事件处理器"""
    def decorator(func: Callable) -> Callable:
        return func
    return decorator


def permission_type(permission_type: PermissionType) -> Callable:
    """添加权限过滤器
    
    用法: @filter.permission_type(PermissionType.ADMIN)
    """
    def decorator(func: Callable) -> Callable:
        handler_full_name = f"{func.__module__}_{func.__name__}"
        md = star_handlers_registry.get_handler_by_full_name(handler_full_name)
        if md:
            md.event_filters.append(PermissionTypeFilter(permission_type))
        return func
    return decorator


class PlatformAdapterType:
    """平台适配器类型（兼容 AstrBot）"""
    AIOCQHTTP = "aiocqhttp"
    ALL = "all"


def platform_adapter_type(adapter_type: str = "all") -> Callable:
    """平台适配器类型装饰器
    
    用法: @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    
    兼容 AstrBot 的平台过滤器装饰器。
    """
    def decorator(func: Callable) -> Callable:
        return func
    return decorator


__all__ = [
    "command",
    "event_message_type",
    "on_decorating_result",
    "on_llm_request",
    "permission_type",
    "llm_tool",
    "EventMessageType",
    "PermissionType",
    "PermissionTypeFilter",
    "PlatformAdapterType",
    "CommandGroup",
    "command_group",
    "on_llm_response",
    "after_message_sent",
    "on_astrbot_loaded",
]