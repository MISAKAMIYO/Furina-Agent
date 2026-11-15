"""
多平台聚合音乐服务

移植自 QQ 平台音乐插件 astrbot_plugin_music-main 的核心方法：
- SearcherMusic / TXQQMusic：通过 https://music.txqq.pro/ 聚合搜索 13 个音乐平台，
  直接返回可播放/可下载的音频 URL、歌词与封面
- BaseMusicPlayer.fetch_extra / fetch_lyrics：通过 meting API 解析网易云歌曲音源与歌词
- NetEaseMusicNodeJS：通过公开的网易云 NodeJS API 获取音源与歌词

支持的平台 type（txqq.pro）:
qq / netease / kugou / kuwo / baidu / 1ting / migu / lizhi / qingting /
ximalaya / 5singyc / 5singfc / kg

本模块作为后端音乐下载器与网易云播放器的"音源降级链"兜底，用于解决
官方接口对 VIP/版权歌曲返回空 URL 的问题。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from zhiyan_web.config import get_settings

logger = logging.getLogger("ZhiYanWeb.music.aggregate")

# txqq.pro 聚合搜索平台类型 -> 展示名称
PLATFORM_DISPLAY = {
    "qq": "QQ音乐",
    "netease": "网易云音乐",
    "kugou": "酷狗音乐",
    "kuwo": "酷我音乐",
    "baidu": "百度音乐",
    "1ting": "一听音乐",
    "migu": "咪咕音乐",
    "lizhi": "荔枝FM",
    "qingting": "蜻蜓FM",
    "ximalaya": "喜马拉雅",
    "5singyc": "5sing原创",
    "5singfc": "5sing翻唱",
    "kg": "全民K歌",
}

# 兼容别名：前端/后端使用的 source 名 -> txqq.pro type
PLATFORM_ALIAS = {
    "qq": "qq",
    "netease": "netease",
    "wangyi": "netease",
    "kugou": "kugou",
    "kuwo": "kuwo",
    "baidu": "baidu",
    "1ting": "1ting",
    "migu": "migu",
    "lizhi": "lizhi",
    "qingting": "qingting",
    "ximalaya": "ximalaya",
    "5singyc": "5singyc",
    "5singfc": "5singfc",
    "kg": "kg",
}

AGGREGATE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) "
        "Gecko/20100101 Firefox/146.0"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://music.txqq.pro",
    "Referer": "https://music.txqq.pro",
}

METING_API = "https://api.qijieya.cn/meting/"

# 共享的异步 HTTP 客户端
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """获取或创建共享的 httpx 异步客户端"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
            },
            follow_redirects=True,
        )
    return _client


def _aggregate_base_url() -> str:
    """聚合搜索服务地址（支持设置覆盖）"""
    try:
        settings = get_settings()
        music_cfg = settings.get("music") or {}
        return (music_cfg.get("aggregate_base_url") or "https://music.txqq.pro/").rstrip("/") + "/"
    except Exception:
        return "https://music.txqq.pro/"


def _nodejs_base_url() -> str:
    """网易云 NodeJS API 根地址（支持设置覆盖）"""
    try:
        settings = get_settings()
        music_cfg = settings.get("music") or {}
        return music_cfg.get("nodejs_base_url") or "https://163api.qijieya.cn"
    except Exception:
        return "https://163api.qijieya.cn"


def normalize_platform(source: str) -> str:
    """将 source 名规范化到 txqq.pro 平台 type"""
    return PLATFORM_ALIAS.get(str(source or "").lower().strip(), "qq")


# 常见音频直链扩展名
_AUDIO_EXTS = (".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg", ".opus", ".amr", ".wma")
# 音频 CDN 域名特征（用于识别不含扩展名的音频直链）
_AUDIO_MARKERS = (
    "music.163.com/song/media",
    "music.126.net",
    "aqqmusic.tc.qq.com",
    "streamoc.music.tc.qq.com",
    "dl.stream.qqmusic.qq.com",
    "isure.stream.qqmusic.qq.com",
    "tme-audio",
    "kugou.com",
    "kuwo.cn/newh5",
    "wyycdn",
)


