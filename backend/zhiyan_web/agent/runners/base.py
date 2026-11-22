"""
智能体运行器基类

定义 AgentState 状态机和 BaseAgentRunner 抽象接口。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Generic

from ..run_context import AgentResponse, ContextWrapper, TContext

logger = logging.getLogger("ZhiYanWeb.Agent")


class AgentState(Enum):
    """智能体状态"""
    IDLE = auto()      # 初始状态
    RUNNING = auto()   # 处理中
    DONE = auto()      # 完成
    ERROR = auto()     # 错误


class BaseAgentRunner(ABC, Generic[TContext]):
    """智能体运行器基类

    定义状态机和核心接口。
    """

    def __init__(self):
        self.state: AgentState = AgentState.IDLE
        self.run_context: ContextWrapper[TContext] | None = None
        self._hooks: list = []

    @abstractmethod
    async def step_until_done(
        self, max_step: int = 10
    ) -> AsyncGenerator[AgentResponse, None]:
        """执行步骤直到完成

        Args:
            max_step: 最大步数

        Yields:
            AgentResponse: 每步的响应
        """
        ...

    @abstractmethod
    def request_stop(self) -> None:
        """请求停止"""
        ...

    @abstractmethod
    def done(self) -> bool:
        """是否已完成"""
        ...

    def register_hook(self, hook) -> None:
        """注册钩子"""
        if hook not in self._hooks:
            self._hooks.append(hook)

    def unregister_hook(self, hook) -> None:
        """注销钩子"""
        if hook in self._hooks:
            self._hooks.remove(hook)

    async def _on_agent_begin(self) -> None:
        """触发智能体开始钩子"""
        for hook in self._hooks:
            try:
                await hook.on_agent_begin(self.run_context)
            except Exception as e:
                logger.warning(f"钩子异常 on_agent_begin: {e}")

    async def _on_tool_start(self, tool, tool_args) -> None:
        """触发工具开始钩子"""
        for hook in self._hooks:
            try:
                await hook.on_tool_start(self.run_context, tool, tool_args)
            except Exception as e:
                logger.warning(f"钩子异常 on_tool_start: {e}")

    async def _on_tool_end(self, tool, tool_args, result) -> None:
        """触发工具结束钩子"""
        for hook in self._hooks:
            try:
                await hook.on_tool_end(self.run_context, tool, tool_args, result)
            except Exception as e:
                logger.warning(f"钩子异常 on_tool_end: {e}")

    async def _on_agent_done(self, result) -> None:
        """触发智能体完成钩子"""
        for hook in self._hooks:
            try:
                await hook.on_agent_done(self.run_context, result)
            except Exception as e:
                logger.warning(f"钩子异常 on_agent_done: {e}")

    def _transition_state(self, new_state: AgentState) -> None:
        """状态转换"""
        old = self.state
        self.state = new_state
        logger.debug(f"智能体状态: {old.name} → {new_state.name}")