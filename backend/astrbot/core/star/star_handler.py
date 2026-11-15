"""
AstrBot core.star.star_handler 兼容模块
"""

from zhiyan_web.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    StarHandlerRegistry,
    star_handlers_registry,
)

# 兼容 AstrBot 的 star_handler_export
star_handler_export = star_handlers_registry

__all__ = [
    "EventType",
    "StarHandlerMetadata",
    "StarHandlerRegistry",
    "star_handlers_registry",
    "star_handler_export",
]