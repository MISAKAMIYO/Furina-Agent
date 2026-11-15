"""
音乐下载路由
提供音乐下载相关的 API 接口

接口列表:
- GET  /api/music/download/search    - 搜索音乐
- POST /api/music/download/from_url  - 通过音频直链下载到本地
- WS   /api/music/download/ws        - 音乐下载 WebSocket
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from zhiyan_web.music.services.music_downloader import MusicDownloader

router = APIRouter(prefix="/api/music/download", tags=["music-download"])

logger = logging.getLogger("ZhiYanWeb")


class DownloadFromUrlRequest(BaseModel):
    """通过直链下载请求体"""
    url: str
    title: str = ""
    artist: str = ""
    source: str = "netease"
    pic: str = ""


@router.get("/search")
async def search_music(source: str = "qq", query: str = "", quality: str = "standard"):
    """
    搜索音乐
    
    参数:
        source: 音乐平台（qq, netease, kugou, kuwo, bilibili, ...）
        query: 搜索关键词
        quality: 音质（standard, high, lossless）
    
    返回:
        dict: 搜索结果
    """
    return await MusicDownloader.search(source, query, quality)


@router.post("/from_url")
async def download_from_url(req: DownloadFromUrlRequest):
    """
    通过音频直链下载到本地

    用于修复前端网易云歌曲"添加到本地"功能（GET 无法携带大请求体 / 直链无法走搜索下载）。
    """
    return await MusicDownloader.download_from_url(
        url=req.url,
        title=req.title,
        artist=req.artist,
        source=req.source,
        pic=req.pic,
    )


@router.websocket("/ws")
async def download_websocket(websocket: WebSocket):
    """
    音乐下载 WebSocket
    
    支持的消息类型:
    - download: 开始下载音乐
    """
    await websocket.accept()
    
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            
            if action == "download":
                source = data.get("source", "qq")
                song = data.get("song", {})
                quality = data.get("quality", "standard")
                
                await MusicDownloader.download(websocket, source, song, quality)
    
    except WebSocketDisconnect:
        logger.info("下载 WebSocket 连接断开")
    except Exception as e:
        logger.error(f"下载 WebSocket 错误: {e}")
