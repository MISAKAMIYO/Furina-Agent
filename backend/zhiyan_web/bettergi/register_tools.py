"""
BetterGI Agent 工具注册模块

把 BetterGI 托管能力注册到 FunctionToolManager，使 Agent / MCP 工具调用链可以直接：
- 查询状态
- 按模式启动（一条龙 / 配置组 / 任务进度）
- 结束进程
- 写入通知配置
- 取消自动关机

在 app.py 初始化时调用 register_bettergi_tools()。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("ZhiYanWeb.BetterGI.Register")


def register_bettergi_tools() -> None:
    """将 BetterGI 托管能力注册为 Agent 工具"""
    try:
        from zhiyan_web.core import func_tool_manager
    except (ImportError, AttributeError):
        logger.warning("FunctionToolManager 不可用，跳过 BetterGI 工具注册")
        return

    if func_tool_manager is None:
        logger.warning("func_tool_manager 为 None，跳过 BetterGI 工具注册")
        return

    from zhiyan_web.bettergi.service import get_bettergi_manager

    count = 0

    def _register(name: str, params: list, desc: str, handler) -> None:
        nonlocal count
        func_tool_manager.add_func(name=name, func_args=params, desc=desc, handler=handler, group="computer")
        count += 1
        logger.debug(f"注册 BetterGI 工具: {name}")

    async def _status_handler(**kwargs: Any) -> str:
        st = get_bettergi_manager().get_status()
        summary = {
            "state": st.get("state"),
            "monitoring": st.get("monitoring"),
            "running": st.get("running"),
            "pids": st.get("pids"),
            "task_state": st.get("task_state"),
            "last_event": st.get("last_event"),
            "last_task_result": st.get("last_task_result"),
            "detected_path": st.get("detected_path"),
            "auto_shutdown_pending": st.get("shutdown_pending"),
            "config": st.get("config"),
        }
        return json.dumps(summary, ensure_ascii=False)

    async def _run_handler(**kwargs: Any) -> str:
        mode = str(kwargs.get("mode") or "onedragon").strip().lower()
        config_name = str(kwargs.get("config_name") or "").strip()
        group_names = str(kwargs.get("group_names") or "").strip()
        result = get_bettergi_manager().launch(
            mode=mode,
            config_name=config_name,
            group_names=group_names,
        )
        return json.dumps(result, ensure_ascii=False)

    async def _kill_handler(**kwargs: Any) -> str:
        return json.dumps(get_bettergi_manager().kill(), ensure_ascii=False)

    async def _cancel_shutdown_handler(**kwargs: Any) -> str:
        return json.dumps(get_bettergi_manager().cancel_shutdown(), ensure_ascii=False)

    async def _detect_handler(**kwargs: Any) -> str:
        return json.dumps(get_bettergi_manager().detect(), ensure_ascii=False)

    async def _setup_notify_handler(**kwargs: Any) -> str:
        base_url = str(kwargs.get("base_url") or "http://127.0.0.1:8001").strip()
        transport = str(kwargs.get("transport") or "webhook").strip().lower()
        result = get_bettergi_manager().configure_notifications(base_url=base_url, transport=transport)
        return json.dumps(result, ensure_ascii=False)

    async def _vision_handler(**kwargs: Any) -> str:
        action = str(kwargs.get("action") or "status").strip().lower()
        mgr = get_bettergi_manager()
        if action == "status":
            return json.dumps(mgr.vision_status(), ensure_ascii=False)
        if action == "recover":
            return json.dumps(await mgr.vision_recover_once(), ensure_ascii=False)
        if action == "test":
            from .vision import capture_window, recognize
            shot = capture_window("", None, "game", False)
            if not shot.get("success"):
                return json.dumps({"success": False, "message": shot.get("message")}, ensure_ascii=False)
            image = shot.get("image")
            try:
                ocr = recognize(image)
            finally:
                try:
                    if image is not None:
                        image.close()
                except Exception:
                    pass
            return json.dumps(
                {
                    "success": ocr.get("success"),
                    "engine": ocr.get("engine"),
                    "text": ocr.get("text"),
                    "shot": "",
                    "message": ocr.get("message"),
                },
                ensure_ascii=False,
            )
        if action == "ensure":
            from .vision import ensure_engine
            return json.dumps(ensure_engine(), ensure_ascii=False)
        return json.dumps({"ok": False, "message": "未知 action: %s" % action}, ensure_ascii=False)

    async def _schedule_handler(**kwargs: Any) -> str:
        action = str(kwargs.get("action") or "status").strip().lower()
        mgr = get_bettergi_manager()
        if action == "status":
            return json.dumps(mgr.schedule_status(), ensure_ascii=False)
        if action == "toggle":
            enabled = bool(kwargs.get("enabled", True))
            mgr.update_config({"schedule_enabled": enabled})
            result = await (mgr.start_scheduler() if enabled else mgr.stop_scheduler())
            return json.dumps(result, ensure_ascii=False)
        return json.dumps({"ok": False, "message": "未知 action: %s" % action}, ensure_ascii=False)

    async def _autostart_handler(**kwargs: Any) -> str:
        action = str(kwargs.get("action") or "status").strip().lower()
        mgr = get_bettergi_manager()
        if action == "status":
            return json.dumps(mgr.get_autostart(), ensure_ascii=False)
        if action == "set":
            enabled = bool(kwargs.get("enabled", True))
            result = mgr.set_autostart(enabled)
            if result.get("ok"):
                mgr.update_config({"auto_start_on_boot": enabled})
            return json.dumps(result, ensure_ascii=False)
        return json.dumps({"ok": False, "message": "未知 action: %s" % action}, ensure_ascii=False)

    _register(
        "bettergi_status",
        [],
        "查询 BetterGI 托管状态：进程是否运行、监控是否开启、任务状态、最近事件与探测到的安装路径。",
        _status_handler,
    )

    _register(
        "bettergi_detect",
        [],
        "探测本机 BetterGI 安装路径与 User 配置目录。",
        _detect_handler,
    )

    _register(
        "bettergi_run",
        [
            {"name": "mode", "type": "string", "description": "启动模式：normal=普通启动，onedragon=一条龙，groups=配置组，task_progress=任务进度"},
            {"name": "config_name", "type": "string", "description": "一条龙配置名（mode=onedragon 时可选，留空使用当前选中配置）"},
            {"name": "group_names", "type": "string", "description": "配置组名，多个用英文逗号分隔（mode=groups/task_progress 时使用）"},
        ],
        "启动 BetterGI。注意：如果 BetterGI 已在运行，命令行任务参数不会作用到现有实例，需要先结束进程。",
        _run_handler,
    )

    _register(
        "bettergi_kill",
        [],
        "结束所有 BetterGI 进程。属于中断性操作，调用前应向用户确认。",
        _kill_handler,
    )

    _register(
        "bettergi_setup_notify",
        [
            {"name": "base_url", "type": "string", "description": "本服务地址，例如 http://127.0.0.1:8001"},
            {"name": "transport", "type": "string", "description": "事件推送方式：webhook 或 websocket"},
        ],
        "把 BetterGI 的任务事件通知写入其 User/config.json（会自动备份），写入后需重启 BetterGI 生效。",
        _setup_notify_handler,
    )

    _register(
        "bettergi_cancel_shutdown",
        [],
        "取消由 BetterGI 托管触发的自动关机倒计时。",
        _cancel_shutdown_handler,
    )

    _register(
        "bettergi_vision",
        [
            {"name": "action", "type": "string", "description": "status=查看 OCR 状态，test=截图并 OCR（不点击），recover=立即执行一次图像兜底，ensure=安装本地 OCR 引擎"},
        ],
        "BetterGI 图像识别兜底：本地 OCR 识别游戏画面，规则未命中时交给 AI 决策，并模拟点击。",
        _vision_handler,
    )

    _register(
        "bettergi_schedule",
        [
            {"name": "action", "type": "string", "description": "status=查看定时任务与下次执行时间，toggle=启停调度"},
            {"name": "enabled", "type": "boolean", "description": "action=toggle 时是否启用"},
        ],
        "BetterGI 定时任务：查看配置与下次执行时间，或启停调度。",
        _schedule_handler,
    )

    _register(
        "bettergi_autostart",
        [
            {"name": "action", "type": "string", "description": "status=查询开机自启，set=设置"},
            {"name": "enabled", "type": "boolean", "description": "action=set 时是否启用开机自启"},
        ],
        "BetterGI 开机自启：查询或设置当前用户的 Windows 启动项。",
        _autostart_handler,
    )

    logger.info(f"BetterGI 工具注册完成，共 {count} 个")
