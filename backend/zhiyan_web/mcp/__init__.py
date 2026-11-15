"""
ZhiYan MCP（Model Context Protocol）集成模块

对外提供:
- McpRuntime: MCP 服务注册表 + 客户端池（连接运行在专用事件循环线程）
- get_registry(): 获取 McpRuntime 单例
- mcp_router: FastAPI 路由（/api/mcp）

MCP 工具会以 mcp__{服务名}__{工具名} 的命名空间前缀注册进
zhiyan_web.core.func_tool_manager，供 Agent / LLM 调用。
"""

from __future__ import annotations

from .manager import McpRuntime, get_registry
from .routers import router as mcp_router

__all__ = ["McpRuntime", "get_registry", "mcp_router"]
