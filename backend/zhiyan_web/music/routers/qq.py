"""QQ 音乐接入（搜索 / 播放代理）

搜索复用 MusicDownloader._search_qq（c.y.qq.com client_search_cp），
播放用 vkey 接口拿直链后由后端代理（QQ 直链校验 Referer，浏览器直连会 403）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from zhiyan_web.music.services.music_downloader import MusicDownloader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/qq", tags=["qq-music"])

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _duration_seconds(raw: Any) -> int:
    """"3:58" / 238 -> 秒"""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw)
    if ":" in text:
        try:
            m, s = text.split(":", 1)
            return int(m) * 60 + int(s)
        except Exception:
            return 0
    try:
        return int(float(text))
    except Exception:
        return 0


@router.get("/search")
async def qq_search(q: str = Query("", description="关键词"), limit: int = Query(20, ge=1, le=50)):
    """搜索 QQ 音乐"""
    keyword = (q or "").strip()
    if not keyword:
        return {"ok": True, "songs": [], "count": 0}
    try:
        results = await MusicDownloader._search_qq(keyword)
    except Exception as e:
        logger.warning(f"QQ 搜索失败: {e}")
        return {"ok": False, "error": f"搜索失败: {e}", "songs": []}

    songs: List[Dict[str, Any]] = []
    for item in (results or [])[:limit]:
        songs.append({
            "source": "qq",
            "mid": item.get("mid") or "",
            "id": item.get("id") or "",
            "title": item.get("title") or item.get("name") or "",
            "artist": item.get("author") or "",
            "album": item.get("album") or "",
            "cover": item.get("pic") or "",
            "duration": _duration_seconds(item.get("duration")),
        })
    return {"ok": True, "songs": songs, "count": len(songs)}


@router.get("/url")
async def qq_url(mid: str = Query(..., description="songmid")):
    """解析 QQ 音乐播放直链"""
    if not mid:
        raise HTTPException(status_code=400, detail="缺少 songmid")
    try:
        url = await MusicDownloader._get_qq_download_url({"mid": mid})
    except Exception as e:
        logger.warning(f"QQ 取直链失败: {e}")
        return {"ok": False, "error": f"解析失败: {e}"}
    if not url:
        return {"ok": False, "error": "没有可用地址（可能需要绿钻或版权受限）"}
    return {"ok": True, "url": url}


@router.get("/stream")
async def qq_stream(mid: str = Query(..., description="songmid"), request: Request = None):
    """代理播放 QQ 音乐（带 Referer）"""
    if not mid:
        raise HTTPException(status_code=400, detail="缺少 songmid")
    try:
        url = await MusicDownloader._get_qq_download_url({"mid": mid})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"解析失败: {e}")
    if not url:
        raise HTTPException(status_code=404, detail="没有可用地址（可能需要绿钻或版权受限）")

    headers = {"User-Agent": _UA, "Referer": "https://y.qq.com/"}
    range_header = request.headers.get("range") if request is not None else None
    if range_header:
        headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True)
    try:
        resp = await client.get(url, headers=headers)
    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"拉取音频失败: {e}")
    finally:
        pass

    try:
        if resp.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"上游返回 {resp.status_code}")
        media_type = resp.headers.get("content-type", "audio/mpeg")
        if "octet-stream" in media_type or not media_type.startswith("audio"):
            media_type = "audio/mpeg"
        out_headers = {}
        for h in ("content-range", "accept-ranges", "content-length"):
            if resp.headers.get(h):
                out_headers[h.title()] = resp.headers[h]
        return Response(
            content=resp.content,
            status_code=resp.status_code if resp.status_code in (200, 206) else 200,
            media_type=media_type,
            headers=out_headers,
        )
    finally:
        await client.aclose()
