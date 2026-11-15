"""
音乐播放器路由
提供音乐播放相关的 API 接口

接口列表:
- GET /api/music/playlist    - 获取播放列表
- POST /api/music/playlist/add     - 添加歌曲到播放列表
- POST /api/music/playlist/remove  - 从播放列表移除歌曲
- POST /api/music/playlist/clear   - 清空播放列表
- POST /api/music/playlist/save    - 保存播放列表
- GET /api/music/playlist/list     - 列出所有播放列表
- POST /api/music/playlist/create  - 创建新播放列表
- POST /api/music/playlist/switch  - 切换播放列表
- POST /api/music/playlist/delete  - 删除播放列表
- POST /api/music/playlist/rename  - 重命名播放列表
- POST /api/music/playlist/scan    - 扫描文件夹批量添加歌曲
- POST /api/music/upload           - 上传单个歌曲文件（含元数据提取）
- GET /api/music/favorites   - 获取收藏列表
- POST /api/music/favorite   - 切换收藏状态
- GET /api/music/stream/{filename} - 流式播放音乐
- GET /api/music/cover/{filename}  - 获取封面图片
- GET /api/music/lyrics/{title}    - 获取歌词（自动补全）
- POST /api/music/lyrics/complete  - 强制网络搜索歌词
- GET /api/music/lyrics/local      - 获取本地歌词文件列表
- GET /api/music/lyrics/file/{filename} - 获取歌词文件原始内容
- POST /api/music/lyrics/save      - 保存歌词文件（手动编辑）
"""
from __future__ import annotations

import os
import logging
import asyncio
import shutil
import urllib.parse
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse

from zhiyan_web.config.settings import SONGS_DIR, COVERS_DIR, LYRICS_DIR
from zhiyan_web.music.services.playlist_manager import PlaylistManager
from zhiyan_web.music.services.lyrics_parser import LyricsParser, SOURCE_NAMES
from zhiyan_web.music.services.metadata_extractor import MetadataExtractor

router = APIRouter(prefix="/api/music", tags=["music"])

playlist_manager = PlaylistManager()

logger = logging.getLogger("ZhiYanWeb")

# 支持的音频格式和最大文件大小
SUPPORTED_AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.m4a', '.ogg', '.aac', '.wma'}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_FILE_SIZE_STR = "50MB"


@router.get("/playlist")
async def get_playlist():
    """获取播放列表（完整数据）"""
    songs = playlist_manager.get_songs()
    return {"playlist": songs, "count": len(songs)}