def is_audio_url(url: str) -> bool:
    """
    判断 URL 是否为可直接播放/下载的音频直链

    聚合平台（txqq.pro）对不同平台返回不一致：
    - qq/netease 等返回音频直链 (xxx.m4a / xxx.mp3)
    - kuwo 等可能返回播放页 URL (play_detail/xxx)，不可直接下载

    此函数用于过滤掉页面 URL，避免把 HTML 当音频下载。
    """
    if not url:
        return False
    url = str(url).strip()
    if not url.startswith(("http://", "https://", "//")):
        return False
    path = url.split("?")[0].split("#")[0].lower()
    # 1. 音频扩展名
    if path.endswith(_AUDIO_EXTS):
        return True
    # 2. 排除明显的页面 URL
    page_markers = ("/play_detail/", "/song/detail/", "/song?id=", "/playlist/", "/album/")
    if any(m in path for m in page_markers):
        return False
    # 3. CDN 特征标记
    if any(m in url for m in _AUDIO_MARKERS):
        return True
    return False


def _normalize_song(raw: dict) -> Dict[str, Any]:
    """将 txqq.pro 返回的单曲原始数据标准化为通用结构"""
    pic = raw.get("pic") or ""
    audio_url = raw.get("url") or raw.get("link") or ""
    if audio_url.startswith("//"):
        audio_url = "https:" + audio_url
    if pic.startswith("//"):
        pic = "https:" + pic

    # 时长可能是秒也可能是毫秒
    duration = raw.get("duration") or raw.get("interval") or 0
    try:
        duration = int(duration)
        if duration > 10000:
            duration = duration // 1000
    except (TypeError, ValueError):
        duration = 0

    return {
        "id": str(raw.get("songid") or raw.get("id") or ""),
        "title": raw.get("title") or raw.get("name") or "未知歌曲",
        "author": raw.get("author") or raw.get("artists") or "未知艺术家",
        "album": raw.get("album") or raw.get("albumname") or "",
        "duration": duration,
        "pic": pic,
        "cover_url": pic,
        "url": audio_url,
        "link": audio_url,
        "lyrics": raw.get("lrc") or raw.get("lyrics") or "",
        "source": "",
    }


async def multi_platform_search(
    song_name: str, platform_type: str, limit: int = 5
) -> List[Dict[str, Any]]:
    """
    多平台聚合搜索（移植自插件 SearcherMusic/TXQQMusic）

    参数:
        song_name: 搜索关键词
        platform_type: txqq.pro 平台 type（qq/netease/kugou/kuwo/...）
        limit: 返回数量

    返回:
        list: 标准化歌曲列表（含可直接播放的 url / lyrics / cover）
    """
    if not song_name:
        return []
    platform_type = normalize_platform(platform_type)
    client = _get_client()
    try:
        resp = await client.post(
            _aggregate_base_url(),
            data={
                "input": song_name,
                "filter": "name",
                "type": platform_type,
                "page": 1,
            },
            headers=AGGREGATE_HEADERS,
        )
        if resp.status_code != 200:
            logger.warning(f"聚合搜索失败 HTTP {resp.status_code}")
            return []
        try:
            result = resp.json()
        except Exception:
            logger.warning("聚合搜索返回非 JSON 数据")
            return []
        if not isinstance(result, dict):
            return []

        # 兼容插件中 searcher.py（result["songs"]）与 txqq.py（result["data"]）两种返回结构
        raw_songs = []
        if isinstance(result.get("songs"), list):
            raw_songs = result["songs"]
        elif isinstance(result.get("data"), list):
            raw_songs = result["data"]

        songs = []
        for raw in raw_songs[:limit]:
            song = _normalize_song(raw)
            song["source"] = platform_type
            songs.append(song)
        return songs
    except Exception as e:
        logger.warning(f"聚合搜索异常: {e}")
        return []


async def resolve_song_by_id(
    platform_type: str, song_id: str, limit: int = 1
) -> Optional[Dict[str, Any]]:
    """
    通过歌曲 ID 在聚合平台解析音源（filter=id）

    参数:
        platform_type: txqq.pro 平台 type
        song_id: 歌曲 ID

    返回:
        dict | None: 标准化歌曲（含可直接播放的 url），失败返回 None
    """
    if not song_id:
        return None
    platform_type = normalize_platform(platform_type)
    client = _get_client()
    try:
        resp = await client.post(
            _aggregate_base_url(),
            data={
                "input": str(song_id),
                "filter": "id",
                "type": platform_type,
                "page": 1,
            },
            headers=AGGREGATE_HEADERS,
        )
        if resp.status_code != 200:
            return None
        try:
            result = resp.json()
        except Exception:
            return None
        if not isinstance(result, dict):
            return None
        raw_songs = []
        if isinstance(result.get("songs"), list):
            raw_songs = result["songs"]
        elif isinstance(result.get("data"), list):
            raw_songs = result["data"]
        if not raw_songs:
            return None
        song = _normalize_song(raw_songs[0])
        song["source"] = platform_type
        # 过滤掉播放页 URL，仅保留音频直链
        if not is_audio_url(song.get("url", "")):
            song["url"] = ""
            song["link"] = ""
        return song
    except Exception as e:
        logger.warning(f"聚合解析音源失败 (id={song_id}): {e}")
        return None


