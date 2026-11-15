"""酷狗音乐 Web API 客户端

签名算法参考 KugouMusic.NET（KuGou.Net）的实现：
- 通用签名 = md5(salt + 按 key 排序的 k=v 拼接 + body + salt)
- 默认参数 appid=3116 / clientver=11440 / dfid="-" / mid=十进制大整数的 md5(installGuid)
- /v5/url 额外需要 key = md5(hash + V5KeySalt + appid + mid + userid)

搜索走 complexsearch.kugou.com，播放地址走 trackercdn.kugou.com。
付费/会员歌曲会返回 fail_process=["pkg","buy"] 且没有 url，这里如实标记为不可播放。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from typing import Any, Dict, List, Optional

import httpx

from zhiyan_web.config.settings import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.Kugou")

APP_ID = "3116"
CLIENT_VER = "11440"
LITE_SALT = "LnT6xpN3khm36zse0QzvmgTZ3waWdRSA"
V5_KEY_SALT = "185672dd44712f60bb1736df5a377e82"
USER_AGENT = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"

SEARCH_HOST = "https://complexsearch.kugou.com"
URL_HOST = "https://trackercdn.kugou.com"
SEARCH_ROUTER = "complexsearch.kugou.com"
URL_ROUTER = "trackercdn.kugou.com"

# 设备身份：进程内固定，避免每次请求都被当成新设备
_INSTALL_GUID = secrets.token_hex(16)
_MID = str(int(hashlib.md5(_INSTALL_GUID.encode("utf-8")).hexdigest(), 16))

_url_cache: Dict[str, Dict[str, Any]] = {}
_URL_TTL = 600.0
_search_cache: Dict[str, Any] = {}
_SEARCH_TTL = 90.0


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _sign(params: Dict[str, str]) -> str:
    raw = LITE_SALT + "".join(f"{k}={params[k]}" for k in sorted(params)) + LITE_SALT
    return _md5(raw)


def _signed_params(extra: Dict[str, str], v5_hash: str = "", dfid: str = "-",
                   web: bool = False) -> Dict[str, str]:
    params: Dict[str, str] = {
        "appid": APP_ID,
        "clientver": CLIENT_VER,
        "dfid": dfid,
        "mid": _MID if dfid == "-" else str(int(_md5(dfid), 16)),
        "uuid": "",
        "userid": "0",
        "clienttime": str(int(time.time())),
    }
    params["uuid"] = _md5(dfid + params["mid"])
    params.update(extra)
    if v5_hash:
        params["key"] = _md5(v5_hash + V5_KEY_SALT + APP_ID + params["mid"] + params["userid"])
    params["signature"] = _web_sign(params) if web else _sign(params)
    return params


def _headers(params: Dict[str, str], router: str) -> Dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "dfid": params.get("dfid", "-"),
        "mid": params.get("mid", ""),
        "clienttime": params.get("clienttime", ""),
        "kg-rc": "1",
        "kg-thash": "5d816a0",
        "kg-rec": "1",
        "kg-rf": "B9EDA08A64250DEFFBCADDEE00F8F25F",
        "x-router": router,
    }


def _client() -> httpx.Client:
    # cloudlist.service.kugou.com 的证书与主机名不匹配（酷狗自己的配置问题），
    # 关闭校验换取可用性；请求体里的 token 仍然走 TLS 加密传输。
    return httpx.Client(timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=True,
                        verify=False)


def _cover(raw: Any) -> str:
    if not isinstance(raw, str) or not raw:
        return ""
    return raw.replace("{size}", "400")


def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    """把酷狗搜索结果整理成前端好用的结构"""
    title = (item.get("OriSongName") or item.get("FileName") or "").strip()
    artist = (item.get("SingerName") or "").strip()
    file_name = (item.get("FileName") or "").strip()
    if not title and file_name:
        title = file_name.split(" - ", 1)[-1]
    duration = item.get("Duration")
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = 0
    return {
        "source": "kugou",
        "hash": item.get("FileHash") or "",
        "mixsong_id": item.get("MixSongID") or "",
        "title": title,
        "artist": artist,
        "album": (item.get("AlbumName") or "").strip(),
        "album_id": str(item.get("AlbumID") or "0"),
        "duration": duration,
        "cover": _cover(item.get("AlbumImage") or item.get("Image")),
        "pay_type": item.get("PayType"),
        "size": item.get("FileSize") or 0,
        # 试听/付费标记：前端据此提示
        "playable_hint": (item.get("PayType") in (0, None)),
    }


def search(keyword: str, page: int = 1, size: int = 30) -> List[Dict[str, Any]]:
    """搜索单曲（酷狗 v3 接口）"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    cache_key = f"{keyword}|{page}|{size}"
    hit = _search_cache.get(cache_key)
    if hit and time.time() - hit["ts"] < _SEARCH_TTL:
        return hit["songs"]
    extra = {
        "keyword": keyword,
        "page": str(max(1, int(page or 1))),
        "pagesize": str(max(1, min(int(size or 30), 50))),
        "platform": "AndroidFilter",
        "iscorrection": "1",
    }
    params = _signed_params(extra)
    try:
        with _client() as client:
            resp = client.get(f"{SEARCH_HOST}/v3/search/song",
                              params=params, headers=_headers(params, SEARCH_ROUTER))
            data = resp.json()
    except Exception as e:
        logger.warning(f"酷狗搜索失败: {e}")
        return []
    lists = ((data or {}).get("data") or {}).get("lists") or []
    songs = [_normalize(item) for item in lists if isinstance(item, dict)]
    _search_cache[cache_key] = {"ts": time.time(), "songs": songs}
    return songs