@router.get("/playlist/paginated")
async def get_playlist_paginated(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页数量"),
    search: str = Query("", description="搜索关键词"),
):
    """分页获取播放列表"""
    songs = playlist_manager.get_songs()

    # 搜索过滤
    if search:
        kw = search.lower().strip()
        songs = [s for s in songs
                 if kw in (s.get("title", "") or "").lower()
                 or kw in (s.get("artist", "") or "").lower()
                 or kw in (s.get("filename", "") or "").lower()]

    total = len(songs)
    total_pages = max(1, (total + page_size - 1) // page_size)

    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_songs = songs[start:end]

    return {
        "success": True,
        "songs": page_songs,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.post("/playlist/add")
async def add_to_playlist(data: Dict[str, Any]):
    """添加歌曲到播放列表"""
    filename = data.get("filename")
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    return playlist_manager.add_song(
        filename=filename,
        filepath=data.get("filepath", ""),
        title=data.get("title", ""),
        artist=data.get("artist", ""),
    )


@router.post("/playlist/remove")
async def remove_from_playlist(data: Dict[str, Any]):
    """从播放列表移除歌曲"""
    filename = data.get("filename")
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    return playlist_manager.remove_song(filename)


@router.post("/playlist/clear")
async def clear_playlist():
    """清空播放列表"""
    return playlist_manager.clear_playlist()


@router.post("/playlist/save")
async def save_playlist(data: Dict[str, Any]):
    """保存播放列表"""
    songs = data.get("songs", [])
    return playlist_manager.save_playlist(songs)


@router.get("/playlist/list")
async def list_playlists():
    """列出所有播放列表"""
    playlists = playlist_manager.list_playlists()
    return {"playlists": playlists, "count": len(playlists)}


@router.post("/playlist/create")
async def create_playlist(data: Dict[str, Any]):
    """创建新播放列表"""
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    return playlist_manager.create_playlist(name)


@router.post("/playlist/scan")
async def scan_music_folder():
    """扫描音乐文件夹，批量添加歌曲到播放列表"""
    return playlist_manager.scan_music_folder()


@router.post("/upload")
async def upload_song(file: UploadFile = File(...)):
    """
    上传单个歌曲文件（含元数据提取）

    支持的格式: mp3, wav, flac, m4a, ogg, aac, wma
    最大文件大小: 50MB

    返回:
        dict: 包含歌曲信息和元数据
    """
    # 1. 验证文件类型
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的音频格式: {ext}。支持的格式: {', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}"
        )

    # 2. 读取文件内容
    content = await file.read()
    file_size = len(content)

    # 3. 验证文件大小
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"文件过大 ({file_size / 1024 / 1024:.1f}MB)。最大支持: {MAX_FILE_SIZE_STR}"
        )

    if file_size == 0:
        raise HTTPException(status_code=400, detail="文件为空")

    # 4. 检查是否已存在
    target_path = os.path.join(SONGS_DIR, filename)
    if os.path.exists(target_path):
        # 文件已存在，检查是否已在播放列表中
        existing = playlist_manager.get_playlist_from_file()
        for song in existing:
            if song.get("filename") == filename:
                raise HTTPException(
                    status_code=409,
                    detail=f"歌曲 '{filename}' 已存在于播放列表中"
                )
        # 文件存在但不在播放列表中，直接添加
        logger.info(f"文件已存在但不在播放列表: {filename}")
    else:
        # 5. 保存文件到 Songs 目录
        try:
            with open(target_path, "wb") as f:
                f.write(content)
            logger.info(f"歌曲文件已保存: {target_path} ({file_size} bytes)")
        except Exception as e:
            logger.error(f"保存文件失败: {e}")
            raise HTTPException(status_code=500, detail=f"保存文件失败: {str(e)}")

    # 6. 提取元数据
    metadata = MetadataExtractor.extract(target_path)
    title = metadata.get("title", os.path.splitext(filename)[0])
    artist = metadata.get("artist", "")
    album = metadata.get("album", "")
    duration = metadata.get("duration", 0)
    format_name = metadata.get("format", ext.lstrip(".").upper())

    # 7. 添加到播放列表
    result = playlist_manager.add_song(
        filename=filename,
        filepath=target_path,
        title=title,
        artist=artist,
    )

    # 8. 返回结果
    return {
        "success": True,
        "message": f"歌曲已添加: {title}",
        "song": {
            "id": abs(hash(filename)),
            "filename": filename,
            "title": title,
            "artist": artist,
            "album": album,
            "duration": duration,
            "duration_formatted": MetadataExtractor.format_duration(duration),
            "format": format_name,
            "size": file_size,
            "size_formatted": MetadataExtractor.format_size(file_size),
            "url": f"/api/music/stream/{filename}",
            "added": result.get("added", True),
        }
    }


@router.post("/playlist/switch")
async def switch_playlist(data: Dict[str, Any]):
    """切换播放列表"""
    filepath = data.get("filepath", "")
    if not filepath:
        raise HTTPException(status_code=400, detail="缺少文件路径")
    return playlist_manager.switch_playlist(filepath)


@router.post("/playlist/delete")
async def delete_playlist(data: Dict[str, Any]):
    """删除播放列表"""
    filepath = data.get("filepath", "")
    if not filepath:
        raise HTTPException(status_code=400, detail="缺少文件路径")
    return playlist_manager.delete_playlist(filepath)


@router.post("/playlist/rename")
async def rename_playlist(data: Dict[str, Any]):
    """重命名播放列表"""
    filepath = data.get("filepath", "")
    new_name = data.get("name", "").strip()
    if not filepath or not new_name:
        raise HTTPException(status_code=400, detail="参数不完整")
    return playlist_manager.rename_playlist(filepath, new_name)


@router.get("/favorites")
async def get_favorites():
    """获取收藏列表（完整歌曲数据）"""
    favorite_songs = playlist_manager.get_favorites()
    return {"playlist": favorite_songs, "count": len(favorite_songs)}


@router.post("/favorite")
async def toggle_favorite(data: Dict[str, Any]):
    """切换收藏状态"""
    filename = data.get("filename")
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    return playlist_manager.toggle_favorite(filename)


@router.get("/stream/{filename}")
async def stream_music(filename: str):
    """流式播放音乐（带客户端断开保护）"""
    from zhiyan_web.config.settings import DATA_DIR
    # 旧 ZhiYan Agent 目录兼容
    old_songs_dir = os.path.join(os.path.dirname(DATA_DIR), 'ZhiYan agent', 'data', 'Songs')
    music_search_paths = [
        SONGS_DIR,
        os.path.join(DATA_DIR, 'plugins_data', 'astrbot_plugin_music', 'songs'),
        os.path.join(DATA_DIR, 'Songs'),
        old_songs_dir,
    ]
    
    filepath = ""
    for search_path in music_search_paths:
        if os.path.exists(search_path):
            candidate = os.path.join(search_path, filename)
            if os.path.exists(candidate):
                filepath = candidate
                break
    
    if not filepath:
        raise HTTPException(status_code=404, detail=f"音乐文件不存在: {filename}")
    
    ext = os.path.splitext(filename)[1].lower()
    mime_types = {
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
        '.flac': 'audio/flac',
        '.m4a': 'audio/mp4',
    }
    media_type = mime_types.get(ext, 'audio/mpeg')
    file_size = os.path.getsize(filepath)
    
    async def file_iterator():
        """逐块读取文件，优雅处理客户端断开"""
        try:
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
                    # 主动让出控制权，让事件循环有机会检查取消
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            # 客户端断开连接时正常退出
            logger.debug(f"流式播放被客户端中断: {filename}")
        except GeneratorExit:
            pass
        except Exception as e:
            # 仅记录，不抛出异常
            if "ConnectionResetError" in str(type(e).__name__) or "connection" in str(e).lower():
                logger.debug(f"流式播放客户端断开: {filename}")
            else:
                logger.warning(f"流式播放读取异常 [{filename}]: {e}")
    
    return StreamingResponse(
        file_iterator(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{urllib.parse.quote(filename)}"',
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
        }
    )


@router.get("/cover/{filename}")
async def get_music_cover(filename: str):
    """获取封面图片"""
    filepath = os.path.join(COVERS_DIR, filename)
    
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="封面文件不存在")
    
    return FileResponse(filepath)


