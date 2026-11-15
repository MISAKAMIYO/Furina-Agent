"""
网易云音乐 EAPI 接口
使用官方 eapi 加密接口实现扫码登录、歌单、搜索与播放
移植自 ZhiYan Agent 的 netease_api.py
"""
from __future__ import annotations

import json
import hashlib
import logging
import asyncio
import secrets
import os
from typing import Optional, Dict, List, Any, Tuple

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

logger = logging.getLogger("ZhiYanWeb.music.weapi")


class NeteaseWeAPI:
    """网易云音乐 EAPI 封装"""

    EAPI_BASE = "https://music.163.com/eapi"

    def __init__(self, timeout: int = 15, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://music.163.com/",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        # EAPI 接口需要客户端身份 Cookie（pyncm 兼容）
        self.session.cookies.update({
            "os": "pc",
            "appver": "2.7.1.198277",
            "osver": "Microsoft-Windows-10-Professional-build-22631-64bit",
            "deviceId": "pyncm!" + secrets.token_hex(16),
            "NMTID": secrets.token_hex(16),
        })

    # ---- 加密工具 ----

    @staticmethod
    def _eapi_encrypt(url_path: str, data: dict) -> str:
        """EAPI 加密

        返回 hex 编码的加密参数（pyncm 兼容），url_path 需包含 /api 前缀。
        """
        text = json.dumps(data)
        message = f"nobody{url_path}use{text}md5forencrypt"
        digest = hashlib.md5(message.encode('utf-8')).hexdigest()
        params = f"{url_path}-36cd479b6b5-{text}-36cd479b6b5-{digest}"
        key = b"e82ckenh8dichen8"
        cipher = AES.new(key, AES.MODE_ECB)
        padded = pad(params.encode('utf-8'), AES.block_size)
        return cipher.encrypt(padded).hex()

    # ---- 请求 ----

    def _eapi_request(self, eapi_path: str, data: dict) -> Optional[dict]:
        """EAPI 请求

        Args:
            eapi_path: eapi 路径（不含 /api 前缀），如 '/login/qrcode/unikey'
        """
        url = f"{self.EAPI_BASE}{eapi_path}"
        encrypt_path = f"/api{eapi_path}"
        params = self._eapi_encrypt(encrypt_path, data)
        for attempt in range(self.max_retries):
            try:
                resp = self.session.post(url, data={"params": params}, timeout=self.timeout)
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get('code') in (200, 801, 802, 803, 800):
                        return result
                logger.debug(f"EAPI 请求失败, 重试 {attempt+1}/{self.max_retries}")
            except Exception as e:
                logger.debug(f"EAPI 异常: {e}, 重试 {attempt+1}/{self.max_retries}")
        return None

    async def _eapi_request_async(self, eapi_path: str, data: dict) -> Optional[dict]:
        """异步包装 EAPI 请求"""
        return await asyncio.to_thread(self._eapi_request, eapi_path, data)

    # ---- Cookie 管理 ----

    def _cookies_path(self) -> str:
        """Cookie 文件路径"""
        from zhiyan_web.config.settings import DATA_DIR
        return os.path.join(DATA_DIR, 'netease_cookies.json')

    def save_cookies(self) -> None:
        """持久化保存 Cookie"""
        try:
            cookies = self.session.cookies.get_dict()
            path = self._cookies_path()
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(cookies, f)
            logger.info("网易云 Cookie 已保存")
        except Exception as e:
            logger.warning(f"保存 Cookie 失败: {e}")

    def load_cookies(self) -> bool:
        """加载持久化 Cookie，返回是否成功"""
        try:
            path = self._cookies_path()
            if not os.path.exists(path):
                return False
            with open(path, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            if cookies:
                self.session.cookies.update(cookies)
                logger.info("网易云 Cookie 已加载")
                return True
        except Exception as e:
            logger.warning(f"加载 Cookie 失败: {e}")
        return False

    def clear_cookies(self) -> None:
        """清除 Cookie"""
        self.session.cookies.clear()
        try:
            path = self._cookies_path()
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        logger.info("网易云 Cookie 已清除")

    # ---- 二维码登录 ----

    async def get_qr_unikey(self) -> Optional[str]:
        """获取二维码 unikey"""
        result = await self._eapi_request_async("/login/qrcode/unikey", {"type": 1})
        if result and 'unikey' in result:
            return result['unikey']
        return None

    async def check_qr_login(self, unikey: str) -> Tuple[int, Optional[str]]:
        """
        检查二维码登录状态

        Returns:
            (code, message)
            code: 800=过期 801=等待扫码 802=等待确认 803=登录成功(已授权) -1=失败
            message: 状态描述
        """
        result = await self._eapi_request_async(
            "/login/qrcode/client/login", {"key": unikey, "type": 1})
        if result is None:
            return -1, "请求失败"
        code = result.get('code', -1)
        messages = {
            800: "二维码已过期",
            801: "等待扫码",
            802: "等待确认",
            803: "登录成功",
        }
        msg = messages.get(code, f"未知状态({code})")
        if code == 803:
            # 登录成功，保存 Cookie
            self.save_cookies()
            return 803, "登录成功"
        return code, msg

    # ---- 用户信息 ----

    async def get_account_info(self) -> Optional[dict]:
        """获取当前登录用户信息"""
        result = await self._eapi_request_async("/nuser/account/get", {"csrf_token": ""})
        if result and 'account' in result:
            return result
        return None

    async def is_logged_in(self) -> bool:
        """检查是否已登录"""
        if not self.session.cookies.get_dict():
            return False
        info = await self.get_account_info()
        return info is not None and info.get('account') is not None

    # ---- 歌单 ----

    async def get_user_playlists(self, uid: int, offset: int = 0, limit: int = 30) -> Optional[dict]:
        """获取用户歌单列表"""
        data = {
            "uid": uid,
            "offset": offset,
            "limit": limit,
            "csrf_token": "",
        }
        return await self._eapi_request_async("/user/playlist", data)

    async def get_playlist_detail(self, playlist_id: int, offset: int = 0, limit: int = 50) -> Optional[dict]:
        """获取歌单详情（含歌曲列表）"""
        data = {
            "id": playlist_id,
            "offset": offset,
            "limit": limit,
            "n": 100000,
            "s": 8,
            "csrf_token": "",
        }
        result = await self._eapi_request_async("/playlist/detail", data)
        # EAPI 返回的 playlist 数据在 result 字段下，标准化为 playlist 键
        if result and 'result' in result:
            return {"playlist": result['result'], "code": result.get('code', 200)}
        return result

    async def get_playlist_all_songs(self, playlist_id: int, track_ids: List[int]) -> List[dict]:
        """批量获取歌单中所有歌曲详情"""
        # 分批获取，每次最多 200 个
        songs = []
        for i in range(0, len(track_ids), 200):
            batch = track_ids[i:i + 200]
            data = {"ids": batch, "csrf_token": ""}
            result = await self._eapi_request_async("/song/detail", data)
            if result and 'songs' in result:
                songs.extend(result['songs'])
        return songs

    # ---- 推荐 ----

    async def get_recommend_songs(self) -> Optional[dict]:
        """获取每日推荐歌曲"""
        return await self._eapi_request_async("/v1/discovery/recommend/songs", {"csrf_token": ""})

    # ---- 公开 API（原有保持不变） ----

    async def get_song_url(self, song_id: int, level: str = "exhigh") -> Optional[dict]:
        """
        获取歌曲播放 URL

        Args:
            song_id: 歌曲 ID
            level: 音质 (low/medium/high/exhigh/lossless/hires)

        Returns:
            包含 url 信息的 dict 或 None
        """
        data = {
            "ids": [song_id],
            "level": level,
            "encodeType": "mp3",
            "csrf_token": "",
        }
        result = await self._eapi_request_async("/song/enhance/player/url/v1", data)
        if result and 'data' in result:
            for item in result['data']:
                if item and item.get('url'):
                    return item
        return None

    async def get_song_detail(self, song_id: int) -> Optional[dict]:
        """获取歌曲详情"""
        data = {"ids": [song_id], "csrf_token": ""}
        result = await self._eapi_request_async("/song/detail", data)
        if result and 'songs' in result:
            songs = result['songs']
            if songs:
                return songs[0]
        return None

    async def get_lyrics(self, song_id: int) -> Optional[str]:
        """获取歌词"""
        data = {"id": song_id, "lv": -1, "kv": -1, "tv": -1, "csrf_token": ""}
        result = await self._eapi_request_async("/song/lyric", data)
        if result and 'lrc' in result and 'lyric' in result['lrc']:
            return result['lrc']['lyric']
        return None

    async def search(self, keyword: str, limit: int = 20, offset: int = 0) -> Optional[dict]:
        """搜索歌曲"""
        data = {
            "s": keyword,
            "type": 1,
            "offset": offset,
            "limit": limit,
            "total": True,
            "csrf_token": "",
        }
        result = await self._eapi_request_async("/cloudsearch/get/web", data)
        if result and 'result' in result:
            return result['result']
        return None

    async def search_and_get_url(self, title: str, artist: str = "") -> Optional[str]:
        """
        搜索歌曲并获取下载 URL

        Args:
            title: 歌曲标题
            artist: 艺术家

        Returns:
            下载 URL 或 None
        """
        keyword = f"{artist} {title}".strip()
        result = await self.search(keyword, limit=5)
        if result and 'songs' in result:
            songs = result['songs']
            if songs:
                song_id = songs[0].get('id')
                if song_id:
                    url_info = await self.get_song_url(song_id)
                    if url_info:
                        return url_info.get('url')
        return None


# 全局实例
netease_weapi = NeteaseWeAPI()