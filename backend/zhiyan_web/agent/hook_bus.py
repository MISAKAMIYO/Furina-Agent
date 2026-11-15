"""
智能体钩子总线

`agent/hooks.py` 早就定义了 BaseAgentRunHooks（on_agent_begin / on_tool_start /
on_tool_end / on_agent_done），`agent/runners/base.py` 里也有对应的触发逻辑 ——
但那套 runner 从未被实例化，钩子实际上是死代码。

本模块把这套钩子机制接到真正在用的引擎（core/llm/chat_manager.py 的 ReAct 循环）
上：提供一个全局总线 + 一个把 chat_manager 运行现场适配成 ContextWrapper 的工厂。

设计要点：
- 钩子异常一律吞掉并记 warning，绝不允许第三方钩子把主对话流程搞崩；
- 钩子是异步的，且串行触发，顺序即注册顺序；
- 支持声明式注册（模块名+类名），供 extensions 清单加载。
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, List, Optional

from .run_context import ContextWrapper, Message

logger = logging.getLogger("ZhiYanWeb.Agent.Hooks")

AGENT_BEGIN = "on_agent_begin"
TOOL_START = "on_tool_start"
TOOL_END = "on_tool_end"
AGENT_DONE = "on_agent_done"

ALL_EVENTS = (AGENT_BEGIN, TOOL_START, TOOL_END, AGENT_DONE)


class HookBus:
    """智能体钩子总线（全局单例，见 get_hook_bus）。"""

    def __init__(self) -> None:
        self._hooks: List[Any] = []

    def register(self, hook: Any) -> bool:
        """注册一个钩子实例；同一实例重复注册会被忽略。"""
        if hook is None:
            return False
        if hook in self._hooks:
            return False
        self._hooks.append(hook)
        logger.info(f"已注册智能体钩子: {type(hook).__name__}")
        return True

    def unregister(self, hook: Any) -> None:
        if hook in self._hooks:
            self._hooks.remove(hook)

    def clear(self) -> None:
        self._hooks.clear()

    def hooks(self) -> List[Any]:
        return list(self._hooks)

    def count(self) -> int:
        return len(self._hooks)

    def describe(self) -> List[Dict[str, str]]:
        """返回钩子清单（供扩展中心展示）。"""
        result = []
        for hook in self._hooks:
            cls = type(hook)
            result.append({
                "name": cls.__name__,
                "module": getattr(cls, "__module__", ""),
                "events": [
                    ev for ev in ALL_EVENTS
                    if getattr(type(hook), ev, None) is not None
                    and ev in getattr(cls, "__dict__", {})
                ] or list(ALL_EVENTS),
                "doc": (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else "",
            })
        return result

    async def emit_agent_begin(self, ctx: ContextWrapper) -> None:
        for hook in list(self._hooks):
            fn = getattr(hook, AGENT_BEGIN, None)
            if fn is None:
                continue
            try:
                await fn(ctx)
            except Exception as e:
                logger.warning(f"钩子 {type(hook).__name__}.{AGENT_BEGIN} 异常: {e}")

    async def emit_tool_start(self, ctx: ContextWrapper, tool: Any,
                              tool_args: Dict[str, Any]) -> None:
        for hook in list(self._hooks):
            fn = getattr(hook, TOOL_START, None)
            if fn is None:
                continue
            try:
                await fn(ctx, tool, tool_args)
            except Exception as e:
                logger.warning(f"钩子 {type(hook).__name__}.{TOOL_START} 异常: {e}")

    async def emit_tool_end(self, ctx: ContextWrapper, tool: Any,
                            tool_args: Dict[str, Any], tool_result: Any) -> None:
        for hook in list(self._hooks):
            fn = getattr(hook, TOOL_END, None)
            if fn is None:
                continue
            try:
                await fn(ctx, tool, tool_args, tool_result)
            except Exception as e:
                logger.warning(f"钩子 {type(hook).__name__}.{TOOL_END} 异常: {e}")

    async def emit_agent_done(self, ctx: ContextWrapper, result: Any) -> None:
        for hook in list(self._hooks):
            fn = getattr(hook, AGENT_DONE, None)
            if fn is None:
                continue
            try:
                await fn(ctx, result)
            except Exception as e:
                logger.warning(f"钩子 {type(hook).__name__}.{AGENT_DONE} 异常: {e}")


_hook_bus: Optional[HookBus] = None


def get_hook_bus() -> HookBus:
    """获取全局钩子总线单例。"""
    global _hook_bus
    if _hook_bus is None:
        _hook_bus = HookBus()
    return _hook_bus


def build_run_context(conv_id: str = "", engine: str = "agent",
                      iteration: int = 0, messages: Optional[List[Any]] = None,
                      extra: Optional[Dict[str, Any]] = None,
                      max_tool_calls: int = 0) -> ContextWrapper:
    """把 chat_manager 的运行现场适配成钩子框架认识的 ContextWrapper。

    messages 传 LLMMessage 列表时会被转成 run_context.Message，方便钩子
    读取对话历史（只读快照，钩子改它不会影响真实对话）。
    """
    converted: List[Message] = []
    for m in (messages or []):
        try:
            converted.append(Message(
                role=str(getattr(m, "role", "user")),
                content=str(getattr(m, "content", "") or ""),
                type="tool_call" if getattr(m, "tool_calls", None) else "text",
                tool_call_id=getattr(m, "tool_call_id", None),
            ))
        except Exception:
            continue

    ctx_data: Dict[str, Any] = {"conv_id": conv_id, "engine": engine,
                                "iteration": iteration}
    if extra:
        ctx_data.update(extra)
    return ContextWrapper(context=ctx_data, messages=converted,
                          max_tool_calls=max_tool_calls)


def register_hook_from_spec(spec: Any) -> bool:
    """按声明式清单注册钩子。

    支持两种写法：
      {"class": "MyHook", "module": "my_pkg.hooks"}      — 从已可导入的模块取
      {"class": "MyHook", "path": "/abs/path/hooks.py"}   — 从任意文件路径动态加载
    """
    if isinstance(spec, str):
        spec = {"class": spec}
    if not isinstance(spec, dict):
        return False
    cls_name = str(spec.get("class") or "").strip()
    if not cls_name:
        return False

    module = spec.get("module")
    path = spec.get("path")
    try:
        if module:
            mod = importlib.import_module(str(module))
        elif path:
            import os
            import importlib.util
            abs_path = os.path.abspath(str(path))
            if not os.path.isfile(abs_path):
                logger.warning(f"钩子文件不存在: {abs_path}")
                return False
            mod_name = f"zhiyan_ext_hook_{os.path.splitext(os.path.basename(abs_path))[0]}"
            loader_spec = importlib.util.spec_from_file_location(mod_name, abs_path)
            if loader_spec is None or loader_spec.loader is None:
                logger.warning(f"无法加载钩子文件: {abs_path}")
                return False
            mod = importlib.util.module_from_spec(loader_spec)
            loader_spec.loader.exec_module(mod)
        else:
            return False

        hook_cls = getattr(mod, cls_name, None)
        if hook_cls is None:
            logger.warning(f"钩子类不存在: {cls_name}")
            return False
        return get_hook_bus().register(hook_cls())
    except Exception as e:
        logger.warning(f"注册钩子失败 {spec}: {e}")
        return False
