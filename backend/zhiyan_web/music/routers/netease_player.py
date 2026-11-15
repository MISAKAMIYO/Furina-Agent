"""
网易云音乐播放器路由
提供扫码登录、歌单浏览、在线搜索和播放功能

端点:
- GET  /api/netease/status          - 检查登录状态
- GET  /api/netease/login/qr        - 获取二维码 unikey
- GET  /api/netease/login/check     - 轮询登录状态
- POST /api/netease/logout          - 退出登录
- GET  /api/netease/playlists       - 获取用户歌单列表
- GET  /api/netease/playlist/{id}   - 获取歌单歌曲
- GET  /api/netease/search          - 搜索歌曲
- GET  /api/netease/song/url/{id}   - 获取歌曲播放URL（含音源降级）
- GET  /api/netease/song/detail/{id} - 获取歌曲详情
- GET  /api/netease/song/lyrics/{id} - 获取歌曲歌词（含多源降级）
- GET  /api/netease/recommend       - 获取每日推荐
"""
from __future__ import annotations

import logging
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from zhiyan_web.music.services.netease_weapi import netease_weapi
from zhiyan_web.music.services.aggregate_music import (
    resolve_netease_playable_url,
    get_netease_extra_by_meting,
    get_netease_lyrics_by_meting,
    get_netease_lyrics_nodejs,
)

logger = logging.getLogger("ZhiYanWeb.music.netease")

router = APIRouter(prefix="/api/netease", tags=["netease"])


@router.get("/status")
async def check_login_status():
    """检查网易云登录状态"""
    try:
        # 尝试加载 Cookie
        if not netease_weapi.session.cookies.get_dict():
            netease_weapi.load_cookies()

        info = await netease_weapi.get_account_info()
        if info and info.get('account'):
            user = info.get('profile', {})
            return {
                "success": True,
                "logged_in": True,
                "user": {
                    "nickname": user.get("nickname", ""),
                    "avatarUrl": user.get("avatarUrl", ""),
                    "userId": user.get("userId", 0),
                }
            }
        return {"success": True, "logged_in": False}
    except Exception as e:
        logger.error(f"检查登录状态失败: {e}")
        return {"success": False, "logged_in": False, "message": str(e)}


@router.get("/login/qr")
async def get_qr_login():
    """获取二维码登录信息"""
    try:
        unikey = await netease_weapi.get_qr_unikey()
        if not unikey:
            return {"success": False, "message": "获取二维码失败"}
        return {
            "success": True,
            "unikey": unikey,
            "qr_url": f"https://music.163.com/login?codekey={unikey}",
        }
    except Exception as e:
        logger.error(f"获取二维码失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/login/check")
async def check_qr_login(
    key: str = Query(..., description="二维码 unikey"),
):
    """轮询检查二维码登录状态"""
    try:
        code, message = await netease_weapi.check_qr_login(key)
        result = {"success": True, "code": code, "message": message}

        if code == 803:
            # 登录成功，获取用户信息
            info = await netease_weapi.get_account_info()
            if info and info.get('account'):
                user = info.get('profile', {})
                result["user"] = {
                    "nickname": user.get("nickname", ""),
                    "avatarUrl": user.get("avatarUrl", ""),
                    "userId": user.get("userId", 0),
                }
        return result
    except Exception as e:
        logger.error(f"检查登录状态失败: {e}")
        return {"success": False, "code": -1, "message": str(e)}