async def get_netease_extra_by_meting(song_id) -> Optional[Dict[str, Any]]:
    """
    通过 meting API 解析网易云歌曲音源/封面/歌词（移植自插件 BaseMusicPlayer.fetch_extra）

    参数:
        song_id: 网易云歌曲 ID

    返回:
        dict | None: {"url": 播放地址, "pic": 封面, "lyrics": 歌词}
    """
    client = _get_client()
    try:
        resp = await client.get(
            METING_API,
            params={"type": "song", "id": song_id},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            return None
        url = data.get("url") or ""
        if url.startswith("//"):
            url = "https:" + url
        return {
            "url": url,
            "pic": data.get("pic") or "",
            "lyrics": data.get("lrc") or "",
        }
    except Exception as e:
        logger.warning(f"meting 解析网易云音源失败 (id={song_id}): {e}")
        return None


async def get_netease_lyrics_by_meting(song_id) -> Optional[str]:
    """
    通过 meting API 获取网易云歌词（移植自插件 BaseMusicPlayer.fetch_lyrics）
    """
    client = _get_client()
    try:
        resp = await client.get(
            METING_API,
            params={"server": "netease", "type": "lrc", "id": song_id},
        )
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            text = resp.text.strip()
            return text or None
        if isinstance(data, dict):
            return data.get("lyric") or data.get("lrc") or None
        if isinstance(data, str):
            return data.strip() or None
        return None
    except Exception as e:
        logger.warning(f"meting 获取网易云歌词失败 (id={song_id}): {e}")
        return None


async def get_netease_song_url_nodejs(song_id, base_url: str = "") -> Optional[str]:
    """
    通过网易云 NodeJS API 获取播放地址（移植自插件 NetEaseMusicNodeJS.fetch_extra）

    返回的接口结构: {"data": [{"url": "...", ...}]}
    """
    base_url = (base_url or _nodejs_base_url()).rstrip("/")
    client = _get_client()
    try:
        resp = await client.get(f"{base_url}/song/url", params={"id": song_id})
        if resp.status_code != 200:
            return None
        data = resp.json()
        info_list = data.get("data") if isinstance(data, dict) else None
        if info_list:
            url = info_list[0].get("url") if isinstance(info_list[0], dict) else None
            if url:
                return url
        return None
    except Exception as e:
        logger.warning(f"网易云 NodeJS 获取音源失败 (id={song_id}): {e}")
        return None


async def get_netease_lyrics_nodejs(song_id, base_url: str = "") -> Optional[str]:
    """
    通过网易云 NodeJS API 获取歌词（移植自插件 NetEaseMusicNodeJS.fetch_lyrics）

    返回的接口结构: {"lrc": {"lyric": "..."}}
    """
    base_url = (base_url or _nodejs_base_url()).rstrip("/")
    client = _get_client()
    try:
        resp = await client.get(f"{base_url}/lyric", params={"id": song_id})
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("lrc"):
            lyric = data["lrc"].get("lyric")
            if lyric:
                return lyric
        return None
    except Exception as e:
        logger.warning(f"网易云 NodeJS 获取歌词失败 (id={song_id}): {e}")
        return None


async def resolve_netease_playable_url(song_id) -> Optional[str]:
    """
    网易云播放地址降级解析链（供播放器/下载器共用）:
        1. 聚合平台 (txqq.pro, filter=id)
        2. meting API (qijieya.cn)
        3. 网易云 NodeJS API
    """
    song_id = str(song_id)
    # 1. 聚合平台
    try:
        resolved = await resolve_song_by_id("netease", song_id)
        if resolved and is_audio_url(resolved.get("url", "")):
            return resolved["url"]
    except Exception:
        pass
    # 2. meting API
    try:
        extra = await get_netease_extra_by_meting(song_id)
        if extra and is_audio_url(extra.get("url", "")):
            return extra["url"]
    except Exception:
        pass
    # 3. NodeJS API
    try:
        url = await get_netease_song_url_nodejs(song_id)
        if url and is_audio_url(url):
            return url
    except Exception:
        pass
    return None
