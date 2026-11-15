"""
AstrBot 兼容的装饰器系统

提供插件开发所需的所有装饰器，与 AstrBot 的 register 模块完全兼容。

装饰器分类:
- 指令注册: register_command, register_command_group, register_regex
- 生命周期: register_on_astrbot_loaded, register_on_astrbot_starting, register_on_astrbot_stop, register_on_astrbot_ready
- 插件事件: register_on_plugin_loaded, register_on_plugin_unloaded, register_on_plugin_error, register_on_command_error
- LLM 事件: register_on_llm_request, register_on_llm_response, register_on_waiting_llm_request
- Agent 事件: register_on_agent_begin, register_on_agent_done
- 工具事件: register_on_using_llm_tool, register_on_llm_tool_respond
- 其他: register_on_platform_loaded, register_on_decorating_result
- 工具: register_llm_tool
- 插件类: register_star
"""
from __future__ import annotations

from .star_handler import (
    get_handler_full_name,
    get_handler_or_create,
    register_command,
    register_command_group,
    register_regex,
    register_star,
    register_llm_tool,
    register_llm_tool as register_llm_tool_func,
    register_on_astrbot_loaded,
    register_on_astrbot_starting,
    register_on_astrbot_stop,
    register_on_astrbot_ready,
    register_on_platform_loaded,
    register_on_plugin_loaded,
    register_on_plugin_unloaded,
    register_on_plugin_error,
    register_on_command_error,
    register_on_decorating_result,
    register_on_waiting_llm_request,
    register_on_llm_request,
    register_on_llm_response,
    register_on_agent_begin,
    register_on_agent_done,
    register_on_using_llm_tool,
    register_on_llm_tool_respond,
    RegisteringCommandable,
)

__all__ = [
    "get_handler_full_name",
    "get_handler_or_create",
    "register_command",
    "register_command_group",
    "register_regex",
    "register_star",
    "register_llm_tool",
    "register_on_astrbot_loaded",
    "register_on_astrbot_starting",
    "register_on_astrbot_stop",
    "register_on_astrbot_ready",
    "register_on_platform_loaded",
    "register_on_plugin_loaded",
    "register_on_plugin_unloaded",
    "register_on_plugin_error",
    "register_on_command_error",
    "register_on_decorating_result",
    "register_on_waiting_llm_request",
    "register_on_llm_request",
    "register_on_llm_response",
    "register_on_agent_begin",
    "register_on_agent_done",
    "register_on_using_llm_tool",
    "register_on_llm_tool_respond",
    "RegisteringCommandable",
]