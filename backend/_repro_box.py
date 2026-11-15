# -*- coding: utf-8 -*-
"""临时脚本：复现 /盒 1668255883 过滤器匹配问题"""
import sys
import os
import asyncio
import importlib
import types

BACKEND = r"d:\Python\NEW_start\ZHIYAN AGENT WEB\backend"
PROJECT = r"d:\Python\NEW_start\ZHIYAN AGENT WEB"
sys.path.insert(0, BACKEND)

# 设置 data.plugins 命名空间包（与 star_manager 一致）
if "data" not in sys.modules:
    data_pkg = types.ModuleType("data")
    data_pkg.__path__ = [os.path.join(PROJECT, "data")]
    sys.modules["data"] = data_pkg
if "data.plugins" not in sys.modules:
    dp = types.ModuleType("data.plugins")
    dp.__path__ = [os.path.join(PROJECT, "data", "plugins")]
    sys.modules["data.plugins"] = dp

from zhiyan_web.star.platform import AstrMessageEvent
from zhiyan_web.star.star_handler import star_handlers_registry
from zhiyan_web.star import star_handler as sh_mod
from zhiyan_web.star.filter.command import CommandFilter

def make_event(message: str):
    ev = AstrMessageEvent(message_str=message, session_id="test", sender_id="123", sender_name="tester")
    ev.is_at_or_wake_command = True
    ev.is_wake = True
    ev.wake_prefixes = ["/", "!"]
    return ev

async def main():
    # 1. 以包方式导入 box 插件（触发 handler 注册）
    box_dir = os.path.join(PROJECT, "data", "plugins", "astrbot_plugin_box")
    sys.path.insert(0, box_dir)
    pkg_name = "data.plugins.astrbot_plugin_box"
    spec = importlib.util.spec_from_file_location(pkg_name, os.path.join(box_dir, "__init__.py"),
                                                  submodule_search_locations=[box_dir])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = pkg
    if spec.loader:
        spec.loader.exec_module(pkg)
    mod = importlib.import_module(f"{pkg_name}.main")
    print("导入成功:", mod.__name__)

    # 2. 实例化插件类
    from astrbot.api.star import Context
    cls = None
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, type) and obj.__module__ == mod.__name__:
            if hasattr(obj, "on_command"):
                cls = obj
                break
    print("插件类:", cls)
    inst = cls(Context(), {})
    print("实例化成功:", type(inst).__name__)

    # 3. 查找 box 插件 handlers
    EventType = sh_mod.EventType
    handlers = star_handlers_registry.get_handlers_by_event_type(EventType.AdapterMessageEvent, only_activated=True)
    box_handlers = [h for h in handlers if "astrbot_plugin_box" in (h.handler_full_name or "")]
    print("box handlers 数量:", len(box_handlers))
    for h in box_handlers:
        print("  HANDLER:", h.handler_full_name, "| filters:", [type(f).__name__ for f in h.event_filters])
        for f in h.event_filters:
            if hasattr(f, "command_name"):
                print("     command_name:", f.command_name, "| alias:", f.alias, "| parent:", f.parent_command_names,
                      "| handler_params:", getattr(f, "handler_params", "N/A"))

    # 4. 用 "/盒 1668255883" 测试过滤器并实际执行 handler
    ev = make_event("/盒 1668255883")
    for h in box_handlers:
        for f in h.event_filters:
            try:
                r = f.filter(ev, {})
                print(f"  FILTER {h.handler_name} {type(f).__name__} -> {r}")
            except Exception as e:
                import traceback; traceback.print_exc()
                print(f"  FILTER EXC: {e}")
        print("  parsed_params:", getattr(ev, "_extra", {}).get("parsed_params"))
        # 实际执行 handler
        print("  event 属性检查: bot=", hasattr(ev, "bot"),
              "| get_group_id=", hasattr(ev, "get_group_id"),
              "| get_sender_id=", hasattr(ev, "get_sender_id"),
              "| get_self_id=", hasattr(ev, "get_self_id"),
              "| is_admin=", hasattr(ev, "is_admin"),
              "| message_obj=", hasattr(ev, "message_obj"))
        parsed_params = getattr(ev, "_extra", {}).get("parsed_params") or {}
        fn = h.handler
        try:
            hr = fn(ev, **parsed_params)
            print("  handler 调用成功, result type:", type(hr).__name__)
            if hasattr(hr, "__aiter__"):
                items = []
                async for it in hr:
                    items.append(str(it)[:80])
                print("  async gen items:", items)
            else:
                print("  await:", await hr)
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"  HANDLER EXC: {type(e).__name__}: {e}")

    # 5. 直接构造 CommandFilter 测试（模拟 box handler 签名）
    print("\n=== 直接构造 CommandFilter 测试 ===")
    async def fake_handler(self, event, input_id=None):
        pass
    md = sh_mod.get_handler_or_create(fake_handler)
    cf = CommandFilter("盒", alias={"开盒"}, handler_md=md)
    ev2 = make_event("/盒 1668255883")
    try:
        r = cf.filter(ev2, {})
        print("直接 CommandFilter filter ->", r, "| params:", getattr(ev2, "_extra", {}).get("parsed_params"))
    except Exception as e:
        import traceback; traceback.print_exc()

asyncio.run(main())
