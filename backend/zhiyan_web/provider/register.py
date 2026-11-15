"""
函数工具注册表

提供插件注册 LLM 函数工具的接口。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zhiyan_web.agent.tool import FunctionTool

# LLM 函数工具注册表（插件通过此注册表注册函数工具）
llm_tools: set[Any] = set()
"""LLM 函数工具集合，插件通过 context.add_llm_tools() 注册"""