"""
插件元数据模块 - 兼容层
所有功能委托给 zhiyan_web.star.star 实现
"""

from __future__ import annotations
from zhiyan_web.star.star import StarMetadata, star_map, star_registry

# 向后兼容别名
PluginMetadata = StarMetadata
plugin_registry = star_registry
plugin_map = star_map

__all__ = [
    "PluginMetadata",
    "plugin_registry",
    "plugin_map",
]