def get_play_url(hash_: str, album_id: str = "0", quality: str = "128") -> Dict[str, Any]:
    """取播放地址（带 10 分钟缓存）"""
    hash_ = (hash_ or "").strip().lower()
    if not hash_:
        return {"ok": False, "error": "缺少 hash"}
    cache_key = f"{hash_}:{quality}"
    hit = _url_cache.get(cache_key)
    if hit and time.time() - hit["ts"] < _URL_TTL:
        return hit["data"]

    extra = {
        "album_id": str(album_id or "0"),
        "area_code": "1",
        "hash": hash_,
        "ssa_flag": "is_fromtrack",
        "version": "11430",
        "page_id": "967177915",
        "quality": str(quality or "128"),
        "album_audio_id": "0",
        "behavior": "play",
        "pid": "411",
        "cmd": "26",
        "pidversion": "3001",
        "IsFreePart": "0",
        "ppage_id": "356753938,823673182,967485191",
        "cdnBackup": "1",
        "module": "",
        "clientver": "11430",
    }
    params = _signed_params(extra, v5_hash=hash_)
    result: Dict[str, Any] = {"ok": False, "error": "未知错误"}
    try:
        with _client() as client:
            resp = client.get(f"{URL_HOST}/v5/url",
                              params=params, headers=_headers(params, URL_ROUTER))
            data = resp.json()
        urls = data.get("url") or []
        if isinstance(urls, str):
            urls = [urls]
        if urls:
            result = {
                "ok": True,
                "url": urls[0],
                "backup": urls[1:],
                "quality": quality,
                "bitrate": ((data.get("bitRate") or 0) if isinstance(data.get("bitRate"), int) else 0),
                "file_size": data.get("fileSize") or 0,
                "ext": data.get("extName") or "",
            }
        else:
            fail = data.get("fail_process") or []
            err = data.get("error") or ""
            if "buy" in fail or "pkg" in fail:
                result = {"ok": False, "error": "这首需要酷狗会员/购买才能播放", "fail_process": fail}
            elif data.get("errcode"):
                result = {"ok": False, "error": f"酷狗拒绝请求（{data.get('errcode')}）"}
            else:
                result = {"ok": False, "error": err or "没有可用播放地址", "fail_process": fail}
    except Exception as e:
        logger.warning(f"酷狗取播放地址失败: {e}")
        result = {"ok": False, "error": f"取播放地址失败: {e}"}

    if result.get("ok"):
        _url_cache[cache_key] = {"ts": time.time(), "data": result}
    return result


def search_lyrics(title: str, artist: str = "") -> str:
    """按歌名/歌手从酷狗歌词库取 LRC 文本（失败返回空串）"""
    try:
        with _client() as client:
            keyword = f"{title} {artist}".strip()
            r1 = client.get("https://lyrics.kugou.com/search",
                            params={"ver": "1", "man": "yes", "client": "pc",
                                    "keyword": keyword})
            cands = (r1.json() or {}).get("candidates") or []
            if not cands:
                return ""
            cand = cands[0]
            r2 = client.get("https://lyrics.kugou.com/download",
                            params={"ver": "1", "client": "pc", "id": cand.get("id"),
                                    "accesskey": cand.get("accesskey"), "fmt": "lrc",
                                    "charset": "utf8"})
            import base64
            content = (r2.json() or {}).get("content") or ""
            return base64.b64decode(content).decode("utf-8", errors="ignore") if content else ""
    except Exception as e:
        logger.debug(f"酷狗歌词获取失败: {e}")
        return ""



# ==================== 登录与会话 ====================

WEB_SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
LOGIN_HOST = "https://login-user.kugou.com"
CLOUD_HOST = "https://cloudlist.service.kugou.com"
SESSION_PATH = os.path.join(DATA_DIR, "kugou_session.json")