# ============= 桌面歌词：当前播放状态（音乐页写入，歌词浮窗读取） =============

_now_playing: Dict[str, Any] = {
    "title": "",
    "artist": "",
    "playing": False,
    "time": 0.0,
    "line": {"text": "", "words": []},
    "next": "",
    "updated_at": 0.0,
}


@router.post("/now-playing")
async def set_now_playing(data: Dict[str, Any]):
    """音乐页上报当前播放状态（供桌面歌词浮窗轮询）"""
    import time as _time
    if not isinstance(data, dict):
        return {"success": False}
    for key in ("title", "artist", "playing", "time", "line", "next", "cover",
                "duration", "mode", "favorite", "volume"):
        if key in data:
            _now_playing[key] = data[key]
    _now_playing["updated_at"] = _time.time()
    return {"success": True}


# ===== 迷你播放器控制命令（主页面轮询执行） =====
_music_commands: list = []
_music_cmd_seq = 0


@router.post("/command")
async def push_music_command(data: Dict[str, Any]):
    """迷你播放器下发命令：play / pause / toggle / next / prev / seek / volume / mode / fav"""
    import time as _time
    global _music_cmd_seq
    if not isinstance(data, dict):
        return {"success": False}
    action = str(data.get("action") or "")
    if not action:
        return {"success": False, "message": "缺少 action"}
    _music_cmd_seq += 1
    _music_commands.append({
        "seq": _music_cmd_seq,
        "action": action,
        "value": float(data.get("value") or 0),
        "ts": _time.time(),
    })
    if len(_music_commands) > 30:
        del _music_commands[:-30]
    return {"success": True, "seq": _music_cmd_seq}


