# -*- coding: utf-8 -*-
"""
selftest.py — Win7 / Python 3.8 自检。

依次检查：Python 版本 → 兼容补丁 → 精简后端导入 → 路由数量 → mock 流式对话。
不联网、不写 data/（用环境变量把设置指向临时目录）。

用法：
    python win7/selftest.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, os.path.join(_ROOT, "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)


def ok(label: str, detail: str = "") -> None:
    print(f"[ok]   {label}" + (f"  {detail}" if detail else ""))


def fail(label: str, detail: str = "") -> None:
    print(f"[FAIL] {label}" + (f"  {detail}" if detail else ""))
    raise SystemExit(1)


def main() -> int:
    print("=" * 62)
    print(" Furina Agent · Win7 self-test")
    print("=" * 62)

    ver = sys.version_info
    if ver[:2] == (3, 8):
        ok("python", f"{ver.major}.{ver.minor}.{ver.micro}")
    elif ver[:2] < (3, 8):
        fail("python", f"{ver.major}.{ver.minor} 太旧，需要 3.8.10")
    else:
        ok("python", f"{ver.major}.{ver.minor}.{ver.micro}（非 3.8，兼容层不启用，也能跑）")

    import win7.compat38 as compat

    patches = compat.install(verbose=False)
    ok("compat38", f"{len(patches)} patches" if patches else "nothing to patch")

    try:
        import asyncio

        assert hasattr(asyncio, "to_thread")
        import functools

        assert hasattr(functools, "cache")
    except Exception as exc:
        fail("stdlib shims", repr(exc))
    ok("stdlib shims", "asyncio.to_thread / functools.cache")

    try:
        from win7.agent_app import app
    except Exception as exc:
        fail("agent_app import", f"{type(exc).__name__}: {exc}")
    ok("agent_app import")

    n_routes = len(app.routes)
    (ok if n_routes > 20 else fail)("routes", f"{n_routes}")

    # provider 是否挂上
    try:
        from zhiyan_web.core import chat_manager

        provider = getattr(chat_manager, "_provider", None)
        ok("llm provider", provider.name if provider else "None（首次启动请到设置里填 API Key）")
    except Exception as exc:
        fail("chat_manager", repr(exc))

    # mock 流式对话：不联网
    try:
        import asyncio

        from zhiyan_web.core import chat_manager
        from zhiyan_web.core.llm.providers import MockProvider

        prev = getattr(chat_manager, "_provider", None)
        chat_manager.set_provider(MockProvider())

        async def run() -> str:
            parts = []

            def on_chunk(chunk) -> None:
                parts.append(str(chunk))

            await chat_manager.chat_stream("你好", "selftest", callback=on_chunk)
            return "".join(parts)

        loop = asyncio.new_event_loop()
        try:
            text = loop.run_until_complete(run())
        finally:
            loop.close()
        if prev is not None:
            chat_manager.set_provider(prev)
        (ok if text else fail)("mock stream", f"{len(text)} chars")
    except Exception as exc:
        fail("mock stream", f"{type(exc).__name__}: {exc}")

    print("-" * 62)
    print("所有检查通过。启动：python win7\\launch.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
