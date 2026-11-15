"""扩展能力总览路由。

统一管理页的顶部总览条与类型徽标需要一次拿到五类扩展的数量/状态，
本路由把它们聚合为单个响应，避免前端并发打五组接口。

设计原则：每个子系统单独 try/except，任一子系统异常都不影响其它子系统
的数据返回（对应各自的执行管道互不影响）。
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter

logger = logging.getLogger("ZhiYanWeb.extensions")

router = APIRouter(prefix="/api/extensions", tags=["extensions"])


def _count_native_plugins() -> dict:
    """原生插件：走 star Handler 管道。"""
    try:
        from zhiyan_web.plugins.routers.plugins import get_plugin_manager
        mgr = get_plugin_manager()
        if mgr is None:
            return {"total": 0, "active": 0, "error": 0, "ready": False}
        items = mgr.get_plugin_status() or []
        # star 插件的启用态字段是 activated（失败插件另有 error 字段）
        active = sum(1 for p in items if p.get("activated"))
        err = sum(1 for p in items if p.get("error"))
        return {"total": len(items), "active": active, "error": err, "ready": True}
    except Exception as exc:
        logger.debug("[EXT] 汇总原生插件失败: %s", exc)
        return {"total": 0, "active": 0, "error": 0, "ready": False}


def _count_native_skills() -> dict:
    """原生技能：注册进 FunctionToolManager，作为 Agent 工具。"""
    try:
        from zhiyan_web.skills.skill_manager import get_skill_manager
        stats = get_skill_manager().get_stats() or {}
        return {
            "total": int(stats.get("total") or 0),
            "active": int(stats.get("active") or 0),
            "pending": int(stats.get("pending") or 0),
            "ready": True,
        }
    except Exception as exc:
        logger.debug("[EXT] 汇总原生技能失败: %s", exc)
        return {"total": 0, "active": 0, "pending": 0, "ready": False}


def _count_mcp() -> dict:
    """MCP 服务：独立事件循环线程，工具以 mcp__ 前缀注册。"""
    try:
        from zhiyan_web.mcp.manager import get_registry
        ov = get_registry().overview() or {}
        return {
            "total": int(ov.get("servers") or 0),
            "active": int(ov.get("active") or 0),
            "connected": int(ov.get("connected") or 0),
            "tools": int(ov.get("tools") or 0),
            "errors": list(ov.get("errors") or []),
            "ready": True,
        }
    except Exception as exc:
        logger.debug("[EXT] 汇总 MCP 失败: %s", exc)
        return {"total": 0, "active": 0, "connected": 0, "tools": 0,
                "errors": [], "ready": False}


def _builtin_rows() -> list:
    """DSH 内置 cordis 行（只读展示用）。"""
    from zhiyan_web.dsh.engine import list_builtin_plugins
    return list(list_builtin_plugins() or [])


def _count_dsh() -> dict:
    """DSH 内核：插件为 cordis 行，技能为内核内的 SKILL.md 目录。"""
    out = {"plugins": 0, "user_plugins": 0, "builtin_plugins": 0,
           "skills": 0, "running": False, "ready": False}
    try:
        from zhiyan_web.dsh.engine import (
            get_dsh_engine, get_user_dsh_plugins, DSH_CORE, DSH_RUNTIME,
        )
        user = get_user_dsh_plugins() or []
        out["user_plugins"] = len(user)
        out["builtin_plugins"] = len(
            [p for p in _builtin_rows() if p.get("enabled")])
        out["plugins"] = out["user_plugins"] + out["builtin_plugins"]
        out["running"] = bool(get_dsh_engine().alive)
        # DSH 技能目录与 dsh/routers.py 的 /skills 端点保持一致
        home = os.path.join(DSH_RUNTIME, "dsh_home")
        for d in (os.path.join(home, "skills"),
                  os.path.join(DSH_CORE, ".agents", "skills")):
            if os.path.isdir(d):
                out["skills"] += sum(
                    1 for n in os.listdir(d)
                    if os.path.isdir(os.path.join(d, n)))
        out["ready"] = True
    except Exception as exc:
        logger.debug("[EXT] 汇总 DSH 失败: %s", exc)
    return out


@router.get("/overview")
async def extensions_overview():
    """五类扩展的数量与状态总览（供统一管理页顶部展示）。"""
    plugins = _count_native_plugins()
    skills = _count_native_skills()
    mcp = _count_mcp()
    dsh = _count_dsh()
    return {
        "ok": True,
        "categories": {
            "plugin": plugins,
            "skill": skills,
            "mcp": mcp,
            "dsh_plugin": {
                "total": dsh.get("plugins", 0),
                "active": dsh.get("user_plugins", 0) + dsh.get("builtin_plugins", 0),
                "running": dsh.get("running", False),
            },
            "dsh_skill": {
                "total": dsh.get("skills", 0),
                "active": dsh.get("skills", 0),
                "running": dsh.get("running", False),
            },
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# 内置 agent 引擎的扩展面：子代理类型 / 工具分组与场景 / 钩子 / 声明式清单
# 这些不属于「五类扩展」，而是引擎自身的可扩展点，放在同一个总览接口下方便
# 扩展中心统一展示与配置。
# ═══════════════════════════════════════════════════════════════════════════

def _subagents_block() -> dict:
    try:
        from zhiyan_web.subagent.registry import describe_types, list_subagent_types
        from zhiyan_web.subagent.runner import scope_tools
        from zhiyan_web.subagent.types_store import custom_types
        items = describe_types()
        custom_names = {row.get("name") for row in custom_types()}
        # 顺带把每个类型实际能拿到的工具数量算出来，扩展中心可以直接展示
        # 「这个子代理有多少权限」，不用再单独调接口。
        counts = {}
        for t in list_subagent_types():
            try:
                counts[t.name] = len(scope_tools(t, depth=1))
            except Exception:
                counts[t.name] = 0
        for item in items:
            item["tool_count"] = counts.get(item["name"], 0)
            item["custom"] = item["name"] in custom_names
        return {"total": len(items), "types": items, "ready": True}
    except Exception as exc:
        logger.debug("[EXT] 汇总子代理类型失败: %s", exc)
        return {"total": 0, "types": [], "ready": False}


def _tool_scenarios_block() -> dict:
    try:
        from zhiyan_web.agent import tool_groups
        from zhiyan_web.config.settings import get_settings
        return {
            "current": tool_groups.get_default_scenario(),
            "scenarios": tool_groups.list_scenarios(),
            "groups": tool_groups.list_groups(),
            "group_count": len(tool_groups.list_groups()),
            "ready": True,
        }
    except Exception as exc:
        logger.debug("[EXT] 汇总工具场景失败: %s", exc)
        return {"current": "full", "scenarios": [], "groups": [],
                "group_count": 0, "ready": False}


def _hooks_block() -> dict:
    try:
        from zhiyan_web.agent.hook_bus import get_hook_bus
        bus = get_hook_bus()
        return {"total": bus.count(), "items": bus.describe(), "ready": True}
    except Exception as exc:
        logger.debug("[EXT] 汇总钩子失败: %s", exc)
        return {"total": 0, "items": [], "ready": False}


def _manifests_block() -> dict:
    try:
        from zhiyan_web.extensions.registry import summary
        data = summary()
        data["ready"] = True
        return data
    except Exception as exc:
        logger.debug("[EXT] 汇总扩展清单失败: %s", exc)
        return {"manifests": 0, "items": [], "ready": False}


@router.get("/agent")
async def agent_extension_surface():
    """内置 agent 引擎的可扩展点总览：子代理、工具场景、钩子、声明式清单。"""
    return {
        "ok": True,
        "subagent": _subagents_block(),
        "tools": _tool_scenarios_block(),
        "hooks": _hooks_block(),
        "manifests": _manifests_block(),
    }


@router.post("/manifests/reload")
async def reload_manifests():
    """重新扫描并加载扩展清单（新增/修改 extension.json 后无需重启）。"""
    try:
        import asyncio
        from zhiyan_web.extensions.registry import load_manifests
        result = await asyncio.to_thread(load_manifests, None, True)
        return {"ok": True, "result": result}
    except Exception as exc:
        logger.warning("[EXT] 重载扩展清单失败: %s", exc)
        return {"ok": False, "error": str(exc)}
