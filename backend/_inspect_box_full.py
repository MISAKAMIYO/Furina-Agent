# -*- coding: utf-8 -*-
"""加载所有插件，检查 box handler 在完整加载场景下的状态"""
import sys
import os
import asyncio
import types
import logging

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

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


async def main():
    ctx = Context()
    pm = PluginManager(context=ctx, config={})
    ok, failed = await pm.load()
    print(f"加载完成 ok={ok} failed={failed}", flush=True)

    # 找 box 插件
    box_handlers = [h for h in star_handlers_registry if "astrbot_plugin_box" in (h.handler_full_name or "")]
    print(f"box handlers: {len(box_handlers)}", flush=True)
    for h in box_handlers:
        print(f"  {h.handler_full_name} | type={type(h.handler).__name__} | enabled={h.enabled}", flush=True)
        for f in h.event_filters:
            if isinstance(f, CommandFilter):
                print(f"    CommandFilter cmd={f.command_name!r} alias={f.alias} parent={f.parent_command_names} params={getattr(f,'handler_params','N/A')}", flush=True)

    # 尝试匹配 /盒 1668255883
    try:
        from zhiyan_web.star.platform import AstrMessageEvent as AE
        ev = AE(message_str="/盒 1668255883", session_id="s", sender_id="1", sender_name="t")
        ev.is_at_or_wake_command = True
        ev.is_wake = True
        ev.wake_prefixes = ["/", "!"]
        for h in box_handlers:
            for f in h.event_filters:
                if isinstance(f, CommandFilter):
                    try:
                        r = f.filter(ev, {})
                        print(f"  filter({f.command_name}) -> {r}, parsed={ev.get_extra('parsed_params')}", flush=True)
                    except Exception as e:
                        import traceback; traceback.print_exc()
    except Exception as e:
        import traceback; traceback.print_exc()


asyncio.run(main())
