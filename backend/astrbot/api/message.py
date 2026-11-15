"""AstrBot api.message 兼容模块（加载级）。

提供 GreedyStr 类型：命令名之后的"剩余全部文本"，由平台参数解析器注入，
兼容插件 `from astrbot.api.message import GreedyStr` 的写法。
"""
from __future__ import annotations

from zhiyan_web.star.filter.command import GreedyStr

__all__ = ["GreedyStr"]