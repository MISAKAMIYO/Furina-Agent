"""
AstrBot 兼容的装饰器系统 - 注册器实现

提供插件开发所需的所有装饰器，与 AstrBot 的 register 模块完全兼容。
"""

from __future__ import annotations

import re
from typing import AsyncGenerator, Awaitable, Callable
from typing import Any

from ..filter.command import CommandFilter
from ..filter.command_group import CommandGroupFilter
from ..star_handler import EventType, StarHandlerMetadata, star_handlers_registry


def get_handler_full_name(awaitable: Callable) -> str:
    """获取 Handler 的全名"""
    return f"{awaitable.__module__}_{awaitable.__name__}"


def get_handler_or_create(handler, event_type: EventType, dont_add=False, **kwargs) -> StarHandlerMetadata:
    """获取 Handler 或者创建一个新的 Handler"""
    handler_full_name = get_handler_full_name(handler)
    md = star_handlers_registry.get_handler_by_full_name(handler_full_name)
    if md:
        return md
    md = StarHandlerMetadata(
        event_type=event_type,
        handler_full_name=handler_full_name,
        handler_name=handler.__name__,
        handler_module_path=handler.__module__,
        handler=handler,
        event_filters=[],
    )
    if handler.__doc__:
        md.desc = handler.__doc__.strip()
    if "desc" in kwargs:
        md.desc = kwargs["desc"]
        del kwargs["desc"]
    md.extras_configs = kwargs
    if not dont_add:
        star_handlers_registry.append(md)
    return md


def register_command(
    command_name: str | None = None,
    sub_command: str | None = None,
    alias: set | None = None,
    **kwargs,
):
    """注册一个指令

    支持裸指令和指令组子指令两种模式。

    裸指令:
        @register_command("hello")
        async def hello_handler(self, event):
            ...

    指令组子指令:
        @cmd_group.command("sub")
        async def sub_handler(self, event):
            ...
    """
    new_command = None
    add_to_event_filters = False

    if isinstance(command_name, RegisteringCommandable):
        # 子指令（通过指令组注册）
        if sub_command is not None:
            parent_command_names = command_name.parent_group.get_complete_command_names()
            new_command = CommandFilter(
                sub_command,
                alias,
                None,
                parent_command_names=parent_command_names,
            )
            command_name.parent_group.add_sub_command_filter(new_command)
        else:
            import logging
            logging.getLogger("ZhiYanWeb.star").warning(
                f"No sub_command argument was provided while registering a "
                f"subcommand for {command_name}.",
            )
    elif command_name is None:
        import logging
        logging.getLogger("ZhiYanWeb.star").warning(
            "No command_name argument was provided while registering a bare command."
        )
    else:
        # 裸指令 - 检测命令冲突
        _check_command_conflict(command_name, alias)
        new_command = CommandFilter(command_name, alias, None)
        add_to_event_filters = True

    def decorator(awaitable):
        if not add_to_event_filters:
            kwargs["sub_command"] = True
        handler_md = get_handler_or_create(awaitable, EventType.AdapterMessageEvent, **kwargs)
        if new_command:
            new_command.init_handler_md(handler_md)
            handler_md.event_filters.append(new_command)
        return awaitable
    return decorator


def register_command_group(
    command_group_name: str | None = None,
    sub_command: str | None = None,
    alias: set | None = None,
    **kwargs,
):
    """注册一个指令组

    根指令组:
        @register_command_group("cmdmgr")
        class CmdMgrGroup:
            ...

    子指令组:
        @parent_group.group("sub")
        class SubGroup:
            ...
    """
    new_group = None

    if isinstance(command_group_name, RegisteringCommandable):
        # 子指令组
        if sub_command is None:
            import logging
            logging.getLogger("ZhiYanWeb.star").warning(
                f"No sub_command was specified for a subgroup of command group "
                f"{command_group_name}."
            )
        else:
            new_group = CommandGroupFilter(
                sub_command,
                alias,
                parent_group=command_group_name.parent_group,
            )
            command_group_name.parent_group.add_sub_command_filter(new_group)
    elif command_group_name is None:
        import logging
        logging.getLogger("ZhiYanWeb.star").warning(
            "No name was specified for the root command group."
        )
    else:
        # 根指令组 - 检测命令冲突
        _check_command_conflict(command_group_name, alias)
        new_group = CommandGroupFilter(command_group_name, alias)

    def decorator(obj):
        if new_group:
            handler_md = get_handler_or_create(
                obj,
                EventType.AdapterMessageEvent,
                **kwargs,
            )
            handler_md.event_filters.append(new_group)
            handler_md.extras_configs["command_group"] = True
            return RegisteringCommandable(new_group)
        raise ValueError("注册指令组失败。")
    return decorator


class RegisteringCommandable:
    """用于指令组级联注册

    支持链式调用:
        @register_command_group("cmdmgr")
        class CmdMgr:
            @cmd.group("sub")
            class SubGroup:
                @cmd.command("on")
                async def on(self, event):
                    ...
    """

    group: Callable[..., Callable[..., RegisteringCommandable]] = register_command_group
    command: Callable[..., Callable[..., None]] = register_command

    def __init__(self, parent_group: CommandGroupFilter) -> None:
        self.parent_group = parent_group


def register_regex(regex: str | re.Pattern, **kwargs):
    """注册一个正则表达式指令"""
    from ..filter.regex import RegexFilter
    def decorator(awaitable):
        handler_md = get_handler_or_create(awaitable, EventType.AdapterMessageEvent, **kwargs)
        handler_md.event_filters.append(RegexFilter(regex))
        return awaitable
    return decorator


