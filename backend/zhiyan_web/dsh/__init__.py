"""DSH (DeepSeek Harness) 对话引擎集成包。"""
from __future__ import annotations

from zhiyan_web.dsh.engine import get_dsh_engine, DshEngine, new_session_id
from zhiyan_web.dsh.routers import router as dsh_router

# 兼容 app.py 的 `from zhiyan_web.dsh import router`
router = dsh_router

__all__ = ["get_dsh_engine", "DshEngine", "new_session_id", "dsh_router", "router"]