"""
MCP 路由模块

提供 /api/mcp 下的服务管理与工具调用相关端点：
- GET    /api/mcp/servers              列出所有 MCP 服务
- POST   /api/mcp/servers              新增服务
- PATCH  /api/mcp/servers/{name}       更新服务
- DELETE /api/mcp/servers/{name}       删除服务
- POST   /api/mcp/servers/{name}/toggle   启用/停用服务
- POST   /api/mcp/servers/{name}/test     测试连接
- POST   /api/mcp/reload               重载并重连全部 active 服务
- GET    /api/mcp/status               运行状态

说明:
- 路由只做参数校验与转发，真正的网络操作在 McpRuntime 的专用事件循环线程执行，
  因此统一用 asyncio.to_thread 包裹同步方法，避免阻塞主事件循环。
- 所有端点返回 dict，参数非法时返回 {"ok": false, "error": ...}，不抛 HTTPException。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body

from .manager import get_registry

logger = logging.getLogger("ZhiYanWeb.MCP")

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/servers")
async def list_servers():
    """列出所有 MCP 服务（含未连接的，connected=false）"""
    reg = get_registry()
    return await asyncio.to_thread(reg.list_servers)


@router.post("/servers")
async def create_server(payload: dict = Body(default_factory=dict)):
    """新增 MCP 服务；active=true 时尝试连接（连接失败仍算保存成功）"""
    reg = get_registry()
    return await asyncio.to_thread(reg.add_server, payload)


@router.patch("/servers/{name}")
async def update_server(name: str, payload: dict = Body(default_factory=dict)):
    """更新 MCP 服务字段，断开旧连接后按新配置重连（若 active）"""
    reg = get_registry()
    return await asyncio.to_thread(reg.update_server, name, payload)


@router.delete("/servers/{name}")
async def delete_server(name: str):
    """删除 MCP 服务（先断开并注销其工具）"""
    reg = get_registry()
    return await asyncio.to_thread(reg.remove_server, name)


@router.post("/servers/{name}/toggle")
async def toggle_server(name: str, payload: dict = Body(default_factory=dict)):
    """启用/停用服务（启用即连接，停用即断开并注销工具）"""
    reg = get_registry()
    active = bool(payload.get("active", False))
    return await asyncio.to_thread(reg.toggle, name, active)


@router.post("/servers/{name}/test")
async def test_server(name: str, payload: dict | None = Body(default=None)):
    """测试连接：临时连接一次、列出工具后断开"""
    reg = get_registry()
    return await asyncio.to_thread(reg.test_server, name, payload)


@router.post("/reload")
async def reload_servers():
    """断开全部后按配置重连全部 active 服务，重新注册工具"""
    reg = get_registry()
    return await asyncio.to_thread(reg.reload)


@router.get("/status")
async def mcp_status():
    """MCP 运行状态概览"""
    reg = get_registry()
    return await asyncio.to_thread(reg.status)
