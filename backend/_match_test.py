# -*- coding: utf-8 -*-
"""只测试 CommandFilter 匹配（不执行 handler），找出无法匹配的命令"""
import sys
import os
import asyncio
import types
import logging

logging.basicConfig(level=logging.ERROR)

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
from zhiyan_web.star.star_handler import star_handlers_registry, EventType
from zhiyan_web.star.platform import AstrMessageEvent


async def main():
    ctx = Context()
    pm = PluginManager(context=ctx, config={})
    ok, failed = await pm.load()
    print(f"插件加载: ok={ok}, failed={failed}")

    handlers = [h for h in star_handlers_registry
                if h.event_type == EventType.AdapterMessageEvent and h.enabled
                and not h.extras_configs.get("command_group")]
    print(f"可执行 AdapterMessageEvent handler: {len(handlers)}")

    # 用真实过滤器测试匹配（与运行时一致）
    no_match = []
    total = 0
    for h in handlers:
        plugin = None
        for m in star_registry_all():
            if m.module_path == h.handler_module_path:
                plugin = m
                break
        for f in h.event_filters:
            if hasattr(f, 'command_name'):
                for name in [f.command_name] + list(f.alias or []):
                    for msg in [f"/{name}", f"/{name} 12345"]:
                        total += 1
                        ev = AstrMessageEvent(message_str=msg)
                        ev.is_at_or_wake_command = True
                        ev.wake_prefixes = ["/", "!"]
                        try:
                            m = f.filter(ev, {})
                        except Exception as e:
                            m = f"EXC:{type(e).__name__}:{e}"
                        if m is not True:
                            no_match.append((msg, h.handler_name, str(m)[:80]))
    print(f"共测试 {total} 种命令格式，匹配失败 {len(no_match)}")
    print("=" * 70)
    for msg, handler, reason in no_match:
        print(f"不匹配: {msg!r:22s} handler={handler:22s} 原因={reason}")


def star_registry_all():
    from zhiyan_web.star.star import star_registry
    return list(star_registry)


asyncio.run(main())
