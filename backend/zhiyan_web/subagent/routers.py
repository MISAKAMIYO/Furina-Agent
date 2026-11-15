"""
子代理管理接口

挂在 /api/subagent/* 下，供扩展中心的「引擎扩展」面板使用：

- 类型管理：列出 / 新建 / 更新 / 删除自定义子代理类型（内置类型只读，可被同名覆盖）
- 运行记录：历史运行（轮次 / 工具 / token / 耗时）+ 当前正在跑的
- 取消：中断一个正在跑的子代理
- 后台任务：查看与取消
- 编辑辅助：可选工具场景与分组清单（类型编辑器要用）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("ZhiYanWeb.SubAgent.Routers")

router = APIRouter(prefix="/api/subagent", tags=["subagent"])


class SubAgentTypePayload(BaseModel):
    name: str
    label: str = ""
    avatar: str = "🧩"
    description: str = ""
    system_prompt: str = ""
    scenario: str = "chat"
    tool_groups: Optional[List[str]] = None
    allow_tools: Optional[List[str]] = None
    deny_tools: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    max_iterations: int = 24
    temperature: float = 0.4
    max_depth: int = 2


def _types_block() -> Dict[str, Any]:
    from .registry import describe_types
    from .types_store import custom_types, get_custom
    items = describe_types()
    custom_names = {row.get("name") for row in custom_types()}
    for item in items:
        item["custom"] = item["name"] in custom_names
        if item["custom"]:
            row = get_custom(item["name"]) or {}
            item["editable"] = True
            # 编辑器需要拿到当前生效的完整定义（含 system_prompt）
            for key in ("label", "avatar", "description", "system_prompt", "scenario",
                        "tool_groups", "allow_tools", "deny_tools", "skills",
                        "max_iterations", "temperature", "max_depth"):
                if key in row:
                    item[key if key != "label" else "label"] = row[key]
        else:
            item["editable"] = False
    return {"total": len(items), "types": items}


@router.get("/types")
async def list_types():
    """列出全部子代理类型（内置 + 自定义）。"""
    try:
        return {"ok": True, **_types_block()}
    except Exception as e:
        logger.warning("[SUBAGENT] 列出类型失败: %s", e)
        return {"ok": False, "error": str(e), "total": 0, "types": []}


@router.post("/types")
async def save_type(payload: SubAgentTypePayload):
    """新建或更新一个自定义子代理类型（同名会覆盖内置）。"""
    try:
        from .types_store import save_type as _save
        data = payload.model_dump()
        result = await asyncio.to_thread(_save, data)
        if result.get("ok"):
            result["types"] = _types_block()["types"]
        return result
    except Exception as e:
        logger.warning("[SUBAGENT] 保存类型失败: %s", e)
        return {"ok": False, "error": str(e)}


@router.delete("/types/{name}")
async def delete_type(name: str):
    """删除一个自定义子代理类型（内置类型无法删除）。"""
    try:
        from .types_store import delete_type as _delete
        result = await asyncio.to_thread(_delete, name)
        if result.get("ok"):
            result["types"] = _types_block()["types"]
        return result
    except Exception as e:
        logger.warning("[SUBAGENT] 删除类型失败: %s", e)
        return {"ok": False, "error": str(e)}


@router.get("/scenarios")
async def list_scenarios():
    """可选工具场景与工具分组（类型编辑器下拉用）。"""
    try:
        from zhiyan_web.agent import tool_groups
        return {
            "ok": True,
            "scenarios": tool_groups.list_scenarios(),
            "groups": tool_groups.list_groups(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "scenarios": [], "groups": []}


@router.get("/runs")
async def list_runs(limit: int = 50):
    """历史运行记录（最新的在前）与汇总统计。"""
    try:
        from .runtime import list_runs as _list, runs_stats
        return {"ok": True, "stats": runs_stats(), "runs": _list(limit)}
    except Exception as e:
        logger.warning("[SUBAGENT] 读取运行记录失败: %s", e)
        return {"ok": False, "error": str(e), "runs": [], "stats": {}}


@router.get("/running")
async def list_running():
    """当前正在运行（未结束）的子代理。"""
    try:
        from .runtime import list_running as _list
        return {"ok": True, "running": _list()}
    except Exception as e:
        return {"ok": False, "error": str(e), "running": []}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    """请求取消一个正在运行的子代理（软取消，在下一个检查点退出）。"""
    try:
        from .runtime import get_job, list_jobs, request_cancel
        from .runtime import finish_job
        ok = request_cancel(run_id)
        if not ok:
            return {"ok": False, "error": "该运行已经结束或不存在"}
        # 如果这个运行属于某个后台任务，同步把任务标成取消
        for job in list_jobs(limit=50):
            if job.get("run_id") == run_id and job.get("status") == "running":
                finish_job(job["id"], ok=False, error="已被用户取消", status="cancelled")
        return {"ok": True, "run_id": run_id}
    except Exception as e:
        logger.warning("[SUBAGENT] 取消失败: %s", e)
        return {"ok": False, "error": str(e)}


@router.get("/jobs")
async def list_jobs(limit: int = 20):
    """后台子代理任务列表。"""
    try:
        from .runtime import jobs_stats, list_jobs as _list
        return {"ok": True, "stats": jobs_stats(), "jobs": _list(limit)}
    except Exception as e:
        return {"ok": False, "error": str(e), "jobs": [], "stats": {}}


@router.get("/board")
async def board_snapshot():
    """当前回合共享白板的内容（回合结束后为空）。"""
    try:
        from .board import current_board
        board = current_board()
        if board is None:
            return {"ok": True, "entries": [], "active": False}
        return {"ok": True, "entries": board.snapshot(), "active": True,
                "turn_id": board.turn_id}
    except Exception as e:
        return {"ok": False, "error": str(e), "entries": []}


@router.get("/status")
async def subagent_status():
    """子代理整体状态（供扩展中心与设置页展示）。"""
    try:
        from .registry import list_subagent_types
        from .runtime import jobs_stats, runs_stats
        from .types_store import custom_types
        return {
            "ok": True,
            "types": len(list_subagent_types()),
            "custom_types": len(custom_types()),
            "runs": runs_stats(),
            "jobs": jobs_stats(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
