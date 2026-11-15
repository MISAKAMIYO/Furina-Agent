"""
Agent 管理 API
- 列出所有 Agent
- 获取 Agent 详情
- 创建/更新/删除 Agent
"""
from __future__ import annotations

import logging
from typing import Dict
from fastapi import APIRouter

from zhiyan_web.agents.registry import get_registry, AgentConfig

logger = logging.getLogger("ZhiYanWeb.agents.routers")

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/list")
async def list_agents():
    """列出所有 Agent"""
    try:
        registry = get_registry()
        agents = registry.list()
        result = [
            {
                "agent_id": a.agent_id,
                "name": a.name,
                "role": a.role,
                "avatar": a.avatar,
                "system_prompt": a.system_prompt,
                "provider": a.provider,
                "model": a.model,
                "temperature": a.temperature,
                "max_tokens": a.max_tokens,
                "enabled": a.enabled,
                "created_at": a.created_at,
                "updated_at": a.updated_at,
            }
            for a in agents
        ]
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"列出 Agent 失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """获取 Agent 详情"""
    try:
        registry = get_registry()
        agent = registry.get(agent_id)
        if not agent:
            return {"success": False, "message": "Agent 不存在"}
        return {"success": True, "data": {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "role": agent.role,
            "avatar": agent.avatar,
            "system_prompt": agent.system_prompt,
            "provider": agent.provider,
            "model": agent.model,
            "api_key": agent.api_key,
            "api_url": agent.api_url,
            "temperature": agent.temperature,
            "max_tokens": agent.max_tokens,
            "enabled": agent.enabled,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
        }}
    except Exception as e:
        logger.error(f"获取 Agent 详情失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/create")
async def create_agent(data: Dict):
    """创建新 Agent"""
    try:
        registry = get_registry()
        agent_id = data.get("agent_id", "")
        if not agent_id:
            import uuid
            agent_id = f"custom_{uuid.uuid4().hex[:6]}"

        agent = AgentConfig(
            agent_id=agent_id,
            name=data.get("name", agent_id),
            role=data.get("role", "worker"),
            avatar=data.get("avatar", "🤖"),
            system_prompt=data.get("system_prompt", ""),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            api_key=data.get("api_key", ""),
            api_url=data.get("api_url", ""),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 2048),
        )
        success = registry.add(agent)
        if success:
            return {"success": True, "data": agent_id, "message": "Agent 创建成功"}
        else:
            return {"success": False, "message": "创建失败"}
    except Exception as e:
        logger.error(f"创建 Agent 失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/{agent_id}")
async def update_agent(agent_id: str, data: Dict):
    """更新 Agent"""
    try:
        registry = get_registry()
        existing = registry.get(agent_id)
        if not existing:
            return {"success": False, "message": "Agent 不存在"}

        # 更新字段
        if "name" in data:
            existing.name = data["name"]
        if "role" in data:
            existing.role = data["role"]
        if "avatar" in data:
            existing.avatar = data["avatar"]
        if "system_prompt" in data:
            existing.system_prompt = data["system_prompt"]
        if "provider" in data:
            existing.provider = data["provider"]
        if "model" in data:
            existing.model = data["model"]
        if "api_key" in data:
            existing.api_key = data["api_key"]
        if "api_url" in data:
            existing.api_url = data["api_url"]
        if "temperature" in data:
            existing.temperature = float(data["temperature"])
        if "max_tokens" in data:
            existing.max_tokens = int(data["max_tokens"])
        if "enabled" in data:
            existing.enabled = bool(data["enabled"])

        success = registry.add(existing)
        if success:
            return {"success": True, "message": "Agent 更新成功"}
        else:
            return {"success": False, "message": "更新失败"}
    except Exception as e:
        logger.error(f"更新 Agent 失败: {e}")
        return {"success": False, "message": str(e)}


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """删除 Agent"""
    try:
        registry = get_registry()
        success = registry.remove(agent_id)
        if success:
            return {"success": True, "message": "Agent 删除成功"}
        else:
            return {"success": False, "message": "删除失败（内置 Agent 不可删除）"}
    except Exception as e:
        logger.error(f"删除 Agent 失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/list/role/{role}")
async def list_by_role(role: str):
    """按角色列出 Agent"""
    try:
        registry = get_registry()
        agents = registry.list(role=role)
        result = [
            {
                "agent_id": a.agent_id,
                "name": a.name,
                "avatar": a.avatar,
                "role": a.role,
            }
            for a in agents
        ]
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"按角色列出 Agent 失败: {e}")
        return {"success": False, "message": str(e)}


# 导出 router 供 app.py 注册
agent_router = router