_session: Dict[str, Any] = {"userid": "", "token": "", "nickname": "", "saved_at": 0.0}


def _web_sign(params: Dict[str, str]) -> str:
    raw = WEB_SALT + "".join(f"{k}={params[k]}" for k in sorted(params)) + WEB_SALT
    return _md5(raw)


def _sign_with_body(params: Dict[str, str], body: str) -> str:
    raw = LITE_SALT + "".join(f"{k}={params[k]}" for k in sorted(params)) + body + LITE_SALT
    return _md5(raw)


def load_session() -> Dict[str, Any]:
    """从磁盘恢复登录态（进程重启后仍然有效）"""
    global _session
    try:
        if os.path.isfile(SESSION_PATH):
            with open(SESSION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _session.update({
                    "userid": str(data.get("userid") or ""),
                    "token": str(data.get("token") or ""),
                    "nickname": str(data.get("nickname") or ""),
                    "saved_at": float(data.get("saved_at") or 0),
                })
    except Exception as e:
        logger.debug(f"读取酷狗登录态失败: {e}")
    return dict(_session)


def save_session(userid: str, token: str, nickname: str = "") -> None:
    global _session
    _session = {
        "userid": str(userid or ""),
        "token": str(token or ""),
        "nickname": str(nickname or ""),
        "saved_at": time.time(),
    }
    try:
        os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
        tmp = SESSION_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_session, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SESSION_PATH)
    except Exception as e:
        logger.warning(f"保存酷狗登录态失败: {e}")


def clear_session() -> None:
    global _session
    _session = {"userid": "", "token": "", "nickname": "", "saved_at": 0.0}
    try:
        if os.path.isfile(SESSION_PATH):
            os.remove(SESSION_PATH)
    except Exception:
        pass


def get_session() -> Dict[str, Any]:
    return dict(_session)


def is_logged_in() -> bool:
    return bool(_session.get("userid") and _session.get("token"))


