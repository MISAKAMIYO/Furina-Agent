"""
函数工具定义

提供 FunctionTool 和 ToolSet 类，用于工具注册和格式转换。
"""

from __future__ import annotations

import copy
import logging
from typing import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ZhiYanWeb.Agent")


@dataclass
class FunctionTool:
    """函数工具

    定义可供 LLM 调用的工具。

    Attributes:
        name: 工具名称
        description: 工具描述
        parameters: JSON Schema 参数定义
        handler: 处理函数
        active: 是否激活
        is_background_task: 是否后台任务
        group: 所属工具分组（留空则由 tool_groups 按模式自动归类）
    """

    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Callable[..., Awaitable[Any]] | None = None
    active: bool = True
    is_background_task: bool = False
    group: str = ""

    def to_openai_format(self) -> dict:
        """转换为 OpenAI 工具格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_format(self) -> dict:
        """转换为 Anthropic 工具格式"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


class ToolSet:
    """工具集合

    管理多个 FunctionTool，支持去重和格式转换。
    """

    def __init__(self):
        self._tools: dict[str, list[FunctionTool]] = {}

    def add_tool(self, tool: FunctionTool) -> None:
        """添加工具

        去重规则：
        - 优先保留 active=True 的工具
        - 后加载的工具覆盖前面的工具（同 active 状态时）
        """
        if tool.name not in self._tools:
            self._tools[tool.name] = [tool]
            return

        existing = self._tools[tool.name]
        last = existing[-1]

        # 如果新工具 active 状态与最后一个相同 → 替换
        if getattr(tool, "active", True) == getattr(last, "active", True):
            existing[-1] = tool
        # 如果新工具 active 且最后一个 inactive → 添加
        elif getattr(tool, "active", True) and not getattr(last, "active", True):
            existing.append(tool)

    def get_tool(self, name: str) -> FunctionTool | None:
        """获取工具

        优先返回已激活的（后加载的优先）。
        """
        tools = self._tools.get(name)
        if not tools:
            return None
        for t in reversed(tools):
            if getattr(t, "active", True):
                return t
        return tools[-1]  # 退化：最后一个

    def remove_tool(self, name: str) -> None:
        """删除工具"""
        self._tools.pop(name, None)

    def to_openai_format(self) -> list[dict]:
        """转换为 OpenAI 工具格式列表"""
        result = []
        for name, tools in self._tools.items():
            for t in reversed(tools):
                if getattr(t, "active", True):
                    result.append(t.to_openai_format())
                    break
        return result

    def to_anthropic_format(self) -> list[dict]:
        """转换为 Anthropic 工具格式列表"""
        result = []
        for name, tools in self._tools.items():
            for t in reversed(tools):
                if getattr(t, "active", True):
                    result.append(t.to_anthropic_format())
                    break
        return result

    @property
    def tools(self) -> list[FunctionTool]:
        """获取所有激活的工具"""
        result = []
        for name, tools in self._tools.items():
            for t in reversed(tools):
                if getattr(t, "active", True):
                    result.append(t)
                    break
        return result

    def __len__(self) -> int:
        return len(self.tools)

    def __bool__(self) -> bool:
        return len(self) > 0