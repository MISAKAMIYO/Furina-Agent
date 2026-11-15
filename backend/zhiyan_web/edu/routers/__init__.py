"""教辅子系统路由包"""
from __future__ import annotations

from .edu import init_edu_tables, router as edu_router
from .teaching import router as teach_router

__all__ = ["edu_router", "teach_router", "init_edu_tables"]