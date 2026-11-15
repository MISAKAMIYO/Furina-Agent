"""
子代理事件通道

子代理运行在工具调用的内部（父 agent 的 ReAct 循环里 await 一次工具执行），
而它产生的中间过程需要实时推给前端 SSE 流。两者不在同一个调用栈位置上，
所以用 ContextVar 挂一个异步 sink：父循环执行工具前把 sink 装上，子代理
runner 通过 emit() 往外丢事件，父循环在等待工具执行期间轮询队列并转发。

ContextVar 天然按任务隔离，多个子代理并发时互不串台。
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger("ZhiYanWeb.SubAgent.Events")

EventSink = Callable[[Dict[str, Any]], Awaitable[None]]

_sink: ContextVar[Optional[EventSink]] = ContextVar("zhiyan_subagent_sink", default=None)
_depth: ContextVar[int] = ContextVar("zhiyan_subagent_depth", default=0)


def set_sink(sink: Optional[EventSink]):
    """装载事件 sink，返回 token 供 reset_sink 还原。"""
    return _sink.set(sink)


def reset_sink(token) -> None:
    try:
        _sink.reset(token)
    except (ValueError, LookupError):
        # token 跨上下文复用时可能失效，忽略即可（sink 只影响可视化，不影响功能）
        pass


def get_sink() -> Optional[EventSink]:
    return _sink.get()


def current_depth() -> int:
    """当前子代理嵌套深度（父 agent 为 0，一级子代理为 1）。"""
    return _depth.get()


def push_depth() -> Any:
    return _depth.set(_depth.get() + 1)


def pop_depth(token: Any) -> None:
    try:
        _depth.reset(token)
    except (ValueError, LookupError):
        pass


async def emit(event: Dict[str, Any]) -> None:
    """往父流推送一条子代理事件（没有 sink 时静默丢弃）。"""
    sink = _sink.get()
    if sink is None:
        return
    try:
        await sink(event)
    except Exception as e:
        logger.debug(f"推送子代理事件失败: {e}")