@router.get("/command")
async def get_music_command(since: int = 0):
    """主页面轮询命令；since 之后的新命令按顺序返回"""
    pending = [c for c in _music_commands if c["seq"] > int(since or 0)]
    return {"success": True, "seq": _music_cmd_seq, "commands": pending}


@router.get("/now-playing")
async def get_now_playing():
    """歌词浮窗读取当前播放状态"""
    return {"success": True, **_now_playing}


@router.get("/lyrics/local")
async def get_local_lyrics_list():
    """获取所有本地歌词文件列表"""
    lyrics_list = LyricsParser.get_all_local_lyrics()
    return {"lyrics": lyrics_list, "count": len(lyrics_list)}


@router.get("/lyrics/file/{filename}")
async def get_lyrics_file_content(filename: str):
    """获取指定歌词文件的原始文本内容"""
    content = LyricsParser.get_lyrics_file_content(filename)
    if not content:
        raise HTTPException(status_code=404, detail=f"歌词文件不存在: {filename}")
    return {"filename": filename, "content": content}


@router.post("/lyrics/save")
async def save_lyrics_file(data: dict):
    """
    保存歌词文件（用于手动编辑）
    
    参数:
        data: {"filename": "文件名", "content": "歌词内容"}
    """
    filename = data.get("filename", "")
    content = data.get("content", "")
    
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    if not content:
        raise HTTPException(status_code=400, detail="歌词内容为空")
    
    success = LyricsParser.save_lyrics_file(filename, content)
    if not success:
        raise HTTPException(status_code=500, detail="保存歌词文件失败")
    
    return {"success": True, "filename": filename}


@router.get("/lyrics/{title}")
async def get_lyrics(
    title: str,
    artist: str = "",
    filename: str = "",
):
    """
    获取歌词（仅查本地，不再自动搜索网络）
    
    用户需通过"获取歌词"按钮手动触发网络搜索（POST /api/music/lyrics/search 或 /api/music/lyrics/complete）。
    
    参数:
        title: 歌曲标题
        artist: 艺术家（可选）
        filename: 歌曲文件名（可选，用于精确匹配本地歌词）
    
    返回:
        dict: 包含 lyrics, count, source, source_name 字段
    """
    lyrics, source = await LyricsParser.complete_lyrics(title, artist, filename)
    return {
        "lyrics": lyrics,
        "count": len(lyrics),
        "source": source,
        "source_name": SOURCE_NAMES.get(source, source),
    }


@router.post("/lyrics/search")
async def search_lyrics(data: dict):
    """
    搜索歌词（返回多源搜索结果列表，类似 Furina Agent 对话框）
    
    参数:
        data: {"title": "歌曲名", "artist": "艺术家"}
    
    返回:
        dict: 包含 results 列表，每条结果含 title, artist, lyrics, source
    """
    title = data.get("title", "")
    artist = data.get("artist", "")
    
    if not title:
        raise HTTPException(status_code=400, detail="缺少歌曲标题")
    
    results = await LyricsParser.search_lyrics_multi_source(title, artist)
    return {"results": results, "count": len(results)}


@router.post("/lyrics/complete")
async def complete_lyrics(data: dict):
    """
    歌词补全（强制从网络搜索）
    
    参数:
        data: {"title": "歌曲名", "artist": "艺术家", "filename": "文件名"}
    
    返回:
        dict: 包含 lyrics, count, source, source_name 字段
    """
    title = data.get("title", "")
    artist = data.get("artist", "")
    filename = data.get("filename", "")
    
    if not title:
        raise HTTPException(status_code=400, detail="缺少歌曲标题")
    
    lyrics, source = await LyricsParser.search_lyrics_online(title, artist)
    return {
        "lyrics": lyrics,
        "count": len(lyrics),
        "source": source,
        "source_name": SOURCE_NAMES.get(source, source),
    }
