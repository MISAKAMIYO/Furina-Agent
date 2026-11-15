"""
盲盒系统服务层
"""
from __future__ import annotations

from .blindbox_service import (
    init_blindbox_db,
    open_blindbox,
    get_collection,
    get_history,
    USER_ID,
)

__all__ = [
    "init_blindbox_db",
    "open_blindbox",
    "get_collection",
    "get_history",
    "USER_ID",
]
