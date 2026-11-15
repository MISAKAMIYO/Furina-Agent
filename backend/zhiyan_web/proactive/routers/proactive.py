"""
主动交互 API 路由
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("ZhiYanWeb.proactive")

proactive_router = APIRouter(prefix="/api/proactive", tags=["proactive"])

# 全局实例引用
_proactive_engine = None
_scheduler_engine = None


def set_proactive_engine(engine):
    global _proactive_engine
    _proactive_engine = engine


def set_scheduler_engine(engine):
    global _scheduler_engine
    _scheduler_engine = engine


class TaskCreate(BaseModel):
    name: str
    task_type: str  # "cron" / "once"
    message: str
    cron_expr: str = ""
    run_at: str = ""


class TaskUpdate(BaseModel):
    enabled: Optional[bool] = None


# ===== 主动交互 API =====

@proactive_router.get("/status")
async def get_proactive_status():
    """获取主动交互引擎状态"""
    if not _proactive_engine:
        return {"enabled": False, "error": "引擎未初始化"}
    return _proactive_engine.get_status()


@proactive_router.post("/toggle")
async def toggle_proactive(data: dict):
    """启用/禁用主动交互"""
    if not _proactive_engine:
        raise HTTPException(status_code=500, detail="引擎未初始化")
    _proactive_engine.enabled = data.get("enabled", True)
    _proactive_engine._save_state()
    return {"status": "success", "enabled": _proactive_engine.enabled}


@proactive_router.post("/record-interaction")
async def record_interaction():
    """记录交互时间"""
    if not _proactive_engine:
        raise HTTPException(status_code=500, detail="引擎未初始化")
    _proactive_engine.record_interaction()
    return {"status": "success"}


@proactive_router.post("/generate")
async def generate_message():
    """手动生成一条主动消息"""
    if not _proactive_engine:
        raise HTTPException(status_code=500, detail="引擎未初始化")
    message = _proactive_engine.generate_message()
    return {"message": message}


# ===== 定时任务 API =====

@proactive_router.get("/tasks")
async def list_tasks(enabled_only: bool = False):
    """列出所有定时任务"""
    if not _scheduler_engine:
        return {"tasks": []}
    tasks = _scheduler_engine.list_tasks(enabled_only=enabled_only)
    return {"tasks": [t.to_dict() for t in tasks]}


@proactive_router.post("/tasks")
async def create_task(task: TaskCreate):
    """创建定时任务"""
    if not _scheduler_engine:
        raise HTTPException(status_code=500, detail="调度引擎未初始化")
    if task.task_type not in ("cron", "once"):
        raise HTTPException(status_code=400, detail="任务类型必须是 cron 或 once")
    if task.task_type == "cron" and not task.cron_expr:
        raise HTTPException(status_code=400, detail="Cron 类型任务需要提供 cron_expr")
    if task.task_type == "once" and not task.run_at:
        raise HTTPException(status_code=400, detail="Once 类型任务需要提供 run_at")
    new_task = _scheduler_engine.create_task(
        name=task.name, task_type=task.task_type,
        message=task.message, cron_expr=task.cron_expr, run_at=task.run_at,
    )
    return {"status": "success", "task": new_task.to_dict()}


@proactive_router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """删除定时任务"""
    if not _scheduler_engine:
        raise HTTPException(status_code=500, detail="调度引擎未初始化")
    if _scheduler_engine.delete_task(task_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="任务不存在")


@proactive_router.patch("/tasks/{task_id}")
async def update_task(task_id: str, update: TaskUpdate):
    """更新任务（启用/禁用）"""
    if not _scheduler_engine:
        raise HTTPException(status_code=500, detail="调度引擎未初始化")
    if update.enabled is not None:
        if _scheduler_engine.enable_task(task_id, update.enabled):
            return {"status": "success"}
    raise HTTPException(status_code=404, detail="任务不存在")