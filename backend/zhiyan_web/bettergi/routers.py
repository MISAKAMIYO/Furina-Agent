"""
BetterGI 托管 API 路由
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .service import get_bettergi_manager

router = APIRouter(prefix="/api/bettergi", tags=["bettergi"])


@router.get("/status")
async def bettergi_status():
    """获取托管状态（进程、监控、任务、事件）"""
    return {"status": "success", "data": get_bettergi_manager().get_status()}


@router.get("/config")
async def bettergi_get_config():
    """获取 BetterGI 配置"""
    return {"status": "success", "config": get_bettergi_manager().get_config()}


@router.post("/config")
async def bettergi_update_config(payload: Dict[str, Any]):
    """保存 BetterGI 配置（部分字段更新即可）"""
    mgr = get_bettergi_manager()
    config = mgr.update_config(payload or {})

    if not config.get("enabled"):
        try:
            await mgr.stop_monitor()
        except Exception:
            pass

    # 定时任务开关联动
    try:
        if config.get("schedule_enabled"):
            await mgr.start_scheduler()
        else:
            await mgr.stop_scheduler()
    except Exception:
        pass

    # 开机自启状态与配置对齐
    try:
        desired = bool(config.get("auto_start_on_boot"))
        current = mgr.get_autostart().get("enabled")
        if desired and not current:
            mgr.set_autostart(True)
        elif not desired and current:
            mgr.set_autostart(False)
    except Exception:
        pass

    return {"status": "success", "message": "配置已保存", "config": config}


@router.post("/monitor")
async def bettergi_monitor(payload: Dict[str, Any]):
    """开始/停止监控：{"enabled": true|false}"""
    mgr = get_bettergi_manager()
    enabled = bool((payload or {}).get("enabled", True))
    result = await (mgr.start_monitor() if enabled else mgr.stop_monitor())
    return {
        "status": "success" if result.get("ok") else "error",
        "message": result.get("message", ""),
        "data": mgr.get_status(),
    }


@router.get("/detect")
async def bettergi_detect():
    """探测 BetterGI 安装路径"""
    return {"status": "success", "data": get_bettergi_manager().detect()}


@router.post("/launch")
async def bettergi_launch(payload: Dict[str, Any] = None):
    """启动 BetterGI

    body 可选：{"mode": "normal|onedragon|groups|task_progress",
               "config_name": "一条龙配置名", "group_names": ["组名1", "组名2"]}
    """
    payload = payload or {}
    result = get_bettergi_manager().launch(
        mode=payload.get("mode"),
        config_name=payload.get("config_name"),
        group_names=payload.get("group_names"),
    )
    return {
        "status": "success" if result.get("ok") else "error",
        "message": result.get("message", ""),
        "data": result,
    }


@router.post("/kill")
async def bettergi_kill():
    """结束 BetterGI 进程"""
    result = get_bettergi_manager().kill()
    return {
        "status": "success" if result.get("ok") else "error",
        "message": result.get("message", ""),
        "data": result,
    }


@router.post("/event")
async def bettergi_event(payload: Dict[str, Any]):
    """接收 BetterGI Webhook 通知（字段：event/result/message/data/screenshot）"""
    result = get_bettergi_manager().handle_event(payload or {})
    return {"status": "ok", "data": result}


@router.websocket("/ws")
async def bettergi_ws(websocket: WebSocket):
    """接收 BetterGI WebSocket 通知

    BetterGI 的 WebSocketNotifier 每推送一条事件就建立连接、发送一条 JSON、随后主动断开，
    因此这里按「接收即处理、断开即结束」的方式循环。
    """
    await websocket.accept()
    mgr = get_bettergi_manager()
    try:
        while True:
            raw = await websocket.receive_text()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    payload = {"event": "raw", "message": str(payload)[:200]}
            except Exception:
                payload = {"event": "raw", "message": raw[:200]}
            mgr.handle_event(payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/notify-config")
async def bettergi_notify_config():
    """读取 BetterGI 当前通知配置"""
    return {"status": "success", "data": get_bettergi_manager().get_notify_config()}


@router.post("/setup-notify")
async def bettergi_setup_notify(payload: Dict[str, Any]):
    """把 Webhook 通知写入 BetterGI 的 User/config.json

    body: {"base_url": "http://127.0.0.1:8001", "events": "可选事件列表", "dry_run": false}
    """
    payload = payload or {}
    result = get_bettergi_manager().configure_notifications(
        base_url=payload.get("base_url") or "",
        event_codes=payload.get("events"),
        transport=payload.get("transport") or "webhook",
        dry_run=bool(payload.get("dry_run")),
    )
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}


@router.post("/cancel-shutdown")
async def bettergi_cancel_shutdown():
    """取消待执行的自动关机"""
    result = get_bettergi_manager().cancel_shutdown()
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", "")}


@router.post("/clear-events")
async def bettergi_clear_events():
    """清空事件日志"""
    get_bettergi_manager().clear_events()
    return {"status": "success", "message": "事件日志已清空"}


@router.post("/image-recover")
async def bettergi_image_recover():
    """立即执行一次图像识别兜底"""
    result = await get_bettergi_manager().vision_recover_once()
    return {
        "status": "success" if result.get("success") else "error",
        "message": result.get("message", ""),
        "data": result,
    }


@router.get("/vision/status")
async def bettergi_vision_status():
    """OCR 引擎与图像兜底状态"""
    return {"status": "success", "data": get_bettergi_manager().vision_status()}


def _shot_data_url(image) -> str:
    """内存图像转 data URL（不落盘）"""
    try:
        from .vision import _to_data_url
        return _to_data_url(image)
    except Exception:
        return ""


@router.post("/vision/test")
async def bettergi_vision_test(payload: Dict[str, Any] = None):
    """截取游戏窗口（可选区域）并执行一次 OCR + 模板识别（不点击）"""
    from .vision import _screen_boxes, capture_window, load_rules, recognize_scene

    payload = payload or {}
    region = payload.get("region") or None
    mgr = get_bettergi_manager()
    shot = await asyncio.to_thread(capture_window, "", region, "game", False)
    if not shot.get("success"):
        return {"status": "error", "message": shot.get("message") or "截图失败",
                "data": {k: v for k, v in shot.items() if k != "image"}}
    rules = load_rules(mgr._config.get("vision_rules"))
    image = shot.get("image")
    try:
        scene = await asyncio.to_thread(recognize_scene, image, rules, None)
        shot_data = {k: v for k, v in shot.items() if k != "image"}
        shot_data["data_url"] = await asyncio.to_thread(_shot_data_url, image)
    finally:
        try:
            if image is not None:
                image.close()
        except Exception:
            pass
    items = (scene.get("items") or [])[:50]

    boxes = _screen_boxes(scene.get("items") or [], tuple(shot.get("offset") or (0, 0)))
    if boxes:
        await asyncio.to_thread(mgr.show_overlay, boxes)
    return {
        "status": "success" if scene.get("success") else "error",
        "message": scene.get("message") or "",
        "data": {
            "shot": shot_data,
            "engine": scene.get("engine"),
            "text": scene.get("text"),
            "boxes_drawn": len(boxes),
            "templates": scene.get("templates_matched") or [],
            "template_rules": scene.get("template_rules") or 0,
            "items": [
                {
                    "text": i.get("text"),
                    "score": i.get("score"),
                    "kind": i.get("kind"),
                    "cx": i.get("cx"),
                    "cy": i.get("cy"),
                }
                for i in items
            ],
        },
    }


@router.post("/vision/ensure")
async def bettergi_vision_ensure():
    """安装本地 OCR 引擎（源码运行环境）"""
    from .vision import ensure_engine

    result = await asyncio.to_thread(ensure_engine)
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", "")}


@router.get("/overlay/status")
async def bettergi_overlay_status():
    """红框叠加层状态"""
    from .overlay import overlay_status

    return {"status": "success", "data": overlay_status()}


@router.post("/overlay/preview")
async def bettergi_overlay_preview():
    """画三个示例红框，验证叠加层"""
    result = await asyncio.to_thread(get_bettergi_manager().overlay_preview)
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}


@router.post("/overlay/reset")
async def bettergi_overlay_reset():
    """关闭叠加层窗口"""
    from .overlay import reset_overlay

    return {"status": "success", "message": "叠加层已关闭", "data": reset_overlay()}


@router.get("/health/status")
async def bettergi_health_status():
    """健康检查配置与最近一次结果"""
    return {"status": "success", "data": get_bettergi_manager().health_status()}


@router.post("/health/check")
async def bettergi_health_check():
    """执行一次健康检查（只读，不触发自愈）"""
    summary = await get_bettergi_manager().health_check()
    return {"status": "success" if summary.get("ok") else "error", "message": "检查完成", "data": summary}


@router.post("/health/recover")
async def bettergi_health_recover(payload: Dict[str, Any] = None):
    """手动触发健康自愈链（body 可选：{"reason": "..."}）"""
    reason = str((payload or {}).get("reason") or "手动触发")
    result = await get_bettergi_manager().run_health_chain(reason)
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}


@router.get("/update/flows")
async def bettergi_update_flows():
    """更新流程定义与最近一次执行结果"""
    return {"status": "success", "data": get_bettergi_manager().update_flow_status()}


@router.post("/update/flow")
async def bettergi_run_update_flow(payload: Dict[str, Any] = None):
    """执行一条更新流程：{"target": "game|bgi"}"""
    target = str((payload or {}).get("target") or "game").strip().lower()
    result = await get_bettergi_manager().run_update(target)
    return {
        "status": "success" if result.get("success") else "error",
        "message": result.get("message", ""),
        "data": result,
    }


@router.get("/boot-plan/status")
async def bettergi_boot_plan_status():
    """开机任务计划状态"""
    return {"status": "success", "data": get_bettergi_manager().boot_plan_status()}


@router.post("/boot-plan/run")
async def bettergi_boot_plan_run():
    """立即按开机计划执行一次（用于调试）"""
    result = await get_bettergi_manager().boot_task_plan_startup()
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}


@router.get("/errors/status")
async def bettergi_errors_status():
    """异常判定状态（模式、日计数、动作映射）"""
    return {"status": "success", "data": get_bettergi_manager().error_status()}


@router.post("/errors/reset")
async def bettergi_errors_reset():
    """清空当日错误计数与连续无响应计数"""
    return {"status": "success", "message": "异常计数已重置", "data": get_bettergi_manager().reset_error_counters()}


@router.get("/relaunch/status")
async def bettergi_relaunch_status():
    """自动续跑状态"""
    return {"status": "success", "data": get_bettergi_manager().relaunch_status()}


@router.post("/relaunch/cancel")
async def bettergi_relaunch_cancel():
    """取消待执行的自动续跑"""
    result = get_bettergi_manager().cancel_relaunch("页面手动取消")
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", "")}


@router.get("/schedule/status")
async def bettergi_schedule_status():
    """定时任务状态"""
    return {"status": "success", "data": get_bettergi_manager().schedule_status()}


@router.post("/schedule/toggle")
async def bettergi_schedule_toggle(payload: Dict[str, Any]):
    """启停定时任务调度"""
    mgr = get_bettergi_manager()
    enabled = bool((payload or {}).get("enabled", True))
    # 先写回配置，保证重启后仍生效
    mgr.update_config({"schedule_enabled": enabled})
    result = await (mgr.start_scheduler() if enabled else mgr.stop_scheduler())
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", "")}


@router.get("/autostart")
async def bettergi_autostart_status():
    """查询开机自启状态"""
    return {"status": "success", "data": get_bettergi_manager().get_autostart()}


@router.get("/update/status")
async def bettergi_update_status():
    """更新维护信息：日志、配置路径、备份列表"""
    return {"status": "success", "data": get_bettergi_manager().update_status()}


@router.post("/update/backup")
async def bettergi_update_backup():
    """备份 BetterGI 的 User 配置"""
    result = await asyncio.to_thread(get_bettergi_manager().update_backup)
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}


@router.post("/update/restore")
async def bettergi_update_restore(payload: Dict[str, Any] = None):
    """恢复备份（mode=config 仅配置 / full 全部）"""
    payload = payload or {}
    result = await asyncio.to_thread(
        get_bettergi_manager().update_restore,
        payload.get("backup") or "",
        payload.get("mode") or "config",
    )
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}


@router.post("/update/restart")
async def bettergi_update_restart():
    """重启 BetterGI（普通模式）"""
    result = await get_bettergi_manager().restart_bgi()
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}


@router.post("/autostart")
async def bettergi_autostart_set(payload: Dict[str, Any]):
    """开关开机自启"""
    mgr = get_bettergi_manager()
    enabled = bool((payload or {}).get("enabled", True))
    result = mgr.set_autostart(enabled)
    if result.get("ok"):
        mgr.update_config({"auto_start_on_boot": enabled})
    return {"status": "success" if result.get("ok") else "error", "message": result.get("message", ""), "data": result}
