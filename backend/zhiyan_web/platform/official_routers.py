"""
QQ 开放平台机器人 API 路由
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from . import qq_official

router = APIRouter(prefix="/api/qq-official", tags=["qq-official"])

_ALLOWED_KEYS = {
    "enabled", "app_id", "app_secret", "target_type", "target_id", "api_base", "timeout",
    "ws_enabled", "intents", "command_enabled", "command_prefix",
    "command_allowed_groups", "command_allowed_users", "auto_bind_target",
}


@router.get("/status")
async def qq_official_status():
    """官方机器人配置与 token 状态"""
    return {"status": "success", "data": await qq_official.status()}


@router.get("/config")
async def qq_official_get_config():
    """读取配置（AppSecret 脱敏）"""
    return {"status": "success", "config": qq_official.get_config(mask=True)}


@router.post("/config")
async def qq_official_update_config(payload: Dict[str, Any]):
    """更新配置（只接受白名单字段）；涉及凭据时顺带校验一次 token"""
    patch = {k: v for k, v in (payload or {}).items() if k in _ALLOWED_KEYS}
    secret_saved = bool(str(patch.get("app_secret") or "").strip())
    config = qq_official.update_config(patch)
    result: Dict[str, Any] = {
        "status": "success",
        "message": "AppSecret 已加密保存" if secret_saved else "配置已保存",
        "secret_saved": secret_saved,
        "config": config,
    }
    if any(k in patch for k in ("app_id", "app_secret", "target_id", "target_type", "enabled")):
        validate = await qq_official.validate_config()
        result["validate"] = validate
        if not validate.get("ok"):
            prefix = "AppSecret 已加密保存，但" if secret_saved else "配置已保存，但"
            result["message"] = prefix + "校验未通过：" + (validate.get("message") or "未知原因")
    return result


@router.get("/gateway/status")
async def qq_official_gateway_status():
    """网关状态（配置 / token / WS 连接 / 消息计数）"""
    return {"status": "success", "data": await qq_official.gateway_status()}


@router.post("/gateway/restart")
async def qq_official_gateway_restart():
    """只重启 WS 接收，不动配置"""
    result = await qq_official.restart_gateway()
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}


@router.post("/validate")
async def qq_official_validate():
    """校验 AppID / AppSecret 能否换取 token"""
    result = await qq_official.validate_config()
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}


@router.post("/test")
async def qq_official_test(payload: Dict[str, Any] = None):
    """发送一条测试消息"""
    payload = payload or {}
    text = str(payload.get("text") or "BetterGI 托管测试消息")
    result = await qq_official.send_text(
        text,
        target_type=payload.get("target_type"),
        target_id=payload.get("target_id"),
        msg_id=str(payload.get("msg_id") or ""),
    )
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}


@router.post("/ws/start")
async def qq_official_ws_start():
    """启动 WebSocket 接收客户端"""
    result = await qq_official.get_client().start()
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": await qq_official.status()}


@router.post("/ws/stop")
async def qq_official_ws_stop():
    """停止 WebSocket 接收客户端"""
    result = await qq_official.get_client().stop()
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": await qq_official.status()}


@router.post("/auto-target")
async def qq_official_auto_target():
    """从最近一次入站消息自动获取 openid 并写入配置（无需手动填写目标 ID）"""
    data = qq_official.auto_detect_target(force=True)
    return {
        "status": "success" if data.get("ok") else "error",
        "message": data.get("message", ""),
        "data": data,
    }


@router.get("/messages")
async def qq_official_messages():
    """最近的入站消息（含缓存的 msg_id）"""
    return {"status": "success", "data": {"messages": qq_official.recent_messages(20)}}


@router.get("/commands")
async def qq_official_commands():
    """最近的命令执行记录"""
    client = qq_official.get_client()
    return {"status": "success", "data": {"commands": list(client.command_log)[::-1], "count": client.commands_handled}}