def register_star(**kwargs):
    """注册一个 Star 插件（装饰器形式的显式注册）"""
    def decorator(cls):
        # Star 基类的 __init_subclass__ 已经自动注册
        # 此装饰器主要用于显式标记，无需额外操作
        return cls
    return decorator


# ===== LLM 工具注册 =====

def register_llm_tool(**kwargs):
    """注册一个 LLM 函数工具

    插件通过此装饰器注册 LLM 可调用的函数工具。
    """
    from ..filter.command import CommandFilter
    from ..star_handler import star_handlers_registry

    def decorator(awaitable):
        handler_md = get_handler_or_create(awaitable, EventType.AdapterMessageEvent, **kwargs)
        # 添加 LLM 工具标记，供 LLM 调用时使用
        handler_md.extras_configs["llm_tool"] = True
        return awaitable
    return decorator


# ===== 生命周期事件注册 =====

def register_on_astrbot_loaded(**kwargs):
    """当 AstrBot 加载完成时"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnAstrBotLoadedEvent, **kwargs)
        return awaitable
    return decorator


def register_on_astrbot_starting(**kwargs):
    """当 AstrBot 启动时"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnAstrBotStartingEvent, **kwargs)
        return awaitable
    return decorator


def register_on_astrbot_stop(**kwargs):
    """当 AstrBot 停止时"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnAstrBotStopEvent, **kwargs)
        return awaitable
    return decorator


def register_on_astrbot_ready(**kwargs):
    """当 AstrBot 就绪时"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnAstrBotReadyEvent, **kwargs)
        return awaitable
    return decorator


def register_on_platform_loaded(**kwargs):
    """当平台加载完成时"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnPlatformLoadedEvent, **kwargs)
        return awaitable
    return decorator


def register_on_plugin_loaded(**kwargs):
    """当有插件加载完成时"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnPluginLoadedEvent, **kwargs)
        return awaitable
    return decorator


def register_on_plugin_unloaded(**kwargs):
    """当有插件卸载完成时"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnPluginUnloadedEvent, **kwargs)
        return awaitable
    return decorator


def register_on_plugin_error(**kwargs):
    """当插件处理消息异常时触发"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnPluginErrorEvent, **kwargs)
        return awaitable
    return decorator


def register_on_command_error(**kwargs):
    """当指令执行出错时触发"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnCommandErrorEvent, **kwargs)
        return awaitable
    return decorator


def register_on_decorating_result(**kwargs):
    """当结果装饰时触发"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnDecoratingResultEvent, **kwargs)
        return awaitable
    return decorator


def register_on_waiting_llm_request(**kwargs):
    """当等待调用 LLM 时触发"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnWaitingLLMRequestEvent, **kwargs)
        return awaitable
    return decorator


def register_on_llm_request(**kwargs):
    """当有 LLM 请求时触发"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnLLMRequestEvent, **kwargs)
        return awaitable
    return decorator


def register_on_llm_response(**kwargs):
    """当有 LLM 响应时触发"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnLLMResponseEvent, **kwargs)
        return awaitable
    return decorator


def register_on_agent_begin(**kwargs):
    """当 Agent 开始运行时触发"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnAgentBeginEvent, **kwargs)
        return awaitable
    return decorator


def register_on_agent_done(**kwargs):
    """当 Agent 运行完成时触发"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnAgentDoneEvent, **kwargs)
        return awaitable
    return decorator


def register_on_using_llm_tool(**kwargs):
    """当调用函数工具前触发"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnUsingLLMToolEvent, **kwargs)
        return awaitable
    return decorator


def register_on_llm_tool_respond(**kwargs):
    """当函数工具响应时触发"""
    def decorator(awaitable):
        _ = get_handler_or_create(awaitable, EventType.OnLLMToolRespondEvent, **kwargs)
        return awaitable
    return decorator


# ===== 命令冲突检测 =====

_registered_command_names: dict[str, list[str]] = {}
"""全局命令名注册表，用于检测命令冲突
key: 命令名
value: [插件模块路径列表]
"""


def _check_command_conflict(command_name: str, alias: set | None = None):
    """检测命令名冲突

    当两个插件注册了相同的命令名时，发出警告日志。
    同时注册到命令注册中心以供查询和冲突解决。
    """
    import logging
    import traceback
    logger = logging.getLogger("ZhiYanWeb.star")

    # 获取调用栈，找到插件模块路径
    stack = traceback.extract_stack()
    caller_path = "unknown"
    calling_module = ""
    for frame in reversed(stack):
        if "zhiyan_web" in frame.filename and "register" not in frame.filename:
            caller_path = frame.filename
            calling_module = frame.filename
            break

    all_names = [command_name] + (list(alias) if isinstance(alias, (set, list, tuple)) else ([alias] if isinstance(alias, str) else []))

    for name in all_names:
        if name in _registered_command_names:
            existing = _registered_command_names[name]
            logger.warning(
                f"命令名冲突检测: '{name}' 已被以下插件注册: {existing}。"
                f"新注册的插件可能会覆盖或冲突。"
            )

    # 记录命令名并注册到注册中心
    for name in all_names:
        if name not in _registered_command_names:
            _registered_command_names[name] = []
        _registered_command_names[name].append(caller_path)

        # 同时注册到命令注册中心
        try:
            from ...plugins.command_registry import get_command_registry
            registry = get_command_registry()
            registry.register(
                command_name=name,
                plugin_name=calling_module.split('\\')[-2] if '\\' in calling_module else calling_module.split('/')[-2] if '/' in calling_module else 'unknown',
                plugin_display_name=calling_module.split('\\')[-2] if '\\' in calling_module else calling_module.split('/')[-2] if '/' in calling_module else 'unknown',
                description="",
                priority=0,
                handler_path=caller_path,
            )
        except Exception:
            pass