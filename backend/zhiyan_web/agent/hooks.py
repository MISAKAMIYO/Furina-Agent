"""
智能体生命周期钩子

提供钩子基类，允许插件在智能体运行的不同阶段介入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from .run_context import ContextWrapper
    from .tool import FunctionTool

TContext = TypeVar("TContext")


class BaseAgentRunHooks(Generic[TContext]):
    """智能体运行钩子基类

    插件可以继承此类并注册到智能体运行器，在以下时机被调用：
    - on_agent_begin: 智能体开始运行时
    - on_tool_start: 工具开始执行时
    - on_tool_end: 工具执行完成时
    - on_agent_done: 智能体完成时
    """

    async def on_agent_begin(self, run_context: "ContextWrapper[TContext]") -> None:
        """智能体开始运行"""
        ...

    async def on_tool_start(
        self,
        run_context: "ContextWrapper[TContext]",
        tool: "FunctionTool",
        tool_args: dict[str, Any],
    ) -> None:
        """工具开始执行"""
        ...

    async def on_tool_end(
        self,
        run_context: "ContextWrapper[TContext]",
        tool: "FunctionTool",
        tool_args: dict[str, Any],
        tool_result: Any,
    ) -> None:
        """工具执行完成"""
        ...

    async def on_agent_done(
        self,
        run_context: "ContextWrapper[TContext]",
        result: Any,
    ) -> None:
        """智能体完成"""
        ...