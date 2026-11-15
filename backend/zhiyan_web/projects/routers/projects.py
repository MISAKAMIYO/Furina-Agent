"""
项目（群聊）管理 API
- 创建/删除项目
- 切换当前项目
- 添加/移除 Agent 到项目
- 项目历史记录
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional
from fastapi import APIRouter

from zhiyan_web.projects.manager import get_project_manager

logger = logging.getLogger("ZhiYanWeb.projects.routers")

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("/list")
async def list_projects():
    """列出所有项目"""
    try:
        pm = get_project_manager()
        projects = pm.list()
        result = [
            {
                "project_id": p.project_id,
                "name": p.name,
                "description": p.description,
                "agents": p.agents,
                "created_at": p.created_at,
                "updated_at": p.updated_at,
                "is_active": p.is_active,
                "is_current": p.project_id == pm.current.project_id,
            }
            for p in projects
        ]
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"列出项目失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/current")
async def get_current_project():
    """获取当前项目"""
    try:
        pm = get_project_manager()
        current = pm.current
        history = pm.get_project_history(current.project_id)
        return {"success": True, "data": {
            "project": {
                "project_id": current.project_id,
                "name": current.name,
                "description": current.description,
                "agents": current.agents,
                "created_at": current.created_at,
                "updated_at": current.updated_at,
            },
            "history": history,
        }}
    except Exception as e:
        logger.error(f"获取当前项目失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/switch/{project_id}")
async def switch_project(project_id: str):
    """切换到指定项目"""
    try:
        pm = get_project_manager()
        success = pm.set_current(project_id)
        if not success:
            return {"success": False, "message": "项目不存在"}
        current = pm.current
        history = pm.get_project_history(current.project_id)
        return {"success": True, "data": {
            "project": {
                "project_id": current.project_id,
                "name": current.name,
            },
            "history": history,
        }, "message": f"已切换到 {current.name}"}
    except Exception as e:
        logger.error(f"切换项目失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/create")
async def create_project(data: Dict):
    """创建新项目"""
    try:
        pm = get_project_manager()
        name = data.get("name", "新建项目")
        description = data.get("description", "")
        agents = data.get("agents", ["coordinator", "zhiyan"])
        project = pm.create(name, description, agents)
        return {"success": True, "data": {
            "project_id": project.project_id,
            "name": project.name,
        }, "message": "项目创建成功"}
    except Exception as e:
        logger.error(f"创建项目失败: {e}")
        return {"success": False, "message": str(e)}


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """删除项目"""
    try:
        pm = get_project_manager()
        success = pm.delete(project_id)
        if success:
            return {"success": True, "message": "项目删除成功"}
        else:
            return {"success": False, "message": "删除失败（默认项目不可删除）"}
    except Exception as e:
        logger.error(f"删除项目失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/{project_id}/agents/add")
async def add_agent_to_project(project_id: str, data: Dict):
    """添加 Agent 到项目"""
    try:
        agent_id = data.get("agent_id", "")
        if not agent_id:
            return {"success": False, "message": "缺少 agent_id"}

        pm = get_project_manager()
        success = pm.add_agent(project_id, agent_id)
        if success:
            project = pm.get(project_id)
            return {"success": True, "data": {
                "agents": project.agents,
            }, "message": "Agent 添加成功"}
        else:
            return {"success": False, "message": "添加失败"}
    except Exception as e:
        logger.error(f"添加 Agent 失败: {e}")
        return {"success": False, "message": str(e)}


@router.delete("/{project_id}/agents/{agent_id}")
async def remove_agent_from_project(project_id: str, agent_id: str):
    """从项目移除 Agent"""
    try:
        pm = get_project_manager()
        success = pm.remove_agent(project_id, agent_id)
        if success:
            project = pm.get(project_id)
            return {"success": True, "data": {
                "agents": project.agents,
            }, "message": "Agent 移除成功"}
        else:
            return {"success": False, "message": "移除失败（coordinator 不可移除）"}
    except Exception as e:
        logger.error(f"移除 Agent 失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/{project_id}/history")
async def get_project_history(project_id: str):
    """获取项目对话历史"""
    try:
        pm = get_project_manager()
        history = pm.get_project_history(project_id)
        return {"success": True, "data": history}
    except Exception as e:
        logger.error(f"获取项目历史失败: {e}")
        return {"success": False, "message": str(e)}


# 导出 router 供 app.py 注册
project_router = router