def qr_key() -> Dict[str, Any]:
    """获取酷狗扫码登录二维码（返回 key 与二维码图片地址）"""
    params = _signed_params({
        "appid": "1001",
        "clientver": "11040",
        "type": "1",
        "plat": "4",
        "srcappid": "2919",
        "qrcode_txt": "https://h5.kugou.com/apps/loginQRCode/html/index.html?appid=3116&",
    }, dfid="-", web=True)
    try:
        with _client() as client:
            resp = client.get(f"{LOGIN_HOST}/v2/qrcode", params=params,
                              headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            data = resp.json()
    except Exception as e:
        logger.warning(f"酷狗二维码获取失败: {e}")
        return {"ok": False, "error": f"获取二维码失败: {e}"}
    body = data.get("data") or {}
    key = body.get("qrcode") or data.get("qrcode") or ""
    if not key:
        return {"ok": False, "error": data.get("error") or "没有拿到二维码 key"}
    return {"ok": True, "key": key, "img": body.get("qrcode_img") or "", "raw_status": data.get("status")}


def qr_check(key: str) -> Dict[str, Any]:
    """轮询扫码状态；确认后写入会话

    酷狗返回的 data.status：0=过期 1=等待扫码 2=已扫码待确认 4=确认登录（带 token）
    """
    if not key:
        return {"ok": False, "error": "缺少二维码 key"}
    params = _signed_params({"plat": "4", "appid": "3116", "srcappid": "2919", "qrcode": key},
                            dfid="-", web=True)
    try:
        with _client() as client:
            resp = client.get(f"{LOGIN_HOST}/v2/get_userinfo_qrcode", params=params,
                              headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            data = resp.json()
    except Exception as e:
        return {"ok": False, "error": f"查询扫码状态失败: {e}"}

    body = data.get("data") or {}
    inner = body.get("status")
    token = body.get("token") or ""
    if token:
        save_session(body.get("userid") or "", token,
                     body.get("nickname") or body.get("username") or "")
        return {"ok": True, "state": "confirmed", "userid": str(body.get("userid") or ""),
                "nickname": _session.get("nickname", ""), "message": "登录成功"}
    if inner == 4:
        return {"ok": True, "state": "confirmed", "message": "已确认，但没拿到 token，请重试"}
    if inner == 2:
        return {"ok": True, "state": "scanned", "message": "已扫码，请在手机上确认"}
    if inner == 0:
        return {"ok": True, "state": "expired", "message": "二维码已过期，请刷新"}
    return {"ok": True, "state": "waiting", "message": "等待扫码"}


def user_playlists(page: int = 1, size: int = 50) -> Dict[str, Any]:
    """获取当前登录用户的歌单列表"""
    if not is_logged_in():
        return {"ok": False, "error": "请先登录酷狗", "need_login": True, "playlists": []}
    body = {
        "userid": _session["userid"],
        "token": _session["token"],
        "total_ver": 979,
        "type": 2,
        "page": int(page or 1),
        "pagesize": int(size or 50),
    }
    body_json = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    params = {"plat": "1", "userid": _session["userid"], "token": _session["token"]}
    params = _signed_params(params, dfid="-")
    params["signature"] = _sign_with_body({k: v for k, v in params.items() if k != "signature"}, body_json)
    try:
        with _client() as client:
            resp = client.post(f"{CLOUD_HOST}/v7/get_all_list", params=params,
                               content=body_json.encode("utf-8"),
                               headers={**_headers(params, "cloudlist.service.kugou.com"),
                                        "Content-Type": "application/json; charset=utf-8"})
            data = resp.json()
    except Exception as e:
        return {"ok": False, "error": f"获取歌单失败: {e}", "playlists": []}

    info = (data.get("data") or {}).get("info") or []
    playlists = []
    for item in info:
        if not isinstance(item, dict):
            continue
        listid = item.get("listid") or item.get("list_create_listid") or item.get("global_collection_id") or ""
        playlists.append({
            "id": str(listid),
            "name": item.get("listname") or item.get("name") or "未命名歌单",
            "count": item.get("count") or item.get("songcount") or 0,
            "cover": _cover(item.get("pic") or item.get("cover") or ""),
            "creator": item.get("nickname") or _session.get("nickname", ""),
            "list_type": item.get("list_type") or item.get("type") or 0,
        })
    return {"ok": True, "playlists": playlists, "count": len(playlists),
            "raw_status": data.get("status"), "error": data.get("error") if not playlists else None}


def playlist_songs(listid: str, page: int = 1, size: int = 30) -> Dict[str, Any]:
    """获取某个歌单里的歌曲"""
    if not is_logged_in():
        return {"ok": False, "error": "请先登录酷狗", "need_login": True, "songs": []}
    if not listid:
        return {"ok": False, "error": "缺少歌单 id", "songs": []}
    body = {
        "listid": str(listid),
        "userid": _session["userid"],
        "area_code": 1,
        "show_relate_goods": 0,
        "pagesize": int(size or 30),
        "allplatform": 1,
        "show_cover": 1,
        "type": 0,
        "token": _session["token"],
        "page": int(page or 1),
    }
    body_json = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    params = _signed_params({"plat": "1", "userid": _session["userid"], "token": _session["token"]}, dfid="-")
    params["signature"] = _sign_with_body({k: v for k, v in params.items() if k != "signature"}, body_json)
    try:
        with _client() as client:
            resp = client.post(f"{CLOUD_HOST}/v4/get_list_all_file", params=params,
                               content=body_json.encode("utf-8"),
                               headers={**_headers(params, "cloudlist.service.kugou.com"),
                                        "Content-Type": "application/json; charset=utf-8"})
            data = resp.json()
    except Exception as e:
        return {"ok": False, "error": f"获取歌单歌曲失败: {e}", "songs": []}

    info = (data.get("data") or {}).get("info") or data.get("info") or []
    songs = []
    for item in info:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        artist = ""
        singers = item.get("singerinfo") or []
        if isinstance(singers, list):
            names = [str(x.get("name") or "") for x in singers if isinstance(x, dict)]
            artist = "/".join([n for n in names if n])
        if " - " in name:
            left, right = name.split(" - ", 1)
            if not artist:
                artist = left.strip()
            title = right.strip()
        else:
            title = name.strip()
        try:
            duration = int(int(item.get("timelen") or 0) / 1000)
        except (TypeError, ValueError):
            duration = 0
        for _ext in (".mp3", ".flac", ".m4a", ".ape", ".wav"):
            if title.lower().endswith(_ext):
                title = title[: -len(_ext)].strip()
                break
        songs.append({
            "source": "kugou",
            "hash": item.get("hash") or "",
            "mixsong_id": item.get("mixsongid") or item.get("audio_id") or "",
            "title": title,
            "artist": artist,
            "album": ((item.get("albuminfo") or {}).get("name") if isinstance(item.get("albuminfo"), dict) else "") or "",
            "album_id": str(item.get("album_id") or "0"),
            "duration": duration,
            "cover": _cover(item.get("cover") or ""),
            "pay_type": item.get("media_pay_type"),
            "size": item.get("size") or 0,
            "playable_hint": (item.get("media_pay_type") in (0, None)),
        })
    return {"ok": True, "songs": songs, "count": len(songs), "error": data.get("error")}


# 进程启动时恢复上次的登录态
load_session()
