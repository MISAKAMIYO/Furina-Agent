"""
音乐服务模块
提供音乐相关的核心服务

导出:
- LyricsParser: 歌词解析器
- CoverFinder: 封面查找器
- PlaylistManager: 播放列表管理器
- MusicDownloader: 音乐下载器
"""
from __future__ import annotations

from .lyrics_parser import LyricsParser
from .cover_finder import CoverFinder
from .playlist_manager import PlaylistManager
from .music_downloader import MusicDownloader

__all__ = [
    "LyricsParser",
    "CoverFinder",
    "PlaylistManager",
    "MusicDownloader",
]
