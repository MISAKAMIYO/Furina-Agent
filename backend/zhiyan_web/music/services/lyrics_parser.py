"""
歌词解析服务
负责解析 LRC 格式的歌词文件，支持多源网络搜索和歌词补全

核心功能:
- 解析 LRC 歌词文件（支持 [mm:ss.xx]、[hh:mm:ss]、纯文本）
- 根据歌曲标题/文件名智能查找本地歌词
- 异步多源网络搜索歌词（网易云、QQ音乐、酷狗、lrclib）
- 歌词补全（纯文本转 LRC）
- 本地缓存已搜索的歌词
"""
from __future__ import annotations

import os
import re
import json
import base64
import asyncio
import logging
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote

import httpx

from zhiyan_web.config.settings import LYRICS_DIR, SONGS_DIR

logger = logging.getLogger("ZhiYanWeb")

# 共享的异步 HTTP 客户端
_http_client: Optional[httpx.AsyncClient] = None

# 歌词搜索源名称映射
SOURCE_NAMES = {
    "local": "本地",
    "netease": "网易云音乐",
    "qq": "QQ音乐",
    "kugou": "酷狗音乐",
    "lrclib": "LRClib",
    "lyricsovh": "Lyrics.ovh",
    "none": "无",
}


def _get_http_client() -> httpx.AsyncClient:
    """获取或创建共享的 httpx 异步客户端"""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=8.0),
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                              'AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            },
            follow_redirects=True,
        )
    return _http_client


def _normalize(text: str) -> str:
    """标准化文本用于匹配：去空格、转小写、去除特殊字符"""
    text = text.lower().strip()
    # 去除常见特殊字符和括号内容
    text = re.sub(r'[（）\(\)\[\]【】\{\}「」『』""\'\"`~!！@#$%^&*+=|/\\<>?？,，。.、:：;；_\-—]', '', text)
    text = text.replace(' ', '').replace('\u3000', '')
    return text


