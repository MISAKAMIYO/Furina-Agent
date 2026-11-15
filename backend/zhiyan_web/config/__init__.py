"""
配置模块
提供系统配置管理功能

导出:
- load_settings, save_settings, get_settings, update_settings, get_setting
- 各种目录路径常量
"""
from __future__ import annotations

from .settings import (
    load_settings,
    save_settings,
    get_settings,
    update_settings,
    get_setting,
    PROJECT_ROOT,
    FRONTEND_DIR,
    DATA_DIR,
    SONGS_DIR,
    COVERS_DIR,
    LYRICS_DIR,
    PLAYLISTS_DIR,
    TTS_DIR,
    resource_path,
    BUILTIN_PLUGINS_DIR,
)

__all__ = [
    "load_settings",
    "save_settings",
    "get_settings",
    "update_settings",
    "get_setting",
    "PROJECT_ROOT",
    "FRONTEND_DIR",
    "DATA_DIR",
    "SONGS_DIR",
    "COVERS_DIR",
    "LYRICS_DIR",
    "PLAYLISTS_DIR",
    "TTS_DIR",
    "resource_path",
    "BUILTIN_PLUGINS_DIR",
]
