"""
音乐播放器模块
提供音乐播放、播放列表管理、歌词解析、封面搜索功能

模块结构:
- __init__.py           - 公共接口导出
- services/             - 服务层
  - lyrics_parser.py    - 歌词解析服务
  - cover_finder.py     - 封面搜索服务
  - playlist_manager.py - 播放列表管理服务
  - music_downloader.py - 音乐下载服务
- routers/              - 路由层
  - music.py            - 音乐播放器路由
"""
from __future__ import annotations

from .services.lyrics_parser import LyricsParser
from .services.cover_finder import CoverFinder
from .services.playlist_manager import PlaylistManager
from .services.music_downloader import MusicDownloader

__all__ = [
    "LyricsParser",
    "CoverFinder",
    "PlaylistManager",
    "MusicDownloader",
]
