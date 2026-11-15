"""
智能体运行上下文

在智能体运行期间维护消息列表和状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .tool import ToolSet

TContext = TypeVar("TContext")


@dataclass
class Message:
    """LLM 消息

    Attributes:
        role: 角色 (user / assistant / system / tool)
        content: 消息内容
        type: 消息类型 (text / image / tool_call / tool_result)
        name: 工具名称（工具调用时）
        tool_call_id: 工具调用 ID
    """

    role: str
    content: str
    type: str = "text"
    name: str | None = None
    tool_call_id: str | None = None


@dataclass
class ContextWrapper(Generic[TContext]):
    """智能体运行上下文

    智能体自动维护 messages 列表。

    Attributes:
        context: 自定义上下文数据
        messages: LLM 消息上下文
        tool_call_timeout: 工具调用超时（秒）
        max_tool_calls: 最大工具调用次数
    """

    context: TContext
    messages: list[Message] = field(default_factory=list)
    tool_call_timeout: int = 120
    max_tool_calls: int = 20


@dataclass
class LLMResponse:
    """LLM 响应

    Attributes:
        role: 角色 (assistant / err)
        content: 响应文本
        tool_calls: 工具调用列表
        usage: Token 使用统计
        model: 模型名称
    """

    role: str = "assistant"
    content: str = ""
    tool_calls: list[dict] | None = None
    usage: dict | None = None
    model: str = ""


@dataclass
class AgentResponse:
    """智能体响应

    Attributes:
        type: 响应类型 (text / streaming_delta / aborted / error / done)
        content: 响应内容
        metadata: 额外元数据
    """

    type: str = "text"  # text / streaming_delta / aborted / error / done
    content: str = ""
    metadata: dict | None = None