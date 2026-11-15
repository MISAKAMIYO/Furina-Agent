"""Skills API 路由 - 技能管理（市场 / 控制 / 安全 / 日志）"""
from __future__ import annotations

import json
import logging
from typing import Optional, Dict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .skill_manager import get_skill_manager
from .security import level_color, level_label

logger = logging.getLogger("ZhiYanWeb.Skills.Router")
router = APIRouter(prefix="/api/skills", tags=["skills"])


# ──── 模型 ────

class ConfigBody(BaseModel):
    config: Dict = None


class ReviewBody(BaseModel):
    status: str = None


class WhitelistBody(BaseModel):
    whitelist: bool = True
    reason: str = ""


class ExecuteBody(BaseModel):
    params: Dict = None


class InstallMarketBody(BaseModel):
    source: str = "openclaw"     # openclaw / takoapi
    skill_id: str = ""
    force: bool = False


class InstallContentBody(BaseModel):
    name: str = ""
    content: str = ""
    meta: Dict = None


# ──── 基础查询（固定路径必须先于路由参数） ────

@router.get("/list")
async def list_skills():
    """获取所有已安装技能（含状态/安全/资源）"""
    sm = get_skill_manager()
    return {"success": True, "data": sm.get_skill_list()}


@router.get("/stats")
async def skill_stats():
    """技能系统统计"""
    sm = get_skill_manager()
    return {"success": True, "data": sm.get_stats()}


@router.get("/meta")
async def skill_meta():
    """元信息：社区首用确认状态、白名单"""
    sm = get_skill_manager()
    return {"success": True, "data": sm.get_meta_status(), "consent_required": not sm.community_consent}


@router.get("/logs")
async def skill_logs(limit: int = Query(200), skill: str = Query("")):
    """技能日志（安装/更新/使用/安全事件）"""
    sm = get_skill_manager()
    return {"success": True, "data": sm.get_logs(limit=limit, skill=skill)}


@router.get("/security/levels")
async def security_levels():
    """安全等级颜色/名称对照"""
    return {"success": True, "data": {
        lvl: {"color": level_color(lvl), "label": level_label(lvl)}
        for lvl in ("A", "B", "C", "D")
    }}


# ──── 社区（openclawskills）集成 ────

@router.post("/community/consent")
async def community_consent(body: dict):
    """首次使用社区的安全风险确认"""
    sm = get_skill_manager()
    value = bool(body.get("confirm", True))
    sm.set_community_consent(value)
    return {"success": True, "consent_required": not sm.community_consent}


@router.get("/community/search")
async def community_search(q: str = Query(""), source: str = Query("openclaw")):
    """检索 openclawskills 社区技能"""
    from .marketplace import OpenClawSkillsClient
    sm = get_skill_manager()
    client = OpenClawSkillsClient()
    results = []
    if source in ("openclaw", "all"):
        results.extend(await client.search_openclaw(q))
    if source == "takoapi" or (source == "all" and not results):
        results.extend(await client.search_takoapi(q))
    # openclaw 源失效（FRP 隧道离线）时自动回退 takoapi，避免默认源检索为空
    if source == "openclaw" and not results:
        results.extend(await client.search_takoapi(q))
    return {
        "success": True,
        "consent_required": not sm.community_consent,
        "data": results,
    }


@router.get("/community/preview")
async def community_preview(skill_id: str = Query(...), source: str = Query("openclaw")):
    """预览社区技能内容（不安装）"""
    from .marketplace import OpenClawSkillsClient, parse_skill_md
    sm = get_skill_manager()
    client = OpenClawSkillsClient()
    content = None
    if source == "openclaw":
        content = await client.download_openclaw(skill_id)
        if not content:
            # openclaw 源失效时回退 takoapi（按 slug 拉取 GitHub SKILL.md）
            content = await client.download_takoapi(skill_id)
    else:
        content = await client.download_takoapi(skill_id)
    if not content:
        return {"success": False, "message": "无法获取该技能内容（市场可能暂时不可用）"}
    meta = parse_skill_md(content)
    return {
        "success": True,
        "consent_required": not sm.community_consent,
        "data": {
            "name": meta["name"], "description": meta["description"],
            "version": meta["version"], "author": meta["author"],
            "tags": meta["tags"],
            "body_preview": meta["body"][:2000],
            "size": len(content),
        },
    }


