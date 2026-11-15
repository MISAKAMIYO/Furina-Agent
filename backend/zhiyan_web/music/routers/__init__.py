"""
音乐路由模块
提供音乐相关的 API 路由

导出:
- music_router: 音乐播放路由
- download_router: 音乐下载路由
- netease_router: 网易云音乐播放器路由
"""
from __future__ import annotations

from .music import router as music_router
from .qq import router as qq_router
from .download import router as download_router
from .netease_player import router as netease_router

__all__ = [
    "music_router",
    "qq_router",
    "download_router",
    "netease_router",
]

from .kugou import router as kugou_router  # noqa: E402,F401
