# -*- coding: utf-8 -*-
"""全面检测：加载所有插件，逐个测试每个命令的匹配（带参数格式）"""
import sys
import os
import asyncio
import types
import logging

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s: %(message)s")

BACKEND = r"d:\Python\NEW_start\ZHIYAN AGENT WEB\backend"
PROJECT = r"d:\Python\NEW_start\ZHIYAN AGENT WEB"
sys.path.insert(0, BACKEND)

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
from zhiyan_web.star.filter.command import CommandFilter
from zhiyan_web.pipeline.scheduler import PipelineEvent
from zhiyan_web.pipeline.stages.process import ProcessStage
from zhiyan_web.pipeline.context import PipelineContext


def make_event(message: str):
    ev = PipelineEvent({
        "message": message,
        "is_at_or_wake_command": True,
        "is_wake": True,
        "wake_prefixes": ["/", "!"],
        "session_id": "test-session",
        "sender_id": "123",
        "sender_name": "tester",
    })
    return ev


async def main():
    ctx = Context()
    pm = PluginManager(context=ctx, config={})
    ok, failed = await pm.load()
    print(f"插件加载: ok={ok}, failed={failed}")

    # 收集所有 AdapterMessageEvent handler 及其命令过滤器
    handlers = [h for h in star_handlers_registry
                if h.event_type.name == "AdapterMessageEvent"]
    print(f"共 {len(handlers)} 个消息 handler")

    # 跳过命令组处理器
    cmd_handlers = [h for h in handlers if not h.extras_configs.get("command_group")]
    print(f"可执行 handler: {len(cmd_handlers)}")

    _pctx = PipelineContext()
    _pctx.plugin_manager = pm
    stage = ProcessStage(_pctx)

    # 测试消息：每个命令名 + 示例参数
    test_cases = []
    seen = set()
    for h in cmd_handlers:
        for f in h.event_filters:
            if isinstance(f, CommandFilter):
                for name in [f.command_name] + list(f.alias or []):
                    parents = f.parent_command_names or [""]
                    for parent in parents:
                        full = f"{parent} {name}".strip() if parent else name
                        if full in seen:
                            continue
                        seen.add(full)
                        test_cases.append((h.handler_name, full))
                        test_cases.append((h.handler_name, full + " 12345"))

    print(f"待测命令格式: {len(test_cases)}")
    print("=" * 60)

    matched = 0
    for handler_name, msg in test_cases:
        ev = make_event(msg)
        try:
            await stage.process(ev)
        except Exception as e:
            print(f"[异常] {msg!r} ({handler_name}): {type(e).__name__}: {e}")
            continue
        result = ev.get_result()
        rtext = str(result)[:60] if result else "None"
        handled = "已处理" if ev.is_stopped() else "未处理→LLM"
        matched += 1 if ev.is_stopped() else 0
        print(f"{msg!r:26s} {handler_name:18s} [{handled}] {rtext}")

    print("=" * 60)
    print(f"匹配成功: {matched}/{len(test_cases)}")


asyncio.run(main())
