"""
播放列表管理服务
负责管理音乐播放列表和收藏功能

核心功能:
- 多播放列表支持（创建、切换、删除、重命名）
- 歌曲增删改查
- 收藏管理
- 自动从 Songs 目录扫描
"""
from __future__ import annotations

import os
import re
import json
import time
import shutil
import logging
import urllib.parse
from typing import List, Dict, Any, Optional

from zhiyan_web.config.settings import DATA_DIR, SONGS_DIR, PLAYLISTS_DIR, COVERS_DIR, LYRICS_DIR
from zhiyan_web.music.services.cover_finder import CoverFinder
from zhiyan_web.music.services.lyrics_parser import LyricsParser

logger = logging.getLogger("ZhiYanWeb")


class PlaylistManager:
    """
    播放列表管理器

    管理多播放列表、收藏列表和歌曲信息
    """

    def __init__(self):
        """初始化播放列表管理器"""
        self._ensure_dirs()
        # 当前播放列表文件路径
        self._current_playlist_path = os.path.join(DATA_DIR, 'playlist.json')
        # 旧 ZhiYan Agent 目录兼容
        old_songs_dir = os.path.join(os.path.dirname(DATA_DIR), 'ZhiYan agent', 'data', 'Songs')
        # 可能的音乐文件存储位置（按优先级排序）
        self._music_search_paths = [
            SONGS_DIR,
            os.path.join(DATA_DIR, 'plugins_data', 'astrbot_plugin_music', 'songs'),
            os.path.join(DATA_DIR, 'Songs'),
            old_songs_dir,
        ]

    def _ensure_dirs(self) -> None:
        """确保必要的目录存在"""
        os.makedirs(PLAYLISTS_DIR, exist_ok=True)
        os.makedirs(SONGS_DIR, exist_ok=True)

    def _find_music_file(self, filename: str) -> str:
        """在所有可能的音乐目录中查找音乐文件"""
        for search_path in self._music_search_paths:
            if os.path.exists(search_path):
                filepath = os.path.join(search_path, filename)
                if os.path.exists(filepath):
                    return filepath
        return ""

    def _get_all_music_files(self) -> Dict[str, str]:
        """获取所有音乐文件的映射（文件名 -> 完整路径）"""
        music_files = {}
        for search_path in self._music_search_paths:
            if os.path.exists(search_path):
                for filename in os.listdir(search_path):
                    if filename.lower().endswith(('.mp3', '.wav', '.flac', '.m4a')):
                        if filename not in music_files:
                            music_files[filename] = os.path.join(search_path, filename)
        return music_files

    # ===== 播放列表文件操作 =====

    def _read_playlist_file(self, filepath: str) -> dict:
        """读取播放列表文件"""
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"读取播放列表失败 {filepath}: {e}")
        return {"version": "1.0", "playlist": [], "timestamp": 0, "current_index": -1}

    def _write_playlist_file(self, filepath: str, data: dict) -> bool:
        """写入播放列表文件"""
        try:
            data['timestamp'] = time.time()
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"写入播放列表失败 {filepath}: {e}")
            return False

    def _build_song_entry(self, item: dict) -> dict:
        """将播放列表项转换为前端需要的格式"""
        filename = item.get('filename', '')
        title = item.get('title', '')
        path = item.get('path', '')
        author = item.get('author', item.get('artist', ''))

        # 兼容旧格式：filename 为空时从 path 提取
        if not filename and path:
            filename = os.path.basename(path)
            # 如果 path 指向旧目录，尝试在新目录中查找
            if not os.path.exists(path):
                filepath = self._find_music_file(filename)
                if filepath:
                    path = filepath

        if not filename:
            return None

        # 优先使用 path 字段
        filepath = ""
        if path and os.path.exists(path):
            filepath = path
        else:
            filepath = self._find_music_file(filename)

        if not filepath:
            return None

        size = os.path.getsize(filepath)
        song_title = title or os.path.splitext(filename)[0]

        return {
            "id": abs(hash(filename)),
            "filename": filename,
            "title": song_title,
            "artist": author,
            "size": size,
            "url": f"/api/music/stream/{urllib.parse.quote(filename, safe='')}",
            "cover": CoverFinder.find_cover(song_title, filename),
            "has_lyrics": bool(LyricsParser.find_lyrics(song_title, filename)[0]),
            "is_favorite": self._is_favorite(filename)
        }

    # ===== 核心 API =====

    def get_songs(self) -> List[Dict[str, Any]]:
        """
        获取当前播放列表的所有歌曲

        优先从 playlist.json 读取，为空则从 Songs 目录扫描
        """
        songs = self.get_playlist_from_file()

        if not songs:
            songs = []
            music_files = self._get_all_music_files()
            for filename, filepath in music_files.items():
                size = os.path.getsize(filepath)
                title = os.path.splitext(filename)[0]
                songs.append({
                    "id": abs(hash(filename)),
                    "filename": filename,
                    "title": title,
                    "artist": "",
                    "size": size,
                    "url": f"/api/music/stream/{urllib.parse.quote(filename, safe='')}",
                    "cover": CoverFinder.find_cover(title, filename),
                    "has_lyrics": bool(LyricsParser.find_lyrics(title, filename)[0]),
                    "is_favorite": self._is_favorite(filename)
                })
            logger.info(f"从目录扫描获取到 {len(songs)} 首歌曲")

        return songs

    def get_playlist_from_file(self) -> List[Dict[str, Any]]:
        """从当前播放列表文件读取歌曲列表"""
        data = self._read_playlist_file(self._current_playlist_path)
        playlist = data.get('playlist', [])
        songs = []

        for item in playlist:
            if isinstance(item, str):
                item = {"filename": item, "title": os.path.splitext(item)[0]}

            song = self._build_song_entry(item)
            if song:
                songs.append(song)

        return songs

    # ===== 歌曲管理 =====

    def add_song(self, filename: str, filepath: str = "",
                 title: str = "", artist: str = "") -> Dict[str, Any]:
        """
        添加歌曲到当前播放列表

        参数:
            filename: 歌曲文件名
            filepath: 完整路径（可选）
            title: 歌曲标题
            artist: 艺术家

        返回:
            dict: 操作结果
        """
        data = self._read_playlist_file(self._current_playlist_path)
        playlist = data.get('playlist', [])

        # 检查是否已存在
        for item in playlist:
            if isinstance(item, dict) and item.get('filename') == filename:
                return {"success": True, "message": "歌曲已在播放列表中", "added": False}

        # 创建新条目
        entry = {
            "filename": filename,
            "title": title or os.path.splitext(filename)[0],
            "author": artist,
            "path": filepath or self._find_music_file(filename),
        }
        playlist.append(entry)
        data['playlist'] = playlist
        self._write_playlist_file(self._current_playlist_path, data)

        logger.info(f"歌曲已添加到播放列表: {filename}")
        return {"success": True, "message": "已添加", "added": True}

    def remove_song(self, filename: str) -> Dict[str, Any]:
        """
        从当前播放列表移除歌曲

        参数:
            filename: 歌曲文件名

        返回:
            dict: 操作结果
        """
        data = self._read_playlist_file(self._current_playlist_path)
        playlist = data.get('playlist', [])
        original_len = len(playlist)

        data['playlist'] = [item for item in playlist
                            if (isinstance(item, dict) and item.get('filename') != filename)
                            or (isinstance(item, str) and item != filename)]

        if len(data['playlist']) == original_len:
            return {"success": False, "message": "未找到该歌曲"}

        self._write_playlist_file(self._current_playlist_path, data)
        logger.info(f"歌曲已从播放列表移除: {filename}")
        return {"success": True, "message": "已移除"}

    def clear_playlist(self) -> Dict[str, Any]:
        """清空当前播放列表"""
        data = self._read_playlist_file(self._current_playlist_path)
        data['playlist'] = []
        self._write_playlist_file(self._current_playlist_path, data)
        logger.info("播放列表已清空")
        return {"success": True, "message": "播放列表已清空"}

    def save_playlist(self, songs: list) -> Dict[str, Any]:
        """
        保存歌曲列表到当前播放列表文件

        参数:
            songs: 歌曲列表 [{"filename": "...", "title": "...", "artist": "...", "path": "..."}]

        返回:
            dict: 操作结果
        """
        data = self._read_playlist_file(self._current_playlist_path)
        data['playlist'] = songs
        self._write_playlist_file(self._current_playlist_path, data)
        logger.info(f"播放列表已保存 ({len(songs)} 首)")
        return {"success": True, "message": f"已保存 {len(songs)} 首歌曲"}

    # ===== 多播放列表管理 =====

    def list_playlists(self) -> List[Dict[str, Any]]:
        """
        列出所有播放列表

        返回:
            list: [{"name": "...", "filepath": "...", "count": N, "is_current": bool}]
        """
        playlists = []

        # 默认播放列表
        default_path = os.path.join(DATA_DIR, 'playlist.json')
        default_data = self._read_playlist_file(default_path)
        playlists.append({
            "name": "默认播放列表",
            "filepath": default_path,
            "count": len(default_data.get('playlist', [])),
            "is_current": default_path == self._current_playlist_path
        })

        # Playlists 目录下的其他播放列表
        if os.path.exists(PLAYLISTS_DIR):
            for fname in sorted(os.listdir(PLAYLISTS_DIR)):
                if fname.endswith('.json') and fname != '我喜欢.json':
                    filepath = os.path.join(PLAYLISTS_DIR, fname)
                    data = self._read_playlist_file(filepath)
                    name = fname.replace('.json', '')
                    playlists.append({
                        "name": name,
                        "filepath": filepath,
                        "count": len(data.get('playlist', [])),
                        "is_current": filepath == self._current_playlist_path
                    })

        return playlists

    def create_playlist(self, name: str) -> Dict[str, Any]:
        """
        创建新播放列表

        参数:
            name: 播放列表名称

        返回:
            dict: 操作结果
        """
        if not name or not name.strip():
            return {"success": False, "message": "名称不能为空"}

        safe_name = re.sub(r'[\\/:*?"<>|]', '_', name.strip())
        filename = safe_name + '.json'
        filepath = os.path.join(PLAYLISTS_DIR, filename)

        if os.path.exists(filepath):
            return {"success": False, "message": "播放列表已存在"}

        data = {
            "version": "1.0",
            "playlist": [],
            "timestamp": time.time(),
            "current_index": -1,
            "name": name.strip()
        }
        self._write_playlist_file(filepath, data)
        logger.info(f"播放列表已创建: {name}")
        return {"success": True, "message": f"已创建播放列表: {name}", "filepath": filepath}

    def switch_playlist(self, filepath: str) -> Dict[str, Any]:
        """
        切换到指定播放列表

        参数:
            filepath: 播放列表文件路径

        返回:
            dict: 操作结果
        """
        if not os.path.exists(filepath):
            return {"success": False, "message": "播放列表文件不存在"}

        self._current_playlist_path = filepath
        logger.info(f"已切换到播放列表: {filepath}")
        return {"success": True, "message": "已切换播放列表"}

    def delete_playlist(self, filepath: str) -> Dict[str, Any]:
        """
        删除播放列表

        参数:
            filepath: 播放列表文件路径

        返回:
            dict: 操作结果
        """
        if not os.path.exists(filepath):
            return {"success": False, "message": "播放列表文件不存在"}

        # 不允许删除默认播放列表
        default_path = os.path.join(DATA_DIR, 'playlist.json')
        if os.path.abspath(filepath) == os.path.abspath(default_path):
            return {"success": False, "message": "不能删除默认播放列表"}

        os.remove(filepath)
        logger.info(f"播放列表已删除: {filepath}")

        # 如果删除的是当前播放列表，切回默认
        if os.path.abspath(filepath) == os.path.abspath(self._current_playlist_path):
            self._current_playlist_path = default_path

        return {"success": True, "message": "播放列表已删除"}

    def rename_playlist(self, filepath: str, new_name: str) -> Dict[str, Any]:
        """
        重命名播放列表

        参数:
            filepath: 播放列表文件路径
            new_name: 新名称

        返回:
            dict: 操作结果
        """
        if not os.path.exists(filepath):
            return {"success": False, "message": "播放列表文件不存在"}

        safe_name = re.sub(r'[\\/:*?"<>|]', '_', new_name.strip())
        new_filename = safe_name + '.json'
        new_dir = os.path.dirname(filepath)
        new_filepath = os.path.join(new_dir, new_filename)

        if os.path.exists(new_filepath):
            return {"success": False, "message": "目标名称已存在"}

        os.rename(filepath, new_filepath)
        logger.info(f"播放列表已重命名: {filepath} -> {new_filepath}")

        if os.path.abspath(filepath) == os.path.abspath(self._current_playlist_path):
            self._current_playlist_path = new_filepath

        return {"success": True, "message": "播放列表已重命名", "filepath": new_filepath}

    # ===== 扫描文件夹 =====

    def scan_music_folder(self) -> Dict[str, Any]:
        """
        扫描音乐目录，将新发现的歌曲添加到当前播放列表

        返回:
            dict: 扫描结果（新增数量、总数量）
        """
        music_files = self._get_all_music_files()
        data = self._read_playlist_file(self._current_playlist_path)
        playlist = data.get('playlist', [])

        existing = set()
        for item in playlist:
            if isinstance(item, dict):
                existing.add(item.get('filename', ''))
            elif isinstance(item, str):
                existing.add(item)

        added = 0
        for filename, filepath in music_files.items():
            if filename not in existing:
                title = os.path.splitext(filename)[0]
                entry = {
                    "filename": filename,
                    "title": title,
                    "author": "",
                    "path": filepath,
                }
                playlist.append(entry)
                added += 1

        if added > 0:
            data['playlist'] = playlist
            self._write_playlist_file(self._current_playlist_path, data)

        logger.info(f"扫描文件夹完成: 新增 {added} 首，共 {len(playlist)} 首")
        return {"success": True, "added": added, "total": len(playlist)}

    # ===== 收藏管理 =====

    FAVORITES_PLAYLIST_NAME = "我喜欢"
    """收藏列表的播放列表名称"""

    def _get_favorites_path(self) -> str:
        """获取收藏列表文件路径"""
        return os.path.join(PLAYLISTS_DIR, f'{self.FAVORITES_PLAYLIST_NAME}.json')

    def get_favorites(self) -> List[Dict[str, Any]]:
        """
        获取收藏列表（完整歌曲数据）

        返回:
            list: 收藏的歌曲列表，与播放列表格式一致
        """
        fav_path = self._get_favorites_path()
        data = self._read_playlist_file(fav_path)
        # 兼容旧格式：如果 playlist 为空但 songs 有数据，则迁移
        playlist = data.get('playlist', [])
        old_songs = data.get('songs', [])
        if not playlist and old_songs:
            playlist = [{"filename": s, "title": os.path.splitext(s)[0]} for s in old_songs]
            data['playlist'] = playlist
            data.pop('songs', None)
            self._write_playlist_file(fav_path, data)
        songs = []

        for item in playlist:
            if isinstance(item, str):
                item = {"filename": item, "title": os.path.splitext(item)[0]}
            song = self._build_song_entry(item)
            if song:
                song['is_favorite'] = True
                songs.append(song)

        return songs

    def _is_favorite(self, filename: str) -> bool:
        """检查歌曲是否已收藏"""
        fav_path = self._get_favorites_path()
        data = self._read_playlist_file(fav_path)
        # 兼容旧格式：检查 playlist 和 songs 两个字段
        playlist = data.get('playlist', [])
        old_songs = data.get('songs', [])
        for item in playlist:
            if isinstance(item, dict) and item.get('filename') == filename:
                return True
            if isinstance(item, str) and item == filename:
                return True
        return filename in old_songs

    def toggle_favorite(self, filename: str) -> Dict[str, Any]:
        """
        切换歌曲收藏状态

        使用标准播放列表格式存储收藏歌曲，支持完整歌曲信息。

        参数:
            filename: 歌曲文件名

        返回:
            dict: {"success": bool, "added": bool, "message": str}
        """
        fav_path = self._get_favorites_path()
        data = self._read_playlist_file(fav_path)
        # 兼容旧格式：迁移旧数据
        old_songs = data.get('songs', [])
        if old_songs:
            playlist = data.get('playlist', [])
            for s in old_songs:
                if s not in [p.get('filename') if isinstance(p, dict) else p for p in playlist]:
                    playlist.append({"filename": s, "title": os.path.splitext(s)[0]})
            data['playlist'] = playlist
            data.pop('songs', None)
            self._write_playlist_file(fav_path, data)

        playlist = data.get('playlist', [])

        # 查找是否已收藏
        existing_entry = None
        existing_index = -1
        for i, item in enumerate(playlist):
            if isinstance(item, dict) and item.get('filename') == filename:
                existing_entry = item
                existing_index = i
                break
            if isinstance(item, str) and item == filename:
                existing_index = i
                break

        if existing_index >= 0:
            # 取消收藏：从列表中移除
            playlist.pop(existing_index)
            added = False
            message = "已取消收藏"
            logger.info(f"取消收藏: {filename}")
        else:
            # 添加收藏：从当前播放列表获取完整歌曲信息
            curr_data = self._read_playlist_file(self._current_playlist_path)
            curr_playlist = curr_data.get('playlist', [])
            full_entry = None

            for item in curr_playlist:
                if isinstance(item, dict) and item.get('filename') == filename:
                    full_entry = dict(item)
                    break

            if full_entry is None:
                # 尝试从 Songs 目录获取信息
                filepath = self._find_music_file(filename)
                full_entry = {
                    "filename": filename,
                    "title": os.path.splitext(filename)[0],
                    "author": "",
                    "path": filepath,
                }

            playlist.append(full_entry)
            added = True
            message = "已收藏"
            logger.info(f"添加收藏: {filename}")

        data['playlist'] = playlist
        self._write_playlist_file(fav_path, data)
        return {"success": True, "added": added, "message": message}

    def add_to_playlist(self, filename: str, title: str = "", artist: str = "") -> None:
        """将歌曲添加到当前播放列表（兼容旧接口）"""
        self.add_song(
            filename=filename,
            title=title or os.path.splitext(filename)[0],
            artist=artist,
        )

    def remove_from_favorites(self, filename: str) -> Dict[str, Any]:
        """
        从收藏列表移除歌曲（与 toggle_favorite 取消收藏功能相同）

        参数:
            filename: 歌曲文件名

        返回:
            dict: 操作结果
        """
        data = self._read_playlist_file(self._get_favorites_path())
        playlist = data.get('playlist', [])
        original_len = len(playlist)

        data['playlist'] = [item for item in playlist
                            if (isinstance(item, dict) and item.get('filename') != filename)
                            or (isinstance(item, str) and item != filename)]

        if len(data['playlist']) == original_len:
            return {"success": False, "message": "未找到该收藏歌曲"}

        self._write_playlist_file(self._get_favorites_path(), data)
        logger.info(f"歌曲已从收藏列表移除: {filename}")
        return {"success": True, "message": "已取消收藏"}