class LyricsParser:
    """
    歌词解析器
    
    提供 LRC 歌词文件的解析、本地查找和异步网络搜索功能
    """
    
    # ──────────────── LRC 解析 ────────────────
    
    @staticmethod
    def parse_lrc_file(filepath: str) -> List[Dict]:
        """解析 LRC 歌词文件"""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            return LyricsParser.parse_lrc_text(content)
        except Exception as e:
            logger.error(f"解析歌词文件失败: {filepath}, {e}")
        return []
    
    @staticmethod
    def parse_lrc_text(content: str) -> List[Dict]:
        """
        解析 LRC 歌词文本
        
        支持格式:
        - [mm:ss.xx] 标准格式
        - [mm:ss.xxx] 三位毫秒
        - [hh:mm:ss] 冒号分隔格式
        - 纯文本歌词（无时间标签）
        """
        lyrics = []
        try:
            lines = content.split('\n')
            # 匹配 [mm:ss.xx] 或 [mm:ss.xxx]
            time_pattern = re.compile(r'\[(\d{1,2}):(\d{2})[.:](\d{2,3})\]')
            
            has_any_timestamp = False
            # 增强型 LRC（A2）：行内逐字时间标记 <mm:ss.xx>
            word_pattern = re.compile(r'<(\d{1,2}):(\d{2})[.:](\d{2,3})>')
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # 跳过元信息行
                if re.match(r'\[(ti|ar|al|by|offset|re|length|sign|hash):', line, re.IGNORECASE):
                    continue
                
                # 处理多时间戳行 [00:01.00][00:05.00]text
                timestamps = []
                remaining = line
                while True:
                    match = time_pattern.match(remaining)
                    if not match:
                        break
                    has_any_timestamp = True
                    g1 = int(match.group(1))
                    g2 = int(match.group(2))
                    g3 = int(match.group(3))
                    if len(match.group(3)) == 2:
                        g3 *= 10
                    # 判断是否为 [hh:mm:ss] 格式
                    if g1 >= 60:
                        time_val = g1 * 3600 + g2 * 60 + g3 / 1000.0
                    else:
                        time_val = g1 * 60 + g2 + g3 / 1000.0
                    timestamps.append(time_val)
                    remaining = remaining[match.end():]
                
                # 先拆逐字时间标记，得到纯文本 + 每段起始时间
                words = []
                plain = remaining
                marks = list(word_pattern.finditer(remaining))
                if marks:
                    head = remaining[:marks[0].start()].strip()
                    if head:
                        words.append({"t": timestamps[0] if timestamps else 0.0, "text": head})
                    for idx, m in enumerate(marks):
                        g1, g2, g3 = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        if len(m.group(3)) == 2:
                            g3 *= 10
                        wt = (g1 * 3600 + g2 * 60 + g3 / 1000.0) if g1 >= 60 else (g1 * 60 + g2 + g3 / 1000.0)
                        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(remaining)
                        seg = remaining[m.end():end]
                        if seg:
                            words.append({"t": wt, "text": seg})
                    plain = word_pattern.sub('', remaining)

                text = plain.strip()
                if text:
                    base_t = timestamps[0] if timestamps else 0.0
                    if words:
                        # 去掉纯空白词，重算相对行首的偏移（前端按秒比较）
                        clean = [w for w in words if str(w.get("text", "")).strip()]
                        if timestamps:
                            for t in timestamps:
                                off = t - base_t
                                lyrics.append({
                                    "time": t,
                                    "text": text,
                                    "words": [{"t": round(w["t"] + off, 3), "text": w["text"]} for w in clean],
                                })
                        else:
                            lyrics.append({"time": base_t, "text": text,
                                           "words": [{"t": round(w["t"], 3), "text": w["text"]} for w in clean]})
                        continue
                    if timestamps:
                        for t in timestamps:
                            lyrics.append({"time": t, "text": text})
                    else:
                        lyrics.append({"time": 0.0, "text": text})
            
            # 纯文本歌词（无时间戳）
            if not has_any_timestamp and not lyrics:
                for line in lines:
                    line = line.strip()
                    if line and not re.match(r'\[(ti|ar|al|by|offset|re|length|sign|hash):', line, re.IGNORECASE):
                        lyrics.append({"time": 0.0, "text": line})
            
            lyrics.sort(key=lambda x: x['time'])
        except Exception as e:
            logger.error(f"解析LRC文本失败: {e}")
        
        return lyrics
    
    # ──────────────── 本地查找 ────────────────
    
    @staticmethod
    def find_lyrics(song_title: str, filename: str = "") -> Tuple[List[Dict], str]:
        """
        根据歌曲标题和文件名查找本地歌词文件
        
        匹配策略（按优先级）:
        1. 文件名完全匹配（去掉扩展名）
        2. 标题与歌词文件名互相包含
        3. 标准化后互相包含（去除特殊字符）
        
        参数:
            song_title: 歌曲标题
            filename: 歌曲文件名（可选，用于更精确匹配）
        
        返回:
            Tuple[List[Dict], str]: (歌词列表, 匹配到的歌词文件名)
        """
        if not os.path.exists(LYRICS_DIR):
            return [], ""
        
        lrc_files = [f for f in os.listdir(LYRICS_DIR) if f.lower().endswith('.lrc')]
        if not lrc_files:
            return [], ""
        
        # 构建候选匹配名称列表
        candidates = []
        if filename:
            candidates.append(os.path.splitext(filename)[0])
        candidates.append(song_title)
        
        # 策略 1: 精确匹配（去扩展名后完全相同）
        for candidate in candidates:
            candidate_clean = _normalize(candidate)
            for lrc_file in lrc_files:
                lrc_name = _normalize(os.path.splitext(lrc_file)[0])
                if candidate_clean and candidate_clean == lrc_name:
                    filepath = os.path.join(LYRICS_DIR, lrc_file)
                    lyrics = LyricsParser.parse_lrc_file(filepath)
                    if lyrics:
                        logger.info(f"本地歌词精确匹配: {song_title} -> {lrc_file}")
                        return lyrics, lrc_file
        
        # 策略 2: 互相包含匹配
        for candidate in candidates:
            candidate_clean = _normalize(candidate)
            if not candidate_clean:
                continue
            for lrc_file in lrc_files:
                lrc_name = _normalize(os.path.splitext(lrc_file)[0])
                if not lrc_name:
                    continue
                if candidate_clean in lrc_name or lrc_name in candidate_clean:
                    filepath = os.path.join(LYRICS_DIR, lrc_file)
                    lyrics = LyricsParser.parse_lrc_file(filepath)
                    if lyrics:
                        logger.info(f"本地歌词模糊匹配: {song_title} -> {lrc_file}")
                        return lyrics, lrc_file
        
        # 策略 3: 关键词匹配（提取标题中的核心关键词）
        title_keywords = LyricsParser._extract_keywords(song_title)
        if title_keywords:
            best_match = None
            best_score = 0
            for lrc_file in lrc_files:
                lrc_name = _normalize(os.path.splitext(lrc_file)[0])
                score = sum(1 for kw in title_keywords if kw in lrc_name)
                if score > best_score and score >= 1:
                    best_score = score
                    best_match = lrc_file
            
            if best_match:
                filepath = os.path.join(LYRICS_DIR, best_match)
                lyrics = LyricsParser.parse_lrc_file(filepath)
                if lyrics:
                    logger.info(f"本地歌词关键词匹配: {song_title} -> {best_match} (score={best_score})")
                    return lyrics, best_match
        
        return [], ""
    
    @staticmethod
    def _extract_keywords(title: str) -> List[str]:
        """从歌曲标题中提取关键词"""
        # 去除常见前缀/后缀
        cleaned = re.sub(r'【[^】]*】', '', title)
        cleaned = re.sub(r'\([^)]*\)', '', cleaned)
        cleaned = re.sub(r'（[^）]*）', '', cleaned)
        cleaned = re.sub(r'[「」『』\[\]{}]', '', cleaned)
        # 去除常见无意义词
        cleaned = re.sub(r'(完整版|官方版|MV|mv|HQ|hq|Hi-?Res|hi-?res|无损|翻唱|cover|Cover|Remix|remix|Live|live|版)', '', cleaned)
        cleaned = cleaned.strip()
        
        # 按分隔符拆分
        parts = re.split(r'[-—_–·/|,，.。]', cleaned)
        keywords = []
        for part in parts:
            part = _normalize(part)
            if len(part) >= 2:
                keywords.append(part)
        
        # 去重，保留顺序
        seen = set()
        result = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                result.append(kw)
        
        return result
    
    # ──────────────── 异步网络搜索 ────────────────
    
    @staticmethod
    async def search_lyrics_online(title: str, artist: str = "") -> Tuple[List[Dict], str]:
        """
        从网络异步搜索歌词（多源依次尝试）
        
        参数:
            title: 歌曲标题
            artist: 艺术家
        
        返回:
            Tuple[List[Dict], str]: (歌词列表, 来源名称)
        """
        if not title:
            return [], "none"
        
        sources = [
            ("netease", LyricsParser._search_from_netease),
            ("qq", LyricsParser._search_from_qqmusic),
            ("kugou", LyricsParser._search_from_kugou),
            ("lrclib", LyricsParser._search_from_lrclib),
        ]
        
        for source_name, search_fn in sources:
            try:
                lrc_text = await search_fn(title, artist)
                if lrc_text:
                    lrc_text = LyricsParser._clean_lrc_text(lrc_text)
                    lyrics = LyricsParser.parse_lrc_text(lrc_text)
                    if lyrics:
                        logger.info(f"网络歌词搜索成功 [{SOURCE_NAMES.get(source_name, source_name)}]: "
                                    f"{title}，共 {len(lyrics)} 行")
                        # 保存到本地缓存
                        LyricsParser._save_lyrics(title, lrc_text)
                        return lyrics, source_name
            except asyncio.TimeoutError:
                logger.warning(f"歌词搜索超时 [{SOURCE_NAMES.get(source_name, source_name)}]: {title}")
            except Exception as e:
                logger.debug(f"歌词搜索源失败 [{SOURCE_NAMES.get(source_name, source_name)}]: {e}")
        
        return [], "none"
    
    @staticmethod
    async def _search_from_netease(title: str, artist: str) -> Optional[str]:
        """从网易云音乐搜索歌词"""
        client = _get_http_client()
        keyword = f"{artist} {title}".strip()
        
        # 搜索歌曲
        search_url = (
            f"https://netease-cloud-music-api-five-roan-25.vercel.app/search"
            f"?keywords={quote(keyword)}&type=1&limit=5"
        )
        
        try:
            resp = await client.get(search_url, headers={
                'Referer': 'https://music.163.com/',
                'Origin': 'https://music.163.com',
            })
            if resp.status_code != 200:
                return None
            data = resp.json()
            
            songs = data.get('result', {}).get('songs', [])
            if not songs:
                return None
            
            song_id = songs[0].get('id')
            if not song_id:
                return None
            
            # 获取歌词
            lrc_url = f"https://netease-cloud-music-api-five-roan-25.vercel.app/lyric?id={song_id}"
            resp2 = await client.get(lrc_url, headers={
                'Referer': 'https://music.163.com/',
                'Origin': 'https://music.163.com',
            })
            if resp2.status_code != 200:
                return None
            lrc_data = resp2.json()
            
            lrc = lrc_data.get('lrc', {}).get('lyric', '')
            if lrc:
                return lrc
        except Exception as e:
            logger.debug(f"网易云搜索失败: {e}")
        
        return None
    
    @staticmethod
    async def _search_from_qqmusic(title: str, artist: str) -> Optional[str]:
        """从QQ音乐搜索歌词"""
        client = _get_http_client()
        keyword = f"{artist} {title}".strip()
        
        search_url = (
            "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
            f"?ct=24&qqmusic_ver=1001&t=0&p=1&n=5&w={quote(keyword)}"
            "&format=json&inCharset=utf8&outCharset=utf-8"
        )
        
        try:
            resp = await client.get(search_url, headers={
                'Referer': 'https://y.qq.com/',
                'Origin': 'https://y.qq.com',
            })
            if resp.status_code != 200:
                return None
            text = resp.text
            # QQ音乐 API 有时返回 JSONP（如 MusicJsonCallback({...})），需剥离回调
            if text.startswith('MusicJsonCallback') or text.startswith('callback'):
                import re as _re
                m = _re.search(r'\{[\s\S]*\}', text)
                if m:
                    text = m.group(0)
                else:
                    return None
            data = json.loads(text)
            
            # 兼容不同的响应结构
            song_list = None
            if 'data' in data and 'song' in data['data']:
                song_list = data['data']['song'].get('list', [])
            elif 'data' in data and isinstance(data['data'], list):
                song_list = data['data']
            
            if not song_list:
                return None
            
            songmid = song_list[0].get('songmid')
            if not songmid:
                return None
            
            lrc_url = (
                "https://c.y.qq.com/lyric/fcgi-bin/fcg_get_lyric.lrc"
                f"?songmid={songmid}&format=json&inCharset=utf8&outCharset=utf-8"
            )
            resp2 = await client.get(lrc_url, headers={
                'Referer': 'https://y.qq.com/',
            })
            if resp2.status_code != 200:
                return None
            lrc_text = resp2.text
            # 同样处理 JSONP
            if lrc_text.startswith('MusicJsonCallback') or lrc_text.startswith('callback'):
                import re as _re
                m = _re.search(r'\{[\s\S]*\}', lrc_text)
                if m:
                    lrc_text = m.group(0)
                else:
                    return None
            lrc_data = json.loads(lrc_text)
            
            lyric_b64 = lrc_data.get('lyric', '')
            if lyric_b64:
                return base64.b64decode(lyric_b64).decode('utf-8')
        except Exception as e:
            logger.debug(f"QQ音乐搜索失败: {e}")
        
        return None
    
    @staticmethod
    async def _search_from_kugou(title: str, artist: str) -> Optional[str]:
        """从酷狗音乐搜索歌词"""
        client = _get_http_client()
        keyword = f"{artist} {title}".strip()
        
        try:
            # 搜索歌曲获取 hash
            search_url = f"https://searchapi.kugou.com/switch/song?keyword={quote(keyword)}"
            resp = await client.get(search_url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            
            items = data.get('data', [])
            if not items:
                return None
            
            song_hash = items[0].get('hash')
            if not song_hash:
                return None
            
            # 搜索歌词
            lrc_search_url = (
                "https://lyrics.kugou.com/search"
                f"?ver=1&client=pc&hash={song_hash}&keyword={quote(keyword)}"
            )
            resp2 = await client.get(lrc_search_url)
            if resp2.status_code != 200:
                return None
            search_result = resp2.json()
            
            candidates = search_result.get('candidates', [])
            if not candidates:
                return None
            
            cand = candidates[0]
            accesskey = cand.get('accesskey')
            id_str = cand.get('id')
            if not accesskey or not id_str:
                return None
            
            # 下载歌词
            dl_url = (
                "https://lyrics.kugou.com/download"
                f"?ver=1&client=pc&id={id_str}&accesskey={accesskey}&fmt=lrc&charset=utf8"
            )
            resp3 = await client.get(dl_url)
            if resp3.status_code != 200:
                return None
            dl_data = resp3.json()
            
            content = dl_data.get('content', '')
            if content:
                return base64.b64decode(content).decode('utf-8')
        except Exception as e:
            logger.debug(f"酷狗搜索失败: {e}")
        
        return None
    
    @staticmethod
    async def _search_from_lrclib(title: str, artist: str, album: str = "", duration: float = 0) -> Optional[str]:
        """从 LRClib 搜索歌词（支持同步歌词）

        使用 /api/search 端点进行搜索，因为 /api/get 需要 album_name 和 duration 参数。
        搜索到结果后优先使用同步歌词，其次使用纯文本。

        参数:
            title: 歌曲标题
            artist: 艺术家
            album: 专辑名（可选）
            duration: 歌曲时长（秒，可选）

        返回:
            Optional[str]: LRC 格式歌词文本，未找到返回 None
        """
        client = _get_http_client()
        app_ua = 'ZhiYanAgentWeb/1.0 (https://github.com/zhiyan-agent)'
        
        try:
            # 1. 先尝试精确匹配：如果有 album 和 duration，尝试 /api/get
            if album and duration > 0:
                get_url = (
                    f"https://lrclib.net/api/get"
                    f"?track_name={quote(title)}"
                    f"&artist_name={quote(artist)}"
                    f"&album_name={quote(album)}"
                    f"&duration={duration}"
                )
                resp = await client.get(
                    get_url,
                    headers={
                        'User-Agent': app_ua,
                        'Accept': 'application/json',
                        'Referer': 'https://lrclib.net/',
                    },
                    timeout=httpx.Timeout(10.0, connect=6.0),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    result = LyricsParser._extract_lrclib_lyrics(data)
                    if result:
                        logger.info(f"LRClib精确匹配成功: {title} - {artist}")
                        return result

            # 2. 使用 /api/search 进行关键词搜索
            search_url = (
                f"https://lrclib.net/api/search"
                f"?track_name={quote(title)}"
                f"&artist_name={quote(artist)}"
            )
            resp = await client.get(
                search_url,
                headers={
                    'User-Agent': app_ua,
                    'Accept': 'application/json',
                    'Referer': 'https://lrclib.net/',
                },
                timeout=httpx.Timeout(10.0, connect=6.0),
            )
            if resp.status_code != 200:
                logger.debug(f"LRClib搜索返回 {resp.status_code}")
                return None

            results = resp.json()
            if not isinstance(results, list) or len(results) == 0:
                logger.debug(f"LRClib搜索无结果: {title} - {artist}")
                return None

            # 取第一个匹配结果
            best = results[0]
            result = LyricsParser._extract_lrclib_lyrics(best)
            if result:
                logger.info(f"LRClib搜索匹配成功: {title} - {artist} -> {best.get('trackName', '?')}")
                return result

        except httpx.TimeoutException:
            logger.warning(f"LRClib请求超时: {title} - {artist}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning(f"LRClib请求频率限制: {title} - {artist}")
            else:
                logger.debug(f"LRClib HTTP错误 {e.response.status_code}: {title} - {artist}")
        except Exception as e:
            logger.debug(f"LRClib搜索失败: {e}")

        return None

    @staticmethod
    def _extract_lrclib_lyrics(data: dict) -> Optional[str]:
        """从 LRClib API 响应中提取歌词文本

        优先使用同步歌词（syncedLyrics），其次使用纯文本（plainLyrics）。
        同时处理可能存在的 YAML 前注格式（如 '---\\ntitle: ...\\n---\\nlyrics'）。

        参数:
            data: LRClib API 返回的单条记录字典

        返回:
            Optional[str]: LRC 或纯文本歌词，无法提取返回 None
        """
        if not data or not isinstance(data, dict):
            return None

        # 检查是否为纯器乐（instrumental）
        if data.get('instrumental', False):
            logger.debug("LRClib 标记为纯器乐，无歌词")
            return None

        # 1. 优先使用同步歌词
        synced = data.get('syncedLyrics', '')
        if synced and isinstance(synced, str) and synced.strip():
            # 检查是否包含 YAML 前注（格式：---\\nkey: value\\n---\\n实际歌词）
            cleaned = LyricsParser._strip_yaml_frontmatter(synced)
            if cleaned != synced:
                logger.debug("LRClib 同步歌词包含 YAML 前注，已剥离")
            return cleaned

        # 2. 退而求其次，使用纯文本转 LRC
        plain = data.get('plainLyrics', '')
        if plain and isinstance(plain, str) and plain.strip():
            cleaned = LyricsParser._strip_yaml_frontmatter(plain)
            return LyricsParser._text_to_lrc(cleaned)

        return None

    @staticmethod
    def _strip_yaml_frontmatter(text: str) -> str:
        """剥离歌词文本中的 YAML 前注（front matter）

        处理格式:
            ---
            title: 歌曲名
            artist: 歌手
            ---
            实际歌词内容...

        参数:
            text: 原始歌词文本

        返回:
            str: 剥离 YAML 前注后的歌词文本
        """
        if not text:
            return text

        lines = text.split('\n')
        # 检查是否以 '---' 开头
        if not lines or lines[0].strip() != '---':
            return text

        # 查找结束的 '---'
        end_idx = -1
        for i in range(1, len(lines)):
            if lines[i].strip() == '---':
                end_idx = i
                break

        if end_idx == -1:
            # 没有结束标记，但以 --- 开头，跳过第一行
            return '\n'.join(lines[1:]).strip()

        # 返回 YAML 前注之后的内容
        return '\n'.join(lines[end_idx + 1:]).strip()
    
    # ──────────────── 多源搜索（返回列表，类似 Furina Agent 对话框） ────────────────
    
    @staticmethod
    async def search_lyrics_multi_source(title: str, artist: str = "") -> List[Dict]:
        """
        从多个网络源搜索歌词，返回结果列表（类似 Furina Agent 对话框）
        
        参数:
            title: 歌曲标题
            artist: 艺术家
        
        返回:
            list: [{"title": "...", "artist": "...", "lyrics": "...", "source": "netease"}, ...]
        """
        results = []
        keyword = f"{artist} {title}".strip() or title
        
        # 1. 网易云音乐 (与 Furina Agent 相同 API)
        try:
            netease_results = await LyricsParser._search_netease_multi(keyword)
            results.extend(netease_results)
        except Exception as e:
            logger.debug(f"网易云多源搜索失败: {e}")
        
        # 2. QQ音乐
        try:
            qq_result = await LyricsParser._search_from_qqmusic(title, artist)
            if qq_result:
                results.append({
                    "title": title,
                    "artist": artist,
                    "lyrics": qq_result,
                    "source": "qq"
                })
        except Exception as e:
            logger.debug(f"QQ音乐多源搜索失败: {e}")
        
        # 3. 酷狗音乐
        try:
            kugou_result = await LyricsParser._search_from_kugou(title, artist)
            if kugou_result:
                results.append({
                    "title": title,
                    "artist": artist,
                    "lyrics": kugou_result,
                    "source": "kugou"
                })
        except Exception as e:
            logger.debug(f"酷狗多源搜索失败: {e}")
        
        # 4. LRClib
        try:
            lrclib_result = await LyricsParser._search_from_lrclib(title, artist)
            if lrclib_result:
                results.append({
                    "title": title,
                    "artist": artist,
                    "lyrics": lrclib_result,
                    "source": "lrclib"
                })
        except Exception as e:
            logger.debug(f"LRClib多源搜索失败: {e}")
        
        # 去重
        seen = set()
        unique_results = []
        for r in results:
            key = (r.get("title", ""), r.get("artist", ""), r.get("source", ""))
            if key not in seen:
                seen.add(key)
                unique_results.append(r)
        
        return unique_results
    
    @staticmethod
    async def _search_netease_multi(keyword: str) -> List[Dict]:
        """
        从网易云音乐搜索歌词（多结果，与 Furina Agent 相同的 API）
        
        使用 music.163.com 官方 API 搜索，返回前 5 个结果
        """
        client = _get_http_client()
        results = []
        
        try:
            # 搜索歌曲 (与 Furina Agent 相同的 API 和参数)
            search_url = "https://music.163.com/api/search/get"
            params = {
                "s": keyword,
                "type": 1,
                "offset": 0,
                "limit": 5
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://music.163.com"
            }
            
            resp = await client.get(search_url, params=params, headers=headers)
            if resp.status_code != 200:
                return results
            
            data = resp.json()
            songs = data.get("result", {}).get("songs", [])
            if not songs:
                return results
            
            for song in songs[:5]:
                song_id = song.get("id")
                if not song_id:
                    continue
                
                song_name = song.get("name", "")
                artists = song.get("artists", [{}])
                artist_name = artists[0].get("name", "") if artists else ""
                
                # 获取歌词 (与 Furina Agent 相同的 API)
                lrc_url = f"https://music.163.com/api/song/lyric?id={song_id}&lv=-1&kv=-1&tv=-1"
                try:
                    lrc_resp = await client.get(lrc_url, headers=headers)
                    if lrc_resp.status_code != 200:
                        continue
                    
                    lrc_data = lrc_resp.json()
                    lyrics = ""
                    if "lrc" in lrc_data and "lyric" in lrc_data["lrc"]:
                        lyrics = lrc_data["lrc"]["lyric"]
                    elif "tlyric" in lrc_data and "lyric" in lrc_data["tlyric"]:
                        lyrics = lrc_data["tlyric"]["lyric"]
                    
                    if lyrics:
                        results.append({
                            "title": song_name,
                            "artist": artist_name,
                            "lyrics": lyrics,
                            "source": "netease"
                        })
                except Exception as e:
                    logger.debug(f"网易云多结果获取歌词失败: {song_name}: {e}")
                    continue
        
        except Exception as e:
            logger.debug(f"网易云多源搜索异常: {e}")
        
        return results
    
    # ──────────────── 工具方法 ────────────────
    
    @staticmethod
    def _text_to_lrc(text: str, duration_per_line: float = 5.0) -> str:
        """将纯文本歌词转换为LRC格式"""
        lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
        lrc_lines = []
        current_time = 0.0
        
        for line in lines:
            minutes = int(current_time // 60)
            seconds = current_time % 60
            lrc_lines.append(f"[{minutes:02d}:{seconds:05.2f}]{line}")
            current_time += duration_per_line
        
        return '\n'.join(lrc_lines)
    
    @staticmethod
    def _clean_lrc_text(lrc_text: str) -> str:
        """清理LRC文本，去除多余的空行和无效行"""
        lines = []
        for line in lrc_text.split('\n'):
            line = line.strip()
            if line:
                lines.append(line)
        return '\n'.join(lines)
    
    @staticmethod
    def _save_lyrics(title: str, lrc_text: str) -> str:
        """保存歌词到本地，返回保存的文件名"""
        try:
            os.makedirs(LYRICS_DIR, exist_ok=True)
            safe_name = re.sub(r'[<>:"/\\|?*\r\n]', '', title)[:100]
            filepath = os.path.join(LYRICS_DIR, f"{safe_name}.lrc")
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(lrc_text)
            logger.info(f"歌词已保存: {filepath}")
            return f"{safe_name}.lrc"
        except Exception as e:
            logger.warning(f"保存歌词失败: {e}")
            return ""
    
    @staticmethod
    def get_all_local_lyrics() -> List[Dict]:
        """获取所有本地歌词文件列表"""
        if not os.path.exists(LYRICS_DIR):
            return []
        
        result = []
        for filename in sorted(os.listdir(LYRICS_DIR)):
            if filename.lower().endswith('.lrc'):
                filepath = os.path.join(LYRICS_DIR, filename)
                size = os.path.getsize(filepath)
                result.append({
                    "filename": filename,
                    "title": os.path.splitext(filename)[0],
                    "size": size,
                })
        return result
    
    @staticmethod
    def get_lyrics_file_content(filename: str) -> str:
        """获取本地歌词文件的原始文本内容"""
        filepath = os.path.join(LYRICS_DIR, filename)
        if not os.path.exists(filepath):
            return ""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception:
            return ""
    
    @staticmethod
    def save_lyrics_file(filename: str, content: str) -> bool:
        """保存歌词文件（用于手动编辑）"""
        try:
            os.makedirs(LYRICS_DIR, exist_ok=True)
            safe_name = re.sub(r'[<>:"/\\|?*\r\n]', '', filename)[:100]
            if not safe_name.lower().endswith('.lrc'):
                safe_name += '.lrc'
            filepath = os.path.join(LYRICS_DIR, safe_name)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"歌词文件已保存: {filepath}")
            return True
        except Exception as e:
            logger.error(f"保存歌词文件失败: {e}")
            return False
    
    # ──────────────── 主入口 ────────────────
    
    @staticmethod
    async def complete_lyrics(title: str, artist: str = "", filename: str = "") -> Tuple[List[Dict], str]:
        """
        歌词补全：仅查本地歌词，不自动搜索网络
        
        参数:
            title: 歌曲标题
            artist: 艺术家（保留参数，但不再自动使用）
            filename: 歌曲文件名（用于更精确的本地匹配）
        
        返回:
            Tuple[List[Dict], str]: (歌词列表, 来源标识)
        """
        # 仅查本地，不自动搜索网络
        # 用户需手动点击"获取歌词"按钮触发网络搜索
        lyrics, matched_file = LyricsParser.find_lyrics(title, filename)
        if lyrics:
            return lyrics, "local"
        
        return [], "none"
