"""
数据库模块
提供 SQLite 数据库操作功能

模块结构:
- __init__.py   - 公共接口导出
- database.py   - 数据库连接和操作
"""
from __future__ import annotations

from .database import get_db, init_db

__all__ = ["get_db", "init_db"]
