"""
插件系统 API 路由
使用 star 模块的 PluginManager（AstrBot 完全兼容）

API 接口列表:
- GET /api/plugins/           - 获取所有插件状态
- GET /api/plugins/marketplace - 获取插件市场列表
- POST /api/plugins/install      - 安装插件（从 GitHub）
- POST /api/plugins/{name}/uninstall - 卸载插件
- GET /api/plugins/{name}/detail - 获取插件详情
- POST /api/plugins/{name}/enable  - 启用插件
- POST /api/plugins/{name}/disable - 禁用插件
- POST /api/plugins/{name}/reload  - 重载单个插件
- POST /api/plugins/reload-all     - 重载所有插件
- GET /api/plugins/{name}/config   - 获取插件配置
- POST /api/plugins/{name}/config  - 更新插件配置
- GET /api/plugins/{name}/deps      - 查询插件依赖状态
- POST /api/plugins/{name}/deps/install - 手动安装插件依赖
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..command_registry import get_command_registry

logger = logging.getLogger("ZhiYanWeb.plugins")

plugin_router = APIRouter(prefix="/api/plugins", tags=["plugins"])

# 全局实例引用
_plugin_manager = None


def set_plugin_manager(manager):
    global _plugin_manager
    _plugin_manager = manager


def get_plugin_manager():
    """获取插件管理器实例（供其他模块，如 QQ 平台 /command_control 使用）"""
    return _plugin_manager


class PluginConfigUpdate(BaseModel):
    config: dict


class PluginInstallRequest(BaseModel):
    source: str
    """GitHub 仓库 URL 或简写（author/repo）"""


# ===== 插件状态 =====

@plugin_router.get("/")
async def list_plugins():
    """获取所有插件状态"""
    if not _plugin_manager:
        return {"plugins": []}
    return {"plugins": _plugin_manager.get_plugin_status()}


# ===== 插件市场 =====

@plugin_router.get("/marketplace")
async def get_plugin_marketplace():
    """
    获取插件市场列表

    返回预定义的推荐插件列表，用户可以从中选择安装。
    """
    marketplace = [
        {
            "name": "helloworld",
            "display_name": "Hello World",
            "author": "System",
            "desc": "示例插件，回复 Hello World",
            "version": "1.0.0",
            "repo": "https://github.com/example/astrbot_plugin_helloworld",
            "source": "System/builtin",
        },
        {
            "name": "weather",
            "display_name": "天气查询",
            "author": "System",
            "desc": "查询天气预报",
            "version": "1.0.0",
            "repo": "https://github.com/example/astrbot_plugin_weather",
            "source": "System/builtin",
        },
        {
            "name": "calculator",
            "display_name": "计算器",
            "author": "System",
            "desc": "简单的数学计算",
            "version": "1.0.0",
            "repo": "https://github.com/example/astrbot_plugin_calculator",
            "source": "System/builtin",
        },
        {
            "name": "datetime",
            "display_name": "日期时间",
            "author": "System",
            "desc": "获取当前日期和时间",
            "version": "1.0.0",
            "repo": "https://github.com/example/astrbot_plugin_datetime",
            "source": "System/builtin",
        },
        {
            "name": "help",
            "display_name": "帮助",
            "author": "System",
            "desc": "显示所有可用命令",
            "version": "1.0.0",
            "repo": "https://github.com/example/astrbot_plugin_help",
            "source": "System/builtin",
        },
        {
            "name": "system_info",
            "display_name": "系统信息",
            "author": "System",
            "desc": "获取系统运行状态信息",
            "version": "1.0.0",
            "repo": "https://github.com/example/astrbot_plugin_system_info",
            "source": "System/builtin",
        },
    ]
    return {"marketplace": marketplace, "count": len(marketplace)}


# ===== 插件安装/卸载 =====

@plugin_router.post("/install")
async def install_plugin(request: PluginInstallRequest):
    """
    安装插件

    支持从 GitHub 仓库 URL 安装：
    - `https://github.com/author/repo`
    - `author/repo`（简写）
    """
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")

    if not request.source.strip():
        raise HTTPException(status_code=400, detail="安装来源不能为空")

    result = await _plugin_manager.install_plugin(request.source.strip())
    if result.get("success"):
        return {"status": "success", "message": result["message"]}
    raise HTTPException(status_code=400, detail=result.get("message", "安装失败"))


@plugin_router.post("/{plugin_name}/uninstall")
async def uninstall_plugin(plugin_name: str):
    """卸载插件"""
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")

    result = await _plugin_manager.uninstall_plugin(plugin_name)
    if result.get("success"):
        return {"status": "success", "message": result["message"]}
    raise HTTPException(status_code=400, detail=result.get("message", "卸载失败"))


# ===== 插件详情 =====

@plugin_router.get("/{plugin_name}/detail")
async def get_plugin_detail(plugin_name: str):
    """获取插件详情（完整元数据、配置模式、Handler 列表）"""
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")

    detail = _plugin_manager.get_plugin_detail(plugin_name)
    if not detail:
        raise HTTPException(status_code=404, detail=f"插件 {plugin_name} 未找到")
    return detail


# ===== 插件生命周期 =====

@plugin_router.post("/{plugin_name}/enable")
async def enable_plugin(plugin_name: str):
    """启用插件"""
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")
    success = await _plugin_manager.enable(plugin_name)
    if success:
        return {"status": "success", "message": f"插件 {plugin_name} 已启用"}
    raise HTTPException(status_code=404, detail=f"插件 {plugin_name} 未找到")


@plugin_router.post("/{plugin_name}/disable")
async def disable_plugin(plugin_name: str):
    """禁用插件"""
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")
    success = await _plugin_manager.disable(plugin_name)
    if success:
        return {"status": "success", "message": f"插件 {plugin_name} 已禁用"}
    raise HTTPException(status_code=404, detail=f"插件 {plugin_name} 未找到")


@plugin_router.post("/{plugin_name}/reload")
async def reload_plugin(plugin_name: str):
    """重载插件"""
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")
    success, message = await _plugin_manager.reload(plugin_name)
    if success:
        return {"status": "success", "message": message}
    return {"status": "partial", "message": message}


@plugin_router.post("/reload-all")
async def reload_all_plugins():
    """重载所有插件"""
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")
    success, message = await _plugin_manager.reload()
    if success:
        return {"status": "success", "message": "所有插件已重载"}
    return {"status": "partial", "message": message}


# ===== 插件依赖管理 =====

@plugin_router.get("/{plugin_name}/deps")
async def get_plugin_deps(plugin_name: str):
    """查询插件依赖状态（已安装/缺失/进行中）"""
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")
    return await asyncio.to_thread(_plugin_manager.get_plugin_dependencies, plugin_name)


@plugin_router.post("/{plugin_name}/deps/install")
async def install_plugin_deps(plugin_name: str):
    """手动安装插件缺失依赖"""
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")
    result = await _plugin_manager.install_plugin_dependencies(plugin_name)
    if result.get("success"):
        return {"status": "success", "message": result.get("message", "依赖安装完成")}
    return {"status": "partial", "message": result.get("message", "依赖安装失败"), "detail": result.get("detail", [])}


# ===== 插件配置 =====

@plugin_router.get("/{plugin_name}/config")
async def get_plugin_config(plugin_name: str):
    """获取插件配置（含配置模式 schema）"""
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")
    config = await _plugin_manager.get_plugin_config(plugin_name)
    if config is None:
        config = {}
    # 获取配置模式 schema
    detail = _plugin_manager.get_plugin_detail(plugin_name)
    conf_schema = detail.get("conf_schema") if detail else None
    return {"config": config, "conf_schema": conf_schema}


@plugin_router.post("/{plugin_name}/config")
async def set_plugin_config(plugin_name: str, update: PluginConfigUpdate):
    """更新插件配置"""
    if not _plugin_manager:
        raise HTTPException(status_code=500, detail="插件管理器未初始化")
    success = await _plugin_manager.set_plugin_config(plugin_name, update.config)
    if success:
        return {"status": "success", "message": "插件配置已更新"}
    raise HTTPException(status_code=404, detail=f"插件 {plugin_name} 未找到")


# ══════════════════════════════════════════════
# 命令注册中心 API
# ══════════════════════════════════════════════

@plugin_router.get("/commands")
async def list_all_commands():
    """获取所有已注册的命令及冲突信息"""
    registry = get_command_registry()
    return {
        "success": True,
        "data": registry.get_command_summary(),
    }


@plugin_router.get("/commands/conflicts")
async def list_command_conflicts():
    """获取所有命令冲突"""
    registry = get_command_registry()
    return {
        "success": True,
        "data": registry.get_conflicts(),
    }


class ResolveConflictRequest(BaseModel):
    command: str
    winning_plugin: str


@plugin_router.post("/commands/conflicts/resolve")
async def resolve_command_conflict(request: ResolveConflictRequest):
    """解决命令冲突，指定哪个插件优先处理该命令"""
    registry = get_command_registry()
    success = registry.resolve_conflict(request.command, request.winning_plugin)
    if success:
        return {"success": True, "message": f"命令 '{request.command}' 已由插件 '{request.winning_plugin}' 处理"}
    return {"success": False, "message": "解决冲突失败，请检查命令名和插件名是否正确"}


@plugin_router.post("/commands/conflicts/reset")
async def reset_command_resolution(data: dict):
    """重置命令冲突解决，恢复自动选择"""
    command = data.get("command", "")
    if not command:
        return {"success": False, "message": "command 不能为空"}
    registry = get_command_registry()
    success = registry.reset_resolution(command)
    if success:
        return {"success": True, "message": f"命令 '{command}' 的冲突解决已重置"}
    return {"success": False, "message": "重置失败，该命令没有冲突解决记录"}


@plugin_router.get("/commands/by-plugin/{plugin_name}")
async def get_plugin_commands(plugin_name: str):
    """获取某个插件注册的所有命令"""
    registry = get_command_registry()
    regs = registry.get_commands_by_plugin(plugin_name)
    return {
        "success": True,
        "data": [
            {
                "command": r.command_name,
                "description": r.description,
                "priority": r.priority,
            }
            for r in regs
        ],
    }