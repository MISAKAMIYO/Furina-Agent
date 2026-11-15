# -*- coding: utf-8 -*-
"""忠实复现运行时：通过 PluginManager.load() 加载插件并运行 ProcessStage"""
import sys
import os
import asyncio
import types
import logging

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BACKEND = r"d:\Python\NEW_start\ZHIYAN AGENT WEB\backend"
PROJECT = r"d:\Python\NEW_start\ZHIYAN AGENT WEB"
sys.path.insert(0, BACKEND)

# 与 star_manager 一致：构建 data / data.plugins 命名空间包
if "data" not in sys.modules:
    data_pkg = types.ModuleType("data")
    data_pkg.__path__ = [os.path.join(PROJECT, "data")]
    sys.modules["data"] = data_pkg
if "data.plugins" not in sys.modules:
    dp = types.ModuleType("data.plugins")
    dp.__path__ = [os.path.join(PROJECT, "data", "plugins")]
    sys.modules["data.plugins"] = dp

from zhiyan_web.star import Context, PluginManager
from zhiyan_web.star.star_handler import star_handlers_registry
from zhiyan_web.pipeline.context import PipelineContext
from zhiyan_web.pipeline.scheduler import PipelineEvent
from zhiyan_web.pipeline.stages.process import ProcessStage


async def main():
    ctx = Context()
    pm = PluginManager(context=ctx, config={})
    # 只加载 box 插件
    ok, failed = await pm.load(specified_dir_name="astrbot_plugin_box")
    print(f"load result: ok={ok}, failed={failed}")

    # 检查 box handler 是否已绑定（functools.partial）
    box_handlers = [h for h in star_handlers_registry if "astrbot_plugin_box" in (h.handler_full_name or "")]
    print("box handlers:", len(box_handlers))
    for h in box_handlers:
        print(f"  {h.handler_full_name} | handler_type={type(h.handler).__name__}")
        for f in h.event_filters:
            if hasattr(f, "command_name"):
                print(f"     command={f.command_name} alias={f.alias} handler_params={getattr(f,'handler_params','N/A')}")

    # 构造 ProcessStage（忠实复现运行时：context 只有 plugin_manager）
    pctx = PipelineContext()
    pctx.plugin_manager = pm
    stage = ProcessStage(pctx)

    for msg in ["/盒 1668255883", "/plugin_list", "/盒", "/开盒 12345"]:
        print(f"\n===== 测试消息: {msg} =====")
        ev = PipelineEvent({
            "message": msg,
            "is_at_or_wake_command": True,
            "is_wake": True,
            "wake_prefixes": ["/", "!"],
            "session_id": "test-session",
            "sender_id": "123",
            "sender_name": "tester",
        })
        try:
            await stage.process(ev)
            result = ev.get_result()
            preview = str(result)[:150] if result else None
            print(f"  结果: {preview}")
            print(f"  已停止: {ev.is_stopped()}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  异常: {type(e).__name__}: {e}")


asyncio.run(main())
