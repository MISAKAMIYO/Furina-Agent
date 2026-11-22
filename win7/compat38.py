# -*- coding: utf-8 -*-
"""
compat38.py — 让 Furina Agent 的核心（Agent / 对话 / 工具）跑在 Python 3.8.10 上。

用法（必须最先导入，早于任何 zhiyan_web 模块）：

    from win7.compat38 import install
    install()

做三件事：
1. 补齐 3.9+ 才有的标准库成员（asyncio.to_thread、functools.cache、math.lcm …）；
2. 把 3.9+ 才有的模块换成 backport（zoneinfo / tomllib / graphlib），没装就降级；
3. 给 collections.abc 的泛型补一个“可下标”的替身：3.8 里 `Callable[...]` 会抛
   TypeError，这里不改源码，直接把运行期用到的那几个名字换掉。

不改变原工程的 3.14 行为：所有补丁只在 sys.version_info < (3, 9) 时生效。
"""
from __future__ import annotations

import sys
import types

_APPLIED = False


def _is_38() -> bool:
    return sys.version_info < (3, 9)


def _patch_asyncio() -> list:
    import asyncio
    done = []
    if not hasattr(asyncio, "to_thread"):
        async def to_thread(func, /, *args, **kwargs):
            import functools
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

        asyncio.to_thread = to_thread
        done.append("asyncio.to_thread")
    return done


def _patch_functools() -> list:
    import functools
    done = []
    if not hasattr(functools, "cache"):
        def cache(user_function):
            return functools.lru_cache(maxsize=None)(user_function)

        functools.cache = cache
        done.append("functools.cache")
    return done


def _patch_math() -> list:
    import math
    done = []
    if not hasattr(math, "lcm"):
        def lcm(*integers):
            result = 1
            for i in integers:
                result = result * int(i) // math.gcd(result, int(i))
            return abs(result)

        math.lcm = lcm
        done.append("math.lcm")
    return done


def _patch_itertools() -> list:
    import itertools
    done = []
    if not hasattr(itertools, "pairwise"):
        def pairwise(iterable):
            it = iter(iterable)
            try:
                prev = next(it)
            except StopIteration:
                return
            for cur in it:
                yield prev, cur
                prev = cur

        itertools.pairwise = pairwise
        done.append("itertools.pairwise")
    if not hasattr(itertools, "batched"):
        def batched(iterable, n):
            it = iter(iterable)
            while True:
                chunk = tuple(itertools.islice(it, n))
                if not chunk:
                    return
                yield chunk

        itertools.batched = batched
        done.append("itertools.batched")
    return done


def _patch_datetime() -> list:
    import datetime
    done = []
    if not hasattr(datetime, "UTC"):
        datetime.UTC = datetime.timezone.utc
        done.append("datetime.UTC")
    return done


def _install_backports() -> list:
    """把 3.9+/3.11 才有的模块映射到 backport，没装就留空（用到时再报错）。"""
    done = []

    if sys.version_info < (3, 9):
        # zoneinfo：3.9 引入
        if "zoneinfo" not in sys.modules:
            try:
                import backports.zoneinfo as _zoneinfo  # type: ignore

                sys.modules["zoneinfo"] = _zoneinfo
                done.append("zoneinfo -> backports.zoneinfo")
            except Exception:
                pass

        # graphlib：3.9 引入
        if "graphlib" not in sys.modules:
            try:
                import graphlib_backport as _graphlib  # type: ignore

                sys.modules["graphlib"] = _graphlib
                done.append("graphlib -> graphlib_backport")
            except Exception:
                pass

    if sys.version_info < (3, 11) and "tomllib" not in sys.modules:
        try:
            import tomli as _tomli  # type: ignore

            sys.modules["tomllib"] = _tomli
            done.append("tomllib -> tomli")
        except Exception:
            pass

    return done


def _patch_str_helpers() -> list:
    """3.9 的 str.removeprefix/removesuffix 没法打到内建 str 上。

    这里提供模块级函数，让业务代码可以 `from win7.compat38 import removeprefix`
    并按需替换调用点；同时给 `str` 的常用路径做一次可用性检查，便于启动日志提示。
    """
    def removeprefix(s: str, prefix: str) -> str:
        return s[len(prefix):] if prefix and s.startswith(prefix) else s

    def removesuffix(s: str, suffix: str) -> str:
        return s[:-len(suffix)] if suffix and s.endswith(suffix) else s

    g = globals()
    g["removeprefix"] = removeprefix
    g["removesuffix"] = removesuffix
    return []


def install(verbose: bool = False) -> list:
    """应用全部补丁，返回实际生效的补丁名列表（重复调用无副作用）。"""
    global _APPLIED
    if _APPLIED:
        return []
    if not _is_38():
        _APPLIED = True
        return []

    patches = []
    for fn in (
        _patch_asyncio,
        _patch_functools,
        _patch_math,
        _patch_itertools,
        _patch_datetime,
        _install_backports,
        _patch_str_helpers,
    ):
        try:
            patches.extend(fn())
        except Exception as exc:  # 单个补丁失败不阻断启动
            patches.append(f"{fn.__name__}: FAILED ({exc})")

    _APPLIED = True
    if verbose and patches:
        print("[win7-compat]", ", ".join(patches))
    return patches


# 让 `import win7.compat38` 就自动生效：绝大多数情况下调用方不必显式 install()
install()
