"""
函数工具管理器

管理函数工具的注册、去重和 MCP 客户端集成。
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from zhiyan_web.agent.tool import FunctionTool, ToolSet

logger = logging.getLogger("ZhiYanWeb.Provider")

# 支持的 JSON Schema 类型
SUPPORTED_TYPES = ["string", "number", "object", "array", "boolean"]

PY_TO_JSON_TYPE = {
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "str": "string",
    "dict": "object",
    "list": "array",
    "tuple": "array",
    "set": "array",
}


class FunctionToolManager:
    """函数工具管理器

    管理所有注册的函数工具和 MCP 客户端。
    """

    def __init__(self):
        self.func_list: list[FunctionTool] = []
        self._mcp_runtime: dict[str, Any] = {}

    def empty(self) -> bool:
        """是否为空"""
        return len(self.func_list) == 0

    def spec_to_func(
        self,
        name: str,
        func_args: list[dict],
        desc: str,
        handler: Callable[..., Awaitable[Any] | AsyncGenerator[Any]],
        group: str = "",
    ) -> FunctionTool:
        """将参数规范转换为 FunctionTool"""
        params = {
            "type": "object",
            "properties": {},
        }
        for param in func_args:
            p = copy.deepcopy(param)
            p.pop("name", None)
            params["properties"][param["name"]] = p
        return FunctionTool(
            name=name,
            description=desc,
            parameters=params,
            handler=handler,
            group=group,
        )

    def add_func(
        self,
        name: str,
        func_args: list,
        desc: str,
        handler: Callable[..., Awaitable[Any] | AsyncGenerator[Any]],
        group: str = "",
    ) -> None:
        """添加函数工具

        Args:
            name: 函数名
            func_args: 函数参数列表 [{type, name, description}, ...]
            desc: 函数描述
            handler: 处理函数
            group: 所属工具分组（供 tool_groups 做场景裁剪，留空则自动归类）
        """
        self.remove_func(name)
        self.func_list.append(
            self.spec_to_func(
                name=name,
                func_args=func_args,
                desc=desc,
                handler=handler,
                group=group,
            )
        )
        logger.info(f"添加函数工具: {name}")

    def remove_func(self, name: str) -> None:
        """删除函数工具"""
        for i, f in enumerate(self.func_list):
            if f.name == name:
                self.func_list.pop(i)
                break

    def get_func(self, name: str) -> FunctionTool | None:
        """获取函数工具

        优先返回已激活的工具（后加载的覆盖前面的）。
        """
        for f in reversed(self.func_list):
            if f.name == name and getattr(f, "active", True):
                return f
        for f in reversed(self.func_list):
            if f.name == name:
                return f
        return None

    def list_funcs(self) -> list[FunctionTool]:
        """列出所有已注册的函数工具"""
        return list(self.func_list)

    def get_full_tool_set(self) -> ToolSet:
        """获取完整工具集

        使用 ToolSet.add_tool 进行填充。
        同名工具去重规则：
        - 优先保留 active=True 的工具
        - 当 active 状态相同时，后加载的工具覆盖前面的
        """
        tool_set = ToolSet()
        for tool in self.func_list:
            tool_set.add_tool(tool)
        return tool_set

    async def init_mcp_clients(self, mcp_config_path: str | None = None) -> None:
        """初始化 MCP 客户端

        委派给 zhiyan_web.mcp.manager 的注册表完成真实连接与工具注册；
        连接运行在注册表自己的事件循环线程，这里只负责触发并同步运行时状态。

        Args:
            mcp_config_path: MCP 配置文件路径，缺省为 backend/data/mcp_server.json
        """
        if not mcp_config_path:
            mcp_config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "data",
                "mcp_server.json",
            )

        try:
            from zhiyan_web.mcp.manager import McpRuntime, get_registry

            registry = get_registry()
            if os.path.abspath(mcp_config_path) != os.path.abspath(registry.config_path):
                # 调用方显式指定了不同路径：使用独立实例，避免污染全局注册表
                registry = McpRuntime(mcp_config_path)
            result = await asyncio.to_thread(registry.reload)
            self._mcp_runtime = registry.runtime_state()
            logger.info(
                f"MCP 初始化完成: 已连接 {result.get('connected', 0)} 个服务，"
                f"注册工具 {result.get('tools', 0)} 个"
            )
            for err in result.get("errors", []):
                logger.warning(f"MCP 服务连接失败: {err}")
        except Exception as e:
            logger.error(f"初始化 MCP 客户端失败: {e}")

    def get_mcp_runtime(self) -> dict[str, Any]:
        """获取 MCP 运行时状态

        返回 {服务名: {"config":..., "connected":bool, "tool_count":int, "error":str}}
        """
        try:
            from zhiyan_web.mcp.manager import get_registry

            return get_registry().runtime_state()
        except Exception:
            return dict(self._mcp_runtime)


_func_tool_manager_singleton: FunctionToolManager | None = None


def get_func_tool_manager() -> FunctionToolManager:
    """获取全局函数工具管理器

    优先返回 zhiyan_web.core 中初始化的实例；核心尚未初始化时，
    回退到本模块的懒加载单例，保证外围调用方始终能拿到可用实例。
    """
    try:
        from zhiyan_web.core import func_tool_manager as core_ftm

        if core_ftm is not None:
            return core_ftm
    except Exception:
        pass

    global _func_tool_manager_singleton
    if _func_tool_manager_singleton is None:
        _func_tool_manager_singleton = FunctionToolManager()
    return _func_tool_manager_singleton