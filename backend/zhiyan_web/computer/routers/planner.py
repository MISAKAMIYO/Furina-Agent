"""
任务规划 API 路由
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services.task_planner import get_task_planner, TaskPlanner, TaskPriority

logger = logging.getLogger("ZhiYanWeb.Computer.Planner")
router = APIRouter(prefix="/api/planner", tags=["planner"])

planner = get_task_planner()


class TaskItem(BaseModel):
    title: str
    description: str = ""
    depends_on: List[str] = []
    estimated_minutes: int = 0
    priority: int = 1
    max_retries: int = 0


class SubTaskItem(BaseModel):
    title: str
    description: str = ""
    depends_on: List[str] = []
    estimated_minutes: int = 5
    max_retries: int = 0


class CreatePlanRequest(BaseModel):
    goal: str
    tasks: List[TaskItem]


@router.post("/plan/create")
async def create_plan(request: CreatePlanRequest):
    """创建任务计划"""
    try:
        tasks = [t.model_dump() for t in request.tasks]
        plan = planner.create_plan(request.goal, tasks)
        return {"success": True, "data": plan.to_dict()}
    except Exception as e:
        logger.error(f"创建计划失败: {e}")
        return {"success": False, "error": str(e)}


@router.post("/plan/decompose")
async def auto_decompose(data: Dict):
    """自动分解任务（需要 LLM 调用）"""
    try:
        goal = data.get("goal", "")
        if not goal:
            return {"success": False, "error": "goal 不能为空"}

        # 返回分解提示模板，由前端/Agent 完成实际 LLM 调用
        prompt = f"""请将以下用户目标分解为可执行的子任务列表。

用户目标: {goal}

要求:
1. 将目标分解为 3-8 个具体的子任务
2. 每个子任务应该具体、可执行
3. 标注任务之间的依赖关系（使用 DAG）
4. 预估每个任务的执行时间（分钟）
5. 标注任务优先级（0=低, 1=普通, 2=高, 3=紧急）
6. 对复杂任务标注 max_retries（最大重试次数，默认 0）

请以 JSON 格式返回。"""

        return {
            "success": True,
            "data": {
                "prompt": prompt,
                "goal": goal,
                "message": "请使用 LLM 生成任务分解，或手动创建任务列表",
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/plan/list")
async def list_plans():
    """列出所有计划"""
    try:
        plans = planner.list_plans()
        return {"success": True, "data": plans}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/plan/{plan_id}")
async def get_plan(plan_id: str):
    """获取计划详情"""
    try:
        plan = planner.get_plan(plan_id)
        if not plan:
            return {"success": False, "error": "计划不存在"}
        return {"success": True, "data": plan.to_dict()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/plan/{plan_id}/progress")
async def get_progress(plan_id: str):
    """获取计划进度"""
    try:
        plan = planner.get_plan(plan_id)
        if not plan:
            return {"success": False, "error": "计划不存在"}
        return {
            "success": True,
            "data": {
                "progress": plan.get_progress(),
                "completed": plan.get_completed_count(),
                "failed": plan.get_failed_count(),
                "running": plan.get_running_count(),
                "pending": plan.get_pending_count(),
                "total": len(plan.tasks),
                "estimated_remaining_minutes": plan.get_estimated_remaining(),
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/plan/{plan_id}/cancel")
async def cancel_plan(plan_id: str):
    """取消计划"""
    try:
        success = planner.cancel_plan(plan_id)
        if not success:
            return {"success": False, "error": "计划不存在"}
        return {"success": True, "message": "计划已取消"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/plan/{plan_id}/pause")
async def pause_plan(plan_id: str):
    """暂停计划"""
    try:
        success = planner.pause_plan(plan_id)
        if not success:
            return {"success": False, "error": "计划不存在"}
        return {"success": True, "message": "计划已暂停"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/plan/{plan_id}/resume")
async def resume_plan(plan_id: str):
    """恢复计划"""
    try:
        success = planner.resume_plan(plan_id)
        if not success:
            return {"success": False, "error": "计划不存在"}
        return {"success": True, "message": "计划已恢复"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/plan/{plan_id}/split/{task_id}")
async def split_task(plan_id: str, task_id: str, subtasks: List[SubTaskItem]):
    """拆分任务"""
    try:
        sub_list = [s.model_dump() for s in subtasks]
        plan = planner.split_task(plan_id, task_id, sub_list)
        if not plan:
            return {"success": False, "error": "计划或任务不存在"}
        return {"success": True, "data": plan.to_dict()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/plan/{plan_id}")
async def delete_plan(plan_id: str):
    """删除计划"""
    try:
        success = planner.delete_plan(plan_id)
        if not success:
            return {"success": False, "error": "计划不存在"}
        return {"success": True, "message": "计划已删除"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/plan/{plan_id}/adjust")
async def adaptive_adjust(plan_id: str):
    """自适应调整计划"""
    try:
        plan = planner.adaptive_adjust(plan_id, None)
        if not plan:
            return {"success": False, "error": "计划不存在"}
        return {"success": True, "data": plan.to_dict()}
    except Exception as e:
        return {"success": False, "error": str(e)}