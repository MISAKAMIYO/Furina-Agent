"""
音乐下载服务
负责从主流音乐平台搜索和下载音乐

支持的平台:
- QQ音乐
- 网易云音乐
- B站音乐

核心功能:
- 搜索音乐（调用真实API）
- 下载音乐（通过WebSocket传输进度）
- 下载封面图片
- 添加到播放列表
"""
from __future__ import annotations

import os
import re
import asyncio
import json
import logging
import urllib.request
from typing import Dict, Any, Optional
from urllib.parse import quote

import httpx

from zhiyan_web.config.settings import SONGS_DIR, COVERS_DIR, PLAYLISTS_DIR
from zhiyan_web.config import get_settings
from zhiyan_web.music.services.playlist_manager import PlaylistManager
from zhiyan_web.music.services.aggregate_music import (
    multi_platform_search,
    resolve_song_by_id,
    resolve_netease_playable_url,
    is_audio_url,
)

logger = logging.getLogger("ZhiYanWeb")


class MusicDownloader:
    """
    音乐下载器

    提供多平台音乐搜索和下载功能
    """

    # 共享 httpx 异步客户端
    _client: Optional[httpx.AsyncClient] = None

    @classmethod
    def _get_client(cls) -> httpx.AsyncClient:
        """获取或创建共享的 httpx 异步客户端"""
        if cls._client is None or cls._client.is_closed:
            cls._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=10.0),
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'application/json, text/plain, */*',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
                },
                follow_redirects=True
            )
        return cls._client

    @staticmethod
    async def search(source: str, query: str, quality: str = "standard") -> Dict[str, Any]:
        """
        搜索音乐（官方 API 优先，失败时自动降级到多平台聚合搜索）

        参数:
            source: 音乐平台（qq, netease, kugou, kuwo, bilibili, ...）
            query: 搜索关键词
            quality: 音质（standard, high, lossless）

        返回:
            Dict[str, Any]: 搜索结果，包含 success, results, count 字段
        """
        if not query:
            return {"success": True, "results": [], "count": 0}

        try:
            source = str(source or "qq").lower().strip()
            results = []

            # 1. 官方 API 优先
            try:
                if source == "netease":
                    results = await MusicDownloader._search_netease(query)
                elif source == "qq":
                    results = await MusicDownloader._search_qq(query)
                elif source == "bilibili":
                    results = await MusicDownloader._search_bilibili(query)
                else:
                    # 酷狗/酷我等平台：直接走聚合搜索
                    results = []
            except Exception as e:
                logger.warning(f"官方搜索异常: {source}, {e}")

            # 2. 官方无结果时降级到聚合搜索
            #    注意: B 站不在聚合平台列表，且其下载流程依赖 B 站专用接口，
            #    故 B 站保持官方-only，不参与聚合降级，维持原始行为。
            if not results and source != "bilibili":
                results = await MusicDownloader._search_aggregate(source, query)

            logger.info(f"音乐搜索完成: source={source}, query={query}, count={len(results)}")
            return {"success": True, "results": results, "count": len(results)}
        except Exception as e:
            logger.error(f"音乐搜索失败: source={source}, query={query}, error={e}")
            return {"success": False, "results": [], "count": 0, "error": str(e)}

    @staticmethod
    async def _search_aggregate(source: str, query: str, limit: int = 20) -> list:
        """
        通过多平台聚合搜索（txqq.pro，移植自 QQ 音乐插件）搜索歌曲

        返回的歌曲自带可直接播放/下载的 url，无需再解析音源。
        """
        try:
            agg_songs = await multi_platform_search(query, source, limit=limit)
        except Exception as e:
            logger.warning(f"聚合搜索异常: {e}")
            return []

        results = []
        for s in agg_songs:
            direct_url = s.get("url", "") or s.get("link", "")
            if not is_audio_url(direct_url):
                direct_url = ""
            results.append({
                "id": s.get("id", ""),
                "mid": s.get("id", ""),
                "title": s.get("title", ""),
                "author": s.get("author", ""),
                "duration": MusicDownloader._format_duration(s.get("duration", 0)),
                "pic": s.get("pic", ""),
                "source": source,
                "album": s.get("album", ""),
                "url": direct_url,  # 直接音源 URL（播放器/下载器可直接使用）
                "link": direct_url,
                "lyrics": s.get("lyrics", ""),
            })
        return results

    @staticmethod
    def _format_duration(seconds) -> str:
        """将秒/毫秒时长格式化为 mm:ss"""
        try:
            seconds = int(seconds)
            if seconds > 10000:  # 毫秒 -> 秒
                seconds = seconds // 1000
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}:{secs:02d}"
        except (TypeError, ValueError):
            return ""


    @staticmethod
    async def _search_netease(query: str) -> list:
        """
        从网易云音乐搜索歌曲（使用官方API）

        参数:
            query: 搜索关键词

        返回:
            list: 歌曲列表
        """
        client = MusicDownloader._get_client()

        # 使用官方API（无需加密）
        search_url = f"https://music.163.com/api/search/get/web?s={quote(query)}&type=1&limit=20&offset=0"

        results = []
        try:
            resp = await client.get(search_url, headers={
                'Referer': 'https://music.163.com/',
                'Origin': 'https://music.163.com'
            })

            if resp.status_code != 200:
                return results

            data = resp.json()
            if 'result' in data and 'songs' in data['result']:
                for song in data['result']['songs']:
                    artists = ', '.join(a.get('name', '') for a in song.get('artists', []))
                    duration_sec = song.get('duration', 0) // 1000
                    minutes = duration_sec // 60
                    seconds = duration_sec % 60

                    pic = ""
                    if song.get('album') and song['album'].get('picUrl'):
                        pic = song['album']['picUrl']
                    elif song.get('album') and song['album'].get('artist') and \
                            song['album']['artist'].get('img1v1Url'):
                        pic = song['album']['artist']['img1v1Url']

                    results.append({
                        "id": str(song.get('id', '')),
                        "title": song.get('name', ''),
                        "author": artists,
                        "duration": f"{minutes}:{seconds:02d}",
                        "pic": pic,
                        "source": "netease",
                        "album": song.get('album', {}).get('name', '') if song.get('album') else ''
                    })
        except Exception as e:
            logger.warning(f"网易云搜索失败: {e}")

        return results

    @staticmethod
    async def _search_qq(query: str) -> list:
        """
        从QQ音乐搜索歌曲

        参数:
            query: 搜索关键词

        返回:
            list: 歌曲列表
        """
        client = MusicDownloader._get_client()
        search_url = (
            "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
            f"?ct=24&qqmusic_ver=1001&t=0&p=1&n=20&w={quote(query)}"
        )

        results = []
        try:
            resp = await client.get(search_url, headers={
                'Referer': 'https://y.qq.com/',
                'Origin': 'https://y.qq.com'
            })

            if resp.status_code != 200:
                return results

            text = resp.text
            if '{' in text and '}' in text:
                json_str = text[text.index('{'):text.rindex('}') + 1]
                data = json.loads(json_str)

                if 'data' in data and 'song' in data['data'] and 'list' in data['data']['song']:
                    for song in data['data']['song']['list']:
                        singers = ', '.join(s.get('name', '') for s in song.get('singer', []))
                        duration_sec = song.get('interval', 0)
                        minutes = duration_sec // 60
                        seconds = duration_sec % 60

                        pic = ""
                        if song.get('albummid'):
                            pic = f"https://y.qq.com/music/photo_new/T002R300x300M000{song['albummid']}.jpg"

                        results.append({
                            "id": str(song.get('songid', '')),
                            "mid": song.get('songmid', ''),
                            "title": song.get('songname', ''),
                            "author": singers,
                            "duration": f"{minutes}:{seconds:02d}",
                            "pic": pic,
                            "source": "qq",
                            "album": song.get('albumname', '')
                        })
        except Exception as e:
            logger.warning(f"QQ音乐搜索失败: {e}")

        return results

    @classmethod
    def _get_bilibili_headers(cls) -> dict:
        """获取带 Cookie 的 Bilibili 请求头"""
        settings = get_settings()
        cookie = settings.get('bilibili_cookie', '')
        headers = {
            'Referer': 'https://www.bilibili.com/',
            'Origin': 'https://www.bilibili.com',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        if cookie:
            headers['Cookie'] = cookie
            logger.info(f"Bilibili 请求已携带 Cookie (长度: {len(cookie)})")
        else:
            logger.info("Bilibili 请求未携带 Cookie，可能触发风控")
        return headers

    @staticmethod
    async def _search_bilibili(query: str) -> list:
        """
        从B站搜索音乐/视频

        参数:
            query: 搜索关键词

        返回:
            list: 歌曲列表
        """
        client = MusicDownloader._get_client()
        search_url = (
            "https://api.bilibili.com/x/web-interface/search/type"
            f"?search_type=video&keyword={quote(query)}&page=1"
        )

        results = []
        try:
            resp = await client.get(search_url, headers=MusicDownloader._get_bilibili_headers())

            if resp.status_code != 200:
                return results

            data = resp.json()
            if data.get('code') == 0 and 'data' in data:
                for video in data['data'].get('result', [])[:20]:
                    pic = video.get('pic', '')
                    if pic and not pic.startswith('http'):
                        pic = 'https:' + pic

                    results.append({
                        "id": video.get('bvid', ''),
                        "title": video.get('title', '').replace('<em class="keyword">', '').replace('</em>', ''),
                        "author": video.get('author', ''),
                        "duration": str(video.get('duration', '')),
                        "pic": pic,
                        "source": "bilibili",
                        "is_bilibili": True
                    })
        except Exception as e:
            logger.warning(f"B站搜索失败: {e}")

        return results

    @staticmethod
    async def download(websocket, source: str, song: dict, quality: str = "standard") -> None:
        """
        下载音乐（通过 WebSocket 传输进度）

        参数:
            websocket: WebSocket 连接对象
            source: 音乐平台
            song: 歌曲信息字典
            quality: 音质（standard, high, lossless）
        """
        try:
            title = song.get('title', '未知歌曲')
            author = song.get('author', '未知艺术家')

            # 发送开始消息
            await websocket.send_json({
                "type": "progress",
                "progress": 0,
                "file_size": 0,
                "downloaded_size": 0,
                "message": f"正在获取 {title} 的下载链接..."
            })

            # 尝试获取真实音频下载链接（优先使用聚合搜索返回的直接音源 URL）
            audio_url = None
            try:
                # 直接音源 URL（聚合搜索/第三方解析直接返回的音频直链）
                direct_url = song.get('url') or song.get('link') or ''
                if is_audio_url(direct_url):
                    audio_url = direct_url
                    if audio_url.startswith('//'):
                        audio_url = 'https:' + audio_url
                    logger.info(f"使用直接音源 URL: {source}")
                else:
                    if source == "netease":
                        audio_url = await MusicDownloader._get_netease_download_url(song)
                    elif source == "qq":
                        audio_url = await MusicDownloader._get_qq_download_url(song)
                    elif source == "bilibili":
                        audio_url = await MusicDownloader._get_bilibili_download_url(song)
                    else:
                        # 酷狗/酷我等：通过聚合平台按 ID 解析音源
                        audio_url = await MusicDownloader._get_aggregate_download_url(song)
            except Exception as e:
                logger.warning(f"获取下载链接异常: {e}")

            if audio_url:
                # 真实下载
                await MusicDownloader._download_from_url(websocket, audio_url, song, source)
            else:
                # 下载链接获取失败
                logger.warning(f"无法获取下载链接: {title} - {author}")
                await websocket.send_json({
                    "type": "error",
                    "message": f"无法获取下载链接，该歌曲可能受版权保护或需要VIP"
                })

        except Exception as e:
            logger.error(f"下载失败: {e}", exc_info=True)
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": f"下载失败: {str(e)}"
                })
            except Exception:
                pass

    @staticmethod
    async def _get_netease_download_url(song: dict) -> Optional[str]:
        """
        获取网易云音乐下载链接（音源降级链，移植自 QQ 音乐插件）:
            1. 官方 EAPI (enhance/player/url)
            2. 聚合平台 (txqq.pro, filter=id)
            3. meting API (qijieya.cn)
            4. 网易云 NodeJS API

        参数:
            song: 歌曲信息

        返回:
            Optional[str]: 下载URL，失败返回None
        """
        song_id = song.get('id', '')
        if not song_id:
            return None

        # 1. 官方 API（可播放时才用）
        try:
            client = MusicDownloader._get_client()
            url = f"https://music.163.com/api/song/enhance/player/url?id={song_id}&ids=[{song_id}]&br=320000"
            resp = await client.get(url, headers={
                'Referer': 'https://music.163.com/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get('data', []) or []:
                    if item and item.get('url'):
                        u = item['url']
                        if u.startswith('//'):
                            u = 'https:' + u
                        if u.startswith('http://') or u.startswith('https://'):
                            return u
        except Exception as e:
            logger.warning(f"网易云官方下载链接失败: {e}")

        # 2-4. 降级解析链（聚合 -> meting -> nodejs）
        return await resolve_netease_playable_url(song_id)

    @staticmethod
    async def _get_qq_download_url(song: dict) -> Optional[str]:
        """
        获取QQ音乐下载链接（参照RAILGUN 5.0方法改进）

        参数:
            song: 歌曲信息

        返回:
            Optional[str]: 下载URL，失败返回None
        """
        try:
            song_mid = song.get('mid', '')
            if not song_mid:
                return None

            client = MusicDownloader._get_client()
            url = "https://u.y.qq.com/cgi-bin/musicu.fcg"

            # 1. 官方 vkey 接口（获取真实下载链接）
            for songtype in [0, 1, 2]:
                try:
                    params = {
                        'format': 'json',
                        'data': json.dumps({
                            "req_0": {
                                "module": "vkey.GetVkeyServer",
                                "method": "CgiGetVkey",
                                "param": {
                                    "guid": "10000",
                                    "songmid": [song_mid],
                                    "songtype": [songtype],
                                    "uin": "0",
                                    "loginflag": 1,
                                    "platform": "20"
                                }
                            }
                        })
                    }
                    resp = await client.get(url, params=params, headers={
                        'Referer': 'https://y.qq.com/'
                    })
                    data = resp.json()

                    req_data = data.get('req_0', {})
                    if req_data.get('code') != 0:
                        continue

                    inner_data = req_data.get('data', {})
                    midurlinfo = inner_data.get('midurlinfo', [])
                    sip_list = inner_data.get('sip', [])
                    sip = sip_list[0] if sip_list else ''

                    # 1. 优先尝试 purl
                    if midurlinfo and midurlinfo[0].get('purl'):
                        result = f"{sip}{midurlinfo[0]['purl']}"
                        if result.startswith('//'):
                            result = 'https:' + result
                        logger.info(f"QQ音乐下载链接获取成功 (purl/songtype={songtype})")
                        return result

                    # 2. 尝试 testfilewifi (purl 为空时的备选方案，验证有效)
                    testfile = inner_data.get("testfilewifi", "")
                    if testfile:
                        result = f"{sip}{testfile}"
                        if result.startswith('//'):
                            result = 'https:' + result
                        logger.info(f"QQ音乐下载链接获取成功 (testfilewifi/songtype={songtype})")
                        return result

                except Exception as e:
                    logger.warning(f"QQ音乐songtype={songtype}尝试失败: {e}")
                    continue

            # 2. 官方失败时降级到聚合平台（txqq.pro，filter=id）解析音源
            agg_url = await MusicDownloader._get_aggregate_download_url(song)
            if agg_url:
                logger.info("QQ音乐下载链接获取成功 (聚合平台)")
                return agg_url

            return None
        except Exception as e:
            logger.warning(f"获取QQ音乐下载链接失败: {e}")
            return None

    @staticmethod
    async def _get_aggregate_download_url(song: dict) -> Optional[str]:
        """
        通过多平台聚合服务（txqq.pro，移植自 QQ 音乐插件）按歌曲 ID 解析音源

        参数:
            song: 歌曲信息（需含 id / mid）

        返回:
            Optional[str]: 可直接播放/下载的音频 URL，失败返回 None
        """
        try:
            platform = song.get('source', '')
            song_id = song.get('mid') or song.get('id', '')
            if not song_id:
                return None
            resolved = await resolve_song_by_id(platform, song_id)
            if resolved and is_audio_url(resolved.get('url', '')):
                return resolved['url']
            # 聚合平台按 ID 找不到时，尝试按歌曲名搜索（粗粒度兜底）
            title = song.get('title', '')
            if title:
                agg_songs = await multi_platform_search(title, platform, limit=1)
                if agg_songs and is_audio_url(agg_songs[0].get('url', '')):
                    return agg_songs[0]['url']
            return None
        except Exception as e:
            logger.warning(f"聚合平台解析音源失败: {e}")
            return None

    @staticmethod
    async def _get_bilibili_download_url(song: dict) -> Optional[str]:
        """
        获取B站视频/音频下载链接

        参数:
            song: 歌曲信息

        返回:
            Optional[str]: 下载URL，失败返回None
        """
        try:
            bvid = song.get('id', '')
            if not bvid:
                return None

            client = MusicDownloader._get_client()
            bilibili_headers = MusicDownloader._get_bilibili_headers()
            # 获取视频信息
            url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            resp = await client.get(url, headers=bilibili_headers)
            data = resp.json()

            if data.get('code') == 0 and data.get('data'):
                cid = data['data'].get('cid', '')
                if cid:
                    # 获取音频流
                    audio_url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=0&fnval=16"
                    resp2 = await client.get(audio_url, headers=bilibili_headers)
                    audio_data = resp2.json()
                    if audio_data.get('code') == 0 and audio_data.get('data'):
                        # 取音频流
                        streams = audio_data['data'].get('dash', {}).get('audio', [])
                        if streams:
                            return streams[0].get('baseUrl') or streams[0].get('base_url', '')
                        # 或者取DURL
                        durl = audio_data['data'].get('durl', [])
                        if durl:
                            return durl[0].get('url', '')
            return None
        except Exception as e:
            logger.warning(f"获取B站下载链接失败: {e}")
            return None

    @staticmethod
    async def _download_from_url(websocket, audio_url: str, song: dict, source: str) -> None:
        """
        从真实 URL 下载音频文件（带超时保护）

        参数:
            websocket: WebSocket 连接
            audio_url: 音频下载 URL
            song: 歌曲信息
            source: 来源平台
        """
        title = song.get('title', '未知歌曲')

        # 发送开始消息
        await websocket.send_json({
            "type": "progress",
            "progress": 0,
            "file_size": 0,
            "downloaded_size": 0,
            "message": f"正在下载 {title}..."
        })

        async def progress_cb(progress: float, total_size: int, downloaded: int, message: str):
            await websocket.send_json({
                "type": "progress",
                "progress": min(99, progress),
                "file_size": total_size or downloaded,
                "downloaded_size": downloaded,
                "message": message,
            })

        result = await MusicDownloader._download_song_to_local(
            audio_url, song, source, progress_cb=progress_cb)

        # 发送完成消息
        await websocket.send_json({
            "type": "completed",
            "filename": result["filename"],
            "filepath": result["filepath"],
            "title": title
        })

    @staticmethod
    async def _download_song_to_local(
        audio_url: str, song: dict, source: str, progress_cb=None
    ) -> dict:
        """
        下载音频文件到本地（供 WebSocket 与 HTTP 接口共用）

        参数:
            audio_url: 音频下载 URL
            song: 歌曲信息
            source: 来源平台
            progress_cb: 进度回调 async (progress, total_size, downloaded, message)

        返回:
            dict: {"filename", "filepath", "title"}

        失败时抛出异常
        """
        client = MusicDownloader._get_client()
        title = song.get('title', '未知歌曲')
        author = song.get('author', '未知艺术家')

        filename = f"{title} - {author}.mp3" if author else f"{title}.mp3"
        filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
        filepath = os.path.join(SONGS_DIR, filename)

        # 确保目录存在
        os.makedirs(SONGS_DIR, exist_ok=True)

        # 流式下载（带超时保护）
        try:
            # 根据来源添加请求头（B站CDN需要Referer和Cookie验证）
            stream_headers = {}
            if source == "bilibili":
                bilibili_headers = MusicDownloader._get_bilibili_headers()
                if 'Referer' in bilibili_headers:
                    stream_headers['Referer'] = bilibili_headers['Referer']
                if 'Cookie' in bilibili_headers:
                    stream_headers['Cookie'] = bilibili_headers['Cookie']
                if 'User-Agent' in bilibili_headers:
                    stream_headers['User-Agent'] = bilibili_headers['User-Agent']

            async with client.stream("GET", audio_url, headers=stream_headers, timeout=60.0) as response:
                if response.status_code != 200:
                    raise Exception(f"下载请求失败，HTTP {response.status_code}")

                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0

                if progress_cb:
                    await progress_cb(0, total_size, 0, f"正在下载 {title}...")

                # 分段写入文件
                with open(filepath, 'wb') as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)

                            if total_size > 0:
                                progress = (downloaded / total_size) * 100
                            else:
                                progress = min(90, (downloaded / (5 * 1024 * 1024)) * 100)

                            if progress_cb:
                                await progress_cb(
                                    progress, total_size, downloaded,
                                    f"正在下载 {title} ({downloaded // 1024}KB)")

        except asyncio.TimeoutError:
            if os.path.exists(filepath):
                os.remove(filepath)
            raise Exception("下载超时，请检查网络连接")
        except Exception as e:
            if os.path.exists(filepath):
                os.remove(filepath)
            raise

        # 验证文件大小
        if not os.path.exists(filepath) or os.path.getsize(filepath) < 1024:
            if os.path.exists(filepath):
                os.remove(filepath)
            raise Exception("下载失败，文件太小或不存在")

        # 下载完成，下载封面
        await MusicDownloader._download_cover(song, filename)

        # 添加到播放列表（写入文件 + 持久化保存）
        playlist_manager = PlaylistManager()
        playlist_manager.add_to_playlist(filename, title=title, artist=author)

        logger.info(f"音乐下载完成: {filename} ({os.path.getsize(filepath)} bytes)")
        return {"filename": filename, "filepath": filepath, "title": title}

    @staticmethod
    async def download_from_url(url: str, title: str = "", artist: str = "",
                                source: str = "netease", pic: str = "") -> Dict[str, Any]:
        """
        直接通过音频 URL 下载到本地（供 POST /api/music/download/from_url 使用）

        参数:
            url: 音频直链
            title: 歌曲标题
            artist: 艺术家
            source: 来源平台
            pic: 封面 URL

        返回:
            dict: {"success", "filename", "filepath", "title", "message"}
        """
        if not url:
            return {"success": False, "message": "缺少音频 URL"}
        song = {
            "title": title or "未知歌曲",
            "author": artist or "未知艺术家",
            "pic": pic or "",
        }
        try:
            result = await MusicDownloader._download_song_to_local(url, song, source)
            return {
                "success": True,
                "filename": result["filename"],
                "filepath": result["filepath"],
                "title": result["title"],
            }
        except Exception as e:
            logger.error(f"直接 URL 下载失败: {e}")
            return {"success": False, "message": str(e)}

    @staticmethod
    async def _download_cover(song: dict, music_filename: str) -> None:
        """
        下载歌曲封面

        参数:
            song: 歌曲信息
            music_filename: 音乐文件名（用于生成封面文件名）
        """
        pic_url = song.get('pic', '')
        if not pic_url:
            return

        if not pic_url.startswith('http'):
            pic_url = 'https:' + pic_url

        try:
            client = MusicDownloader._get_client()
            resp = await client.get(pic_url)
            if resp.status_code == 200:
                cover_ext = '.jpg'
                cover_filename = f"{os.path.splitext(music_filename)[0]}{cover_ext}"
                cover_path = os.path.join(COVERS_DIR, cover_filename)
                os.makedirs(COVERS_DIR, exist_ok=True)
                with open(cover_path, 'wb') as f:
                    f.write(resp.content)
                logger.info(f"封面下载完成: {cover_filename}")
        except Exception as e:
            logger.warning(f"封面下载失败: {e}")