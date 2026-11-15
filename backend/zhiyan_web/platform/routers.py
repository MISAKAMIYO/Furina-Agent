"""
QQ 平台接入 —— FastAPI 路由
===========================
提供 QQ 平台的管理接口：
- GET  /api/qq/status          —— 运行状态 + presence 概览
- POST /api/qq/start           —— 启动
- POST /api/qq/stop            —— 停止
- POST /api/qq/restart         —— 重启
- GET  /api/qq/config          —— 读取配置（令牌脱敏）
- POST /api/qq/config          —— 更新配置并应用
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .manager import get_qq_platform_manager
from .permission import (
    get_perm_cfg,
    mutate_perm_list,
    user_level,
)

logger = logging.getLogger("ZhiYanWeb.QQPlatform")

router = APIRouter(prefix="/api/qq", tags=["qq-platform"])

# 允许更新的配置字段（白名单，避免覆盖安全相关键之外的任意内容）
_ALLOWED_CONFIG_KEYS = {
    "enabled", "ws_url", "access_token", "self_id", "wake_prefixes",
    "group_respond_mode", "max_message_chars", "call_timeout",
    "owner_only_llm", "plugin_use_permission",
    "min_send_interval", "max_send_per_minute",
    "greet_on_connect", "greet_text",
    # 权限与访问控制
    "master_id", "admins_id", "whitelist", "blacklist",
    "blocked_groups", "allowed_groups", "access_mode",
    "private_enabled", "group_enabled", "permission_reply",
}


class QQConfigUpdate(BaseModel):
    config: Dict[str, Any] = None


def _manager():
    return get_qq_platform_manager()


@router.get("/status")
async def qq_status():
    """QQ 平台运行状态"""
    return _manager().get_status()


@router.get("/config")
async def qq_get_config():
    """读取 QQ 平台配置（令牌已脱敏）"""
    return {"config": _manager().get_config()}


@router.post("/config")
async def qq_update_config(payload: QQConfigUpdate):
    """更新 QQ 平台配置并应用（仅允许白名单字段）"""
    if payload.config is None:
        raise HTTPException(status_code=400, detail="缺少 config 字段")

    update = {k: v for k, v in payload.config.items() if k in _ALLOWED_CONFIG_KEYS}

    # 令牌脱敏防护：若 access_token 为脱敏后的占位值（含 ****），说明用户未修改 token，
    # 忽略之，避免把脱敏串写回配置导致连接鉴权失败。
    if "access_token" in update and update["access_token"]:
        masked = update["access_token"]
        if "****" in masked:
            update.pop("access_token")
            logger.info("QQ 平台配置更新忽略未修改的脱敏 access_token")

    unknown = set(payload.config.keys()) - update.keys()
    unknown.discard("access_token")
    if unknown:
        # 非法的键静默忽略，避免覆盖其他配置
        logger.info(f"QQ 平台配置更新忽略了未知字段: {sorted(unknown)}")

    try:
        from zhiyan_web.config import update_settings
        update_settings({"qq_platform": update})
    except Exception as e:
        logger.error(f"保存 QQ 平台配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"保存配置失败: {e}")

    mgr = _manager()
    # 说明：不再依据 enabled 自动连接/断开 —— 由用户在界面点击「启动/停止」手动决定，
    # 这里仅当平台已运行时应用新配置（重连）。
    if mgr.get_status()["running"]:
        mgr.reconfigure()

    return {"success": True, "message": "配置已更新"}


@router.post("/start")
async def qq_start():
    """启动 QQ 平台"""
    result = await _manager().start()
    return result


@router.post("/stop")
async def qq_stop():
    """停止 QQ 平台"""
    result = await _manager().stop()
    return result


@router.post("/restart")
async def qq_restart():
    """重启 QQ 平台（应用最新配置）"""
    result = await _manager().restart()
    return result


@router.get("/permission")
async def qq_permission():
    """权限与管控概览：配置 + 好友/群列表（标记用户等级与群管控状态）"""
    mgr = _manager()
    perm = get_perm_cfg()
    friends = []
    for f in mgr.presence["friends"]:
        uid = str(f.get("user_id", ""))
        friends.append({
            "user_id": uid,
            "nickname": f.get("nickname", ""),
            "remark": f.get("remark", ""),
            "level": user_level(uid, perm),
            "blacklisted": uid in perm.get("blacklist", []),
        })
    groups = []
    for g in mgr.presence["groups"]:
        gid = str(g.get("group_id", ""))
        groups.append({
            "group_id": gid,
            "group_name": g.get("group_name", ""),
            "blocked": gid in perm.get("blocked_groups", []),
            "allowed": (not perm.get("allowed_groups")) or gid in perm.get("allowed_groups", []),
        })
    return {
        "config": {
            "master": perm.get("master_id", ""),
            "admins": perm.get("admins_id", []),
            "whitelist": perm.get("whitelist", []),
            "blacklist": perm.get("blacklist", []),
            "blocked_groups": perm.get("blocked_groups", []),
            "allowed_groups": perm.get("allowed_groups", []),
            "access_mode": perm.get("access_mode", "whitelist_at_only"),
            "private_enabled": perm.get("private_enabled", True),
            "group_enabled": perm.get("group_enabled", True),
            "permission_reply": perm.get("permission_reply", "ignore"),
        },
        "friends": friends,
        "groups": groups,
    }


class PermListUpdate(BaseModel):
    field: str
    action: str = "add"            # add / remove / clear
    item: Optional[str] = None


@router.post("/perm/list")
async def qq_perm_list(payload: PermListUpdate):
    """增删权限列表字段（whitelist / blacklist / admins_id / blocked_groups / allowed_groups）"""
    res = mutate_perm_list(payload.field, payload.item, payload.action)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("message", "操作失败"))
    return res


class PermImport(BaseModel):
    field: str
    items: List[str] = []


@router.post("/perm/import")
async def qq_perm_import(payload: PermImport):
    """批量导入权限列表（如从好友列表一键加入白名单）"""
    if payload.field not in ("whitelist", "blacklist", "admins_id", "blocked_groups", "allowed_groups"):
        raise HTTPException(status_code=400, detail="不支持的字段")
    from zhiyan_web.config import get_settings, update_settings
    settings = get_settings()
    qq = dict(settings.get("qq_platform") or {})
    lst = [str(x) for x in (qq.get(payload.field) or [])]
    for it in payload.items:
        it = str(it or "").strip()
        if it and it not in lst:
            lst.append(it)
    qq[payload.field] = lst
    update_settings({"qq_platform": qq})
    return {"success": True, "message": f"{payload.field} 已更新 ({len(lst)} 项)", "count": len(lst)}


@router.get("/presence")
async def qq_presence():
    """好友 / 群 presence 状态"""
    mgr = _manager()
    return {
        "friends": mgr.presence["friends"],
        "groups": mgr.presence["groups"],
        "online": bool(mgr.client and mgr.client.is_online),
    }


# ──────────────── 测试接口（验证媒体发送 / agent 触发链路） ────────────────


class QQTestMedia(BaseModel):
    message_type: str = "private"   # private / group
    target_id: str = ""
    media_type: str = "image"       # image / record / video / file
    file: str = ""
    text: str = ""                  # 可选的随附文本


class QQTestAgent(BaseModel):
    message_type: str = "private"   # private / group
    target_id: str = ""
    text: str = "你好"


def _make_test_event(mgr, payload) -> "QQMessageEvent":
    """构造用于测试的 QQMessageEvent（用 master 身份模拟，避免权限拦截）"""
    from .qq_event import QQMessageEvent, QQSegment
    ev = QQMessageEvent()
    ev.message_type = payload.message_type
    ev.group_id = str(payload.target_id) if payload.message_type == "group" else ""
    ev.user_id = str(mgr.get_status().get("permission", {}).get("master") or "228117384")
    ev.self_id = mgr.client.self_id if mgr.client else ""
    ev.raw_message = payload.text
    ev.segments = [QQSegment(type="text", data={"text": payload.text})]
    return ev


@router.post("/test/send-media")
async def qq_test_send_media(payload: QQTestMedia):
    """向指定会话发送媒体（image/record/video/file），验证与 NapCat 的联动"""
    mgr = _manager()
    if not mgr.client or not mgr.client.is_online:
        raise HTTPException(status_code=400, detail="QQ 平台未在线，无法发送媒体")
    if not payload.target_id:
        raise HTTPException(status_code=400, detail="缺少 target_id")
    if payload.media_type not in ("image", "record", "video", "file"):
        raise HTTPException(status_code=400, detail="media_type 仅支持 image/record/video/file")
    try:
        ev = _make_test_event(mgr, payload)
        resp = await mgr.send_media(ev, payload.media_type, payload.file, text=f"[测试] 发送 {payload.media_type}")
        ok = (resp or {}).get("status") in ("ok", "success")
        return {"success": ok, "response": resp, "sent_file": payload.file}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"媒体发送失败: {e}")


@router.post("/test/agent")
async def qq_test_agent(payload: QQTestAgent):
    """模拟 master 的 / 前缀消息，验证 agent 触发链路并真实发送回复到 QQ"""
    from .manager import _get_qq_platform_cfg
    mgr = _manager()
    if not mgr.client or not mgr.client.is_online:
        raise HTTPException(status_code=400, detail="QQ 平台未在线，无法发送")
    if not payload.target_id:
        raise HTTPException(status_code=400, detail="缺少 target_id")
    try:
        ev = _make_test_event(mgr, payload)
        cfg = _get_qq_platform_cfg()
        # 走完整处理链路（含随机延迟、媒体标记解析、限速），回复会真实发送到 QQ
        await mgr._process_and_reply(ev, payload.text.strip(), cfg)
        return {"success": True, "queued": True, "message": f"已模拟 master 消息并排队处理: {payload.text[:50]}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"agent 触发失败: {e}")