@router.post("/logout")
async def logout():
    """退出登录"""
    try:
        netease_weapi.clear_cookies()
        return {"success": True, "message": "已退出登录"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.get("/playlists")
async def get_user_playlists(
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    """获取用户歌单列表"""
    try:
        # 先获取用户信息
        info = await netease_weapi.get_account_info()
        if not info or not info.get('account'):
            return {"success": False, "message": "未登录"}

        uid = info.get('profile', {}).get('userId', 0)
        if not uid:
            return {"success": False, "message": "无法获取用户ID"}

        result = await netease_weapi.get_user_playlists(uid, offset, limit)
        if result and 'playlist' in result:
            playlists = []
            for pl in result['playlist']:
                playlists.append({
                    "id": pl.get('id'),
                    "name": pl.get('name'),
                    "coverImgUrl": pl.get('coverImgUrl', ''),
                    "trackCount": pl.get('trackCount', 0),
                    "playCount": pl.get('playCount', 0),
                    "description": pl.get('description', '') or '',
                    "creator": pl.get('creator', {}).get('nickname', ''),
                })
            return {
                "success": True,
                "playlists": playlists,
                "total": result.get('count', len(playlists)),
            }
        return {"success": False, "message": "获取歌单失败"}
    except Exception as e:
        logger.error(f"获取歌单失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/playlist/{playlist_id}")
async def get_playlist_songs(
    playlist_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """获取歌单中的歌曲"""
    try:
        result = await netease_weapi.get_playlist_detail(playlist_id, offset, limit)
        if result and 'playlist' in result:
            pl = result['playlist']
            tracks = pl.get('tracks', [])
            songs = []
            for t in tracks:
                artists = ', '.join(a.get('name', '') for a in t.get('artists', t.get('ar', [])))
                album = t.get('album', t.get('al', {}))
                songs.append({
                    "id": t.get('id'),
                    "name": t.get('name'),
                    "artists": artists,
                    "album": album.get('name', '') if isinstance(album, dict) else '',
                    "albumCover": album.get('picUrl', '') if isinstance(album, dict) else '',
                    "duration": t.get('duration', t.get('dt', 0)) // 1000,
                    "artistId": (t.get('artists', t.get('ar', [{}]))[0] or {}).get('id', 0),
                })
            return {
                "success": True,
                "songs": songs,
                "total": pl.get('trackCount', len(songs)),
                "playlist_name": pl.get('name', ''),
                "playlist_cover": pl.get('coverImgUrl', ''),
            }
        return {"success": False, "message": "获取歌单详情失败"}
    except Exception as e:
        logger.error(f"获取歌单歌曲失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/search")
async def search_songs(
    keyword: str = Query(..., description="搜索关键词"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """搜索歌曲"""
    try:
        result = await netease_weapi.search(keyword, limit, offset)
        if result and 'songs' in result:
            songs = []
            for s in result['songs']:
                artists = ', '.join(a.get('name', '') for a in s.get('artists', s.get('ar', [])))
                album = s.get('album', s.get('al', {}))
                songs.append({
                    "id": s.get('id'),
                    "name": s.get('name'),
                    "artists": artists,
                    "album": album.get('name', '') if isinstance(album, dict) else '',
                    "albumCover": album.get('picUrl', '') if isinstance(album, dict) else '',
                    "duration": s.get('duration', s.get('dt', 0)) // 1000,
                })
            return {
                "success": True,
                "songs": songs,
                "total": result.get('songCount', len(songs)),
            }
        return {"success": True, "songs": [], "total": 0}
    except Exception as e:
        logger.error(f"搜索歌曲失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/song/url/{song_id}")
async def get_song_url(
    song_id: int,
    level: str = Query("exhigh", description="音质: standard/exhigh/lossless/hires"),
):
    """
    获取歌曲播放 URL（音源降级链，移植自 QQ 音乐插件）:
        1. 官方 EAPI (enhance/player/url/v1)
        2. 聚合平台 (txqq.pro, filter=id)
        3. meting API (qijieya.cn)
        4. 网易云 NodeJS API
    """
    try:
        url_info = await netease_weapi.get_song_url(song_id, level)
        if url_info and url_info.get('url'):
            return {
                "success": True,
                "url": url_info['url'],
                "type": url_info.get('type', 'mp3'),
                "level": url_info.get('level', level),
                "encodeType": url_info.get('encodeType', 'mp3'),
            }
        # 降级尝试低音质
        if level != "standard":
            url_info = await netease_weapi.get_song_url(song_id, "standard")
            if url_info and url_info.get('url'):
                return {
                    "success": True,
                    "url": url_info['url'],
                    "type": url_info.get('type', 'mp3'),
                    "level": "standard",
                    "encodeType": url_info.get('encodeType', 'mp3'),
                }

        # 官方接口无法播放（VIP/版权）时，走降级解析链（聚合 -> meting -> nodejs）
        fallback_url = await resolve_netease_playable_url(song_id)
        if fallback_url:
            logger.info(f"网易云音源降级成功 (song_id={song_id})")
            return {
                "success": True,
                "url": fallback_url,
                "type": "mp3",
                "level": "fallback",
                "encodeType": "mp3",
                "fallback": True,
            }

        return {"success": False, "message": "无法获取播放地址（歌曲可能受版权保护）"}
    except Exception as e:
        logger.error(f"获取歌曲URL失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/song/detail/{song_id}")
async def get_song_detail(song_id: int):
    """获取歌曲详情"""
    try:
        result = await netease_weapi.get_song_detail(song_id)
        if result:
            artists = ', '.join(a.get('name', '') for a in result.get('artists', result.get('ar', [])))
            album = result.get('album', result.get('al', {}))
            return {
                "success": True,
                "song": {
                    "id": result.get('id'),
                    "name": result.get('name'),
                    "artists": artists,
                    "album": album.get('name', '') if isinstance(album, dict) else '',
                    "albumCover": album.get('picUrl', '') if isinstance(album, dict) else '',
                    "duration": result.get('duration', result.get('dt', 0)) // 1000,
                }
            }
        return {"success": False, "message": "歌曲不存在"}
    except Exception as e:
        logger.error(f"获取歌曲详情失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/song/lyrics/{song_id}")
async def get_song_lyrics(song_id: int):
    """
    获取歌曲歌词（多源降级，移植自 QQ 音乐插件）:
        1. 官方 EAPI (song/lyric)
        2. meting API (qijieya.cn)
        3. 网易云 NodeJS API
    """
    try:
        # 1. 官方 EAPI
        try:
            lyrics = await netease_weapi.get_lyrics(song_id)
            if lyrics:
                return {"success": True, "lyrics": lyrics, "source": "official"}
        except Exception as e:
            logger.warning(f"官方歌词获取失败: {e}")

        # 2. meting API
        try:
            lyrics = await get_netease_lyrics_by_meting(song_id)
            if lyrics:
                return {"success": True, "lyrics": lyrics, "source": "meting"}
        except Exception as e:
            logger.warning(f"meting 歌词获取失败: {e}")

        # 3. NodeJS API
        try:
            lyrics = await get_netease_lyrics_nodejs(song_id)
            if lyrics:
                return {"success": True, "lyrics": lyrics, "source": "nodejs"}
        except Exception as e:
            logger.warning(f"NodeJS 歌词获取失败: {e}")

        return {"success": False, "message": "无法获取歌词"}
    except Exception as e:
        logger.error(f"获取歌词失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/recommend")
async def get_recommend_songs():
    """获取每日推荐歌曲"""
    try:
        result = await netease_weapi.get_recommend_songs()
        if result and 'data' in result and 'dailySongs' in result.get('data', {}):
            songs = []
            for s in result['data']['dailySongs']:
                artists = ', '.join(a.get('name', '') for a in s.get('artists', s.get('ar', [])))
                album = s.get('album', s.get('al', {}))
                songs.append({
                    "id": s.get('id'),
                    "name": s.get('name'),
                    "artists": artists,
                    "album": album.get('name', '') if isinstance(album, dict) else '',
                    "albumCover": album.get('picUrl', '') if isinstance(album, dict) else '',
                    "duration": s.get('duration', s.get('dt', 0)) // 1000,
                    "reason": s.get('reason', ''),
                })
            return {"success": True, "songs": songs}
        return {"success": False, "message": "获取推荐失败"}
    except Exception as e:
        logger.error(f"获取推荐失败: {e}")
        return {"success": False, "message": str(e)}