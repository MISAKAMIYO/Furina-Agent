"""酷狗音乐路由：搜索 / 取播放地址 / 歌词 / 音频代理

全部用同步实现（FastAPI 会丢到线程池执行）：酷狗的 httpx.AsyncClient 在部分
Windows/事件循环组合下会出现请求挂起，同步客户端更稳。
音频必须由后端代理：酷狗 CDN 会校验 Referer/UA，直接给前端 URL 常常 403；
代理同时透传 Range 以支持拖动进度条。
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from zhiyan_web.music.services import kugou_webapi as kg

router = APIRouter(prefix="/api/kugou", tags=["kugou"])
logger = logging.getLogger("ZhiYanWeb.Kugou")

_STREAM_HEADERS = {
    "User-Agent": kg.USER_AGENT,
    "Referer": "https://www.kugou.com/",
}


@router.get("/search")
def kugou_search(q: str = "", page: int = 1, size: int = 30):
    """按关键字搜索酷狗单曲"""
    if not (q or "").strip():
        return {"success": False, "error": "缺少关键字", "songs": []}
    songs = kg.search(q, page, size)
    return {"success": True, "songs": songs, "count": len(songs)}


@router.get("/url")
def kugou_url(hash: str = "", album_id: str = "0", quality: str = "128"):
    """获取某首歌的播放地址（付费/会员歌曲返回 ok=false 与原因）"""
    if not hash:
        raise HTTPException(status_code=400, detail="缺少 hash")
    return kg.get_play_url(hash, album_id, quality)


@router.get("/lyrics")
def kugou_lyrics(title: str = "", artist: str = ""):
    """按歌名/歌手从酷狗歌词库取歌词（返回解析好的逐行结构）"""
    if not (title or "").strip():
        return {"success": False, "error": "缺少歌名", "lyrics": []}
    text = kg.search_lyrics(title, artist)
    if not text:
        return {"success": False, "error": "没有找到歌词", "lyrics": []}
    try:
        from zhiyan_web.music.services.lyrics_parser import LyricsParser
        lines = LyricsParser.parse_lrc_text(text)
    except Exception as e:
        logger.warning(f"酷狗歌词解析失败: {e}")
        return {"success": False, "error": str(e), "lyrics": []}
    return {"success": bool(lines), "source": "kugou", "lyrics": lines, "count": len(lines)}


@router.get("/stream")
def kugou_stream(request: Request,
                 hash: str = "",
                 album_id: str = "0",
                 quality: str = "128"):
    """代理酷狗音频流（同步 httpx + Range 透传，支持拖动进度条）"""
    if not hash:
        raise HTTPException(status_code=400, detail="缺少 hash")
    info = kg.get_play_url(hash, album_id, quality)
    if not info.get("ok"):
        raise HTTPException(status_code=404, detail=info.get("error") or "无可播放地址")

    headers = dict(_STREAM_HEADERS)
    range_header = request.headers.get("range") or request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True)
    try:
        resp = client.send(client.build_request("GET", info["url"], headers=headers), stream=True)
    except Exception as e:
        client.close()
        raise HTTPException(status_code=502, detail=f"上游连接失败: {e}")

    if resp.status_code >= 400:
        try:
            body = resp.read()
        except Exception:
            body = b""
        resp.close()
        client.close()
        logger.warning(f"酷狗音频上游 {resp.status_code}: {body[:120]}")
        raise HTTPException(status_code=502, detail=f"上游返回 {resp.status_code}")

    def _iter():
        try:
            for chunk in resp.iter_bytes(64 * 1024):
                yield chunk
        finally:
            resp.close()
            client.close()

    out = StreamingResponse(_iter(), status_code=resp.status_code)
    ctype = resp.headers.get("content-type", "")
    if (not ctype) or ("octet-stream" in ctype):
        ctype = "audio/mpeg"
    out.headers["content-type"] = ctype
    for key in ("content-length", "content-range", "accept-ranges", "etag"):
        if key in resp.headers:
            out.headers[key] = resp.headers[key]
    return out

# ==================== 登录 / 歌单 ====================

@router.get("/session")
def kugou_session():
    """当前酷狗登录态"""
    sess = kg.get_session()
    return {"ok": True, "logged_in": kg.is_logged_in(),
            "userid": sess.get("userid", ""), "nickname": sess.get("nickname", "")}


@router.get("/login/qr")
def kugou_login_qr():
    """获取扫码登录二维码（data:image/png;base64 内联图）"""
    return kg.qr_key()


@router.get("/login/qr/check")
def kugou_login_check(key: str = ""):
    """轮询扫码状态：waiting / scanned / confirmed / expired"""
    return kg.qr_check(key)


@router.post("/logout")
def kugou_logout():
    kg.clear_session()
    return {"ok": True, "message": "已退出酷狗登录"}


@router.get("/playlists")
def kugou_playlists(page: int = 1, size: int = 50):
    """登录用户的歌单列表"""
    return kg.user_playlists(page, size)


@router.get("/playlist")
def kugou_playlist(id: str = "", page: int = 1, size: int = 30):
    """歌单内的歌曲"""
    return kg.playlist_songs(id, page, size)
