"""
五子棋服务层
"""
from __future__ import annotations

from .gomoku_service import (
    init_gomoku_db,
    create_new_game,
    make_move,
    get_game_status,
    cancel_game,
    USER_ID,
)

__all__ = [
    "init_gomoku_db",
    "create_new_game",
    "make_move",
    "get_game_status",
    "cancel_game",
    "USER_ID",
]