@router.post("/community/install")
async def community_install(body: InstallMarketBody):
    """从社区安装技能（需已完成首用安全确认）"""
    from .marketplace import OpenClawSkillsClient, parse_skill_md
    sm = get_skill_manager()
    if not sm.community_consent:
        return {
            "success": False,
            "code": "CONSENT_REQUIRED",
            "message": "首次使用社区技能前请先阅读并确认安全风险提示",
        }
    if not body.skill_id:
        return {"success": False, "message": "缺少技能 ID"}

    client = OpenClawSkillsClient()
    if body.source == "openclaw":
        content = await client.download_openclaw(body.skill_id)
        if not content:
            content = await client.download_takoapi(body.skill_id)
    else:
        content = await client.download_takoapi(body.skill_id)
    if not content:
        return {"success": False, "message": "下载失败：无法获取该技能内容"}

    meta = parse_skill_md(content)
    name = meta["name"]
    # 指令型技能始终以 SKILL.md 安装
    result = sm.install_prompt_skill(
        name=name, content=content, meta=meta,
        installed_from=body.source, source_ref=str(body.skill_id),
    )
    return result


# ──── 操作 ────

@router.post("/reload")
async def reload_skills():
    """重新扫描并加载所有技能（自动注册到工具系统）"""
    sm = get_skill_manager()
    result = await sm.load_all()
    return {"success": True, "data": result}


@router.post("/{skill_name}/execute")
async def execute_skill(skill_name: str, body: ExecuteBody = None):
    """手动执行技能（测试）"""
    sm = get_skill_manager()
    params = (body.params if body else None) or {}
    result = await sm.execute_skill(skill_name, **params)
    return {"success": True, "skill": skill_name, "result": result}


@router.post("/{skill_name}/enable")
async def enable_skill(skill_name: str):
    return get_skill_manager().set_enabled(skill_name, True)


@router.post("/{skill_name}/disable")
async def disable_skill(skill_name: str):
    return get_skill_manager().set_enabled(skill_name, False)


@router.post("/{skill_name}/priority")
async def set_priority(skill_name: str, body: dict):
    return get_skill_manager().set_priority(skill_name, int(body.get("priority", 0) or 0))


@router.post("/{skill_name}/config")
async def set_config(skill_name: str, body: ConfigBody):
    return get_skill_manager().set_config(skill_name, body.config or {})


@router.post("/{skill_name}/review")
async def set_review(skill_name: str, body: ReviewBody):
    return get_skill_manager().set_review_status(skill_name, body.status or "approved")


@router.post("/{skill_name}/whitelist")
async def set_whitelist(skill_name: str, body: WhitelistBody):
    return get_skill_manager().set_whitelist(skill_name, bool(body.whitelist), body.reason)


@router.post("/{skill_name}/rescan")
async def rescan_skill(skill_name: str):
    return get_skill_manager().rescan(skill_name)


@router.post("/{skill_name}/uninstall")
async def uninstall_skill(skill_name: str):
    return get_skill_manager().uninstall(skill_name)


# ──── 详情与工具导出 ────

@router.get("/{skill_name}")
async def get_skill_info(skill_name: str):
    """获取技能详情"""
    sm = get_skill_manager()
    skill = sm.get_skill(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 '{skill_name}' 不存在")
    return {"success": True, "data": skill.to_dict()}


@router.get("/tools/openai")
async def get_openai_tools():
    """获取 OpenAI 格式的活跃技能工具列表"""
    sm = get_skill_manager()
    return {"success": True, "data": sm.to_openai_tools()}