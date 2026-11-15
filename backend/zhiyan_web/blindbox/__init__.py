"""
盲盒系统模块
提供盲盒抽取、收藏、历史记录功能
"""
from __future__ import annotations

from .services import (
    init_blindbox_db,
    open_blindbox,
    get_collection,
    get_history,
)

__all__ = [
    "init_blindbox_db",
    "open_blindbox",
    "get_collection",
    "get_history",
]
