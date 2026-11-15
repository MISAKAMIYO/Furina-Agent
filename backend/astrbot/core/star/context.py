"""AstrBot core.star.context 兼容模块。

插件（如 astrbot_plugin_apis）会 `from astrbot.core.star.context import
Context`。ZhiYan 兼容层的 Context 定义在 astrbot.api.star，这里统一重导出。
"""
from __future__ import annotations

from astrbot.api.star import Context

__all__ = ["Context"]