"""
五子棋游戏模块
提供人机对战五子棋功能
"""
from __future__ import annotations

from .services import (
    init_gomoku_db,
    create_new_game,
    make_move,
    get_game_status,
    cancel_game,
)

__all__ = [
    "init_gomoku_db",
    "create_new_game",
    "make_move",
    "get_game_status",
    "cancel_game",
]
