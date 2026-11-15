"""
QQ 开放平台机器人（官方 Bot API v2）

能力：
- AppID/AppSecret 换取 access_token（带缓存）
- 主动发送文本消息到 群 / 用户（C2C）/ 频道
- 供 BetterGI 事件通知复用

注意：
- 群与 C2C 的主动消息通常需要回复某条入站消息的 msg_id；没有 msg_id 时接口可能拒绝。
- 频道（channel）支持主动消息。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("ZhiYanWeb.QQOfficial")

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

_DEFAULT = {
    "enabled": False,
    "app_id": "",
    "app_secret": "",
    "target_type": "group",  # group / user / channel
    "target_id": "",
    "api_base": "https://api.sgroup.qq.com",
    "timeout": 20,
    "ws_enabled": True,      # 启用 WebSocket 接收消息（自动缓存 msg_id 供主动发送）
    "intents": 33554432,     # 1<<25：群与 C2C 事件；需要频道消息时可加 1<<9
    "command_enabled": True, # 入站消息按命令处理（bgi状态 / bgi一条龙 ...）
    "command_prefix": "bgi",
    "command_allowed_groups": [],   # 留空=不限制
    "command_allowed_users": [],    # 留空=不限制
}

_token_cache: Dict[str, Any] = {"token": "", "expires_at": 0.0}
_msg_cache: Dict[str, Dict[str, str]] = {"group": {}, "user": {}}
_msg_seq: Dict[str, int] = {}


def _cfg() -> Dict[str, Any]:
    from zhiyan_web.config import get_settings

    raw = get_settings().get("qq_official") or {}
    merged = dict(_DEFAULT)
    if isinstance(raw, dict):
        merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _decrypt_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    try:
        from zhiyan_web.config.settings import decrypt_value

        decrypted = decrypt_value(value)
        return decrypted if decrypted and not decrypted.startswith("ENC:") else value
    except Exception:
        return value


def get_config(mask: bool = True) -> Dict[str, Any]:
    cfg = _cfg()
    if mask and cfg.get("app_secret"):
        cfg["app_secret"] = "****"
    return cfg


def update_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    from zhiyan_web.config import get_settings, update_settings
    from zhiyan_web.config.settings import encrypt_value

    current = dict(get_settings().get("qq_official") or {})
    patch = dict(patch or {})
    secret = patch.get("app_secret")
    if secret is not None:
        if "****" in str(secret):
            patch.pop("app_secret", None)
        else:
            patch["app_secret"] = encrypt_value(str(secret))
    current.update({k: v for k, v in patch.items() if v is not None})
    update_settings({"qq_official": current})
    _token_cache["token"] = ""
    _token_cache["expires_at"] = 0.0
    return get_config(mask=True)


def configured() -> bool:
    cfg = _cfg()
    return bool(cfg.get("app_id") and _decrypt_secret(cfg.get("app_secret")) and cfg.get("target_id"))


async def _get_token() -> Dict[str, Any]:
    cfg = _cfg()
    app_id = str(cfg.get("app_id") or "").strip()
    secret = _decrypt_secret(cfg.get("app_secret"))
    if not app_id or not secret:
        return {"ok": False, "message": "未配置 AppID / AppSecret"}
    now = time.time()
    if _token_cache.get("token") and float(_token_cache.get("expires_at") or 0) > now + 60:
        return {"ok": True, "token": _token_cache["token"]}

    try:
        async with httpx.AsyncClient(timeout=float(cfg.get("timeout") or 20)) as client:
            resp = await client.post(TOKEN_URL, json={"appId": app_id, "clientSecret": secret})
            data = resp.json() if resp.content else {}
    except Exception as e:
        return {"ok": False, "message": f"获取 token 失败: {e}"}
    if resp.status_code != 200 or not isinstance(data, dict) or not data.get("access_token"):
        return {"ok": False, "message": f"获取 token 失败: HTTP {resp.status_code} {str(data)[:200]}"}

    token = str(data["access_token"])
    try:
        expires_in = int(data.get("expires_in") or 7200)
    except Exception:
        expires_in = 7200
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in
    return {"ok": True, "token": token}


def _resolve_target(cfg: Dict[str, Any]) -> tuple:
    ttype = str(cfg.get("target_type") or "group").strip().lower()
    tid = str(cfg.get("target_id") or "").strip()
    return ttype, tid


async def send_text(
    text: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    msg_id: str = "",
) -> Dict[str, Any]:
    cfg = _cfg()
    if not cfg.get("enabled"):
        return {"ok": False, "message": "QQ 官方机器人未启用"}
    ttype = (target_type or cfg.get("target_type") or "group").strip().lower()
    tid = str(target_id or cfg.get("target_id") or "").strip()
    if not tid:
        return {"ok": False, "message": "未配置发送目标"}

    token_result = await _get_token()
    if not token_result.get("ok"):
        return token_result
    token = token_result["token"]

    base = str(cfg.get("api_base") or "https://api.sgroup.qq.com").rstrip("/")
    if ttype == "group":
        url = f"{base}/v2/groups/{tid}/messages"
        payload: Dict[str, Any] = {"content": text, "msg_type": 0}
        reply_id = msg_id or _msg_cache["group"].get(tid, "")
        if reply_id:
            payload["msg_id"] = reply_id
            _msg_seq[reply_id] = _msg_seq.get(reply_id, 0) + 1
            payload["msg_seq"] = _msg_seq[reply_id]
    elif ttype == "user":
        url = f"{base}/v2/users/{tid}/messages"
        payload = {"content": text, "msg_type": 0}
        reply_id = msg_id or _msg_cache["user"].get(tid, "")
        if reply_id:
            payload["msg_id"] = reply_id
            _msg_seq[reply_id] = _msg_seq.get(reply_id, 0) + 1
            payload["msg_seq"] = _msg_seq[reply_id]
    elif ttype == "channel":
        url = f"{base}/channels/{tid}/messages"
        payload = {"content": text}
        if msg_id:
            payload["msg_id"] = msg_id
    else:
        return {"ok": False, "message": f"不支持的目标类型: {ttype}"}

    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=float(cfg.get("timeout") or 20)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            body = resp.text[:200]
    except Exception as e:
        return {"ok": False, "message": f"发送失败: {e}"}

    if resp.status_code not in (200, 201, 204):
        hint = ""
        if ttype in ("group", "user") and not msg_id:
            hint = "（群/C2C 主动消息通常需要 msg_id，需先收到一条入站消息）"
        return {"ok": False, "message": f"发送失败: HTTP {resp.status_code} {body}{hint}"}
    logger.info(f"[QQ官方] 已发送到 {ttype}:{tid}")
    return {"ok": True, "message": "已发送", "target_type": ttype, "target_id": tid}


async def send_image(
    image_path: str,
    text: str = "",
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    msg_id: str = "",
) -> Dict[str, Any]:
    """发送图片（富媒体）：先上传文件拿 file_info，再以 msg_type=7 发送

    频道暂不支持本地图片上传，需改用 URL 媒体，见官方文档。
    """
    import os

    cfg = _cfg()
    if not cfg.get("enabled"):
        return {"ok": False, "message": "QQ 官方机器人未启用"}
    if not image_path or not os.path.isfile(image_path):
        return {"ok": False, "message": f"图片不存在: {image_path}"}
    ttype = (target_type or cfg.get("target_type") or "group").strip().lower()
    tid = str(target_id or cfg.get("target_id") or "").strip()
    if not tid:
        return {"ok": False, "message": "未配置发送目标"}
    if ttype not in ("group", "user"):
        return {"ok": False, "message": f"目标类型 {ttype} 暂不支持本地图片，请改用频道文本或 NapCat"}

    token_result = await _get_token()
    if not token_result.get("ok"):
        return token_result
    token = token_result["token"]

    base = str(cfg.get("api_base") or "https://api.sgroup.qq.com").rstrip("/")
    scope = "groups" if ttype == "group" else "users"
    upload_url = f"{base}/v2/{scope}/{tid}/files"
    headers = {"Authorization": f"QQBot {token}"}
    timeout = float(cfg.get("timeout") or 20)

    try:
        with open(image_path, "rb") as f:
            payload = f.read()
    except Exception as e:
        return {"ok": False, "message": f"读取图片失败: {e}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                upload_url,
                headers=headers,
                data={"file_type": "1", "srv_send_msg": "false"},
                files={"file_data": (os.path.basename(image_path), payload, "image/png")},
            )
            try:
                upload_body = resp.json()
            except Exception:
                upload_body = {}
            if resp.status_code not in (200, 201, 204):
                return {"ok": False, "message": f"上传图片失败: HTTP {resp.status_code} {resp.text[:160]}"}
            file_info = upload_body.get("file_info") or ""
            if not file_info:
                return {"ok": False, "message": f"上传图片未返回 file_info: {resp.text[:160]}"}

            message: Dict[str, Any] = {
                "content": text or " ",
                "msg_type": 7,
                "media": {"file_info": file_info},
            }
            reply_id = msg_id or _msg_cache.get("group" if ttype == "group" else "user", {}).get(tid, "")
            if reply_id:
                message["msg_id"] = reply_id
                _msg_seq[reply_id] = _msg_seq.get(reply_id, 0) + 1
                message["msg_seq"] = _msg_seq[reply_id]

            send_url = f"{base}/v2/{scope}/{tid}/messages"
            resp2 = await client.post(send_url, headers=headers, json=message)
            if resp2.status_code not in (200, 201, 204):
                return {"ok": False, "message": f"发送图片失败: HTTP {resp2.status_code} {resp2.text[:160]}"}
    except Exception as e:
        return {"ok": False, "message": f"发送图片异常: {e}"}

    logger.info(f"[QQ官方] 已发送图片到 {ttype}:{tid}")
    return {"ok": True, "message": "已发送图片", "target_type": ttype, "target_id": tid}


async def gateway_status() -> Dict[str, Any]:
    """网关状态：配置 + token + WS 连接 + 消息/命令计数"""
    data = await status()
    client = get_client()
    data["gateway"] = {
        "running": client.running,
        "connected": client.connected,
        "last_error": client.last_error,
        "messages": len(client.messages),
        "commands": client.commands_handled,
        "last_message": client.last_message,
    }
    return data


async def validate_config() -> Dict[str, Any]:
    """保存前校验：真正去换一次 token，避免带着错配置跑"""
    cfg = _cfg()
    if not cfg.get("enabled"):
        return {"ok": False, "message": "QQ 官方机器人未启用"}
    if not configured():
        return {"ok": False, "message": "缺少 AppID / AppSecret / 发送目标"}
    result = await _get_token()
    if result.get("ok"):
        return {
            "ok": True,
            "message": "校验通过，token 已获取",
            "expires_at": _token_cache.get("expires_at") or 0,
        }
    return {"ok": False, "message": result.get("message") or "无法获取 token"}


async def restart_gateway() -> Dict[str, Any]:
    """只重启 WS 接收，不动配置"""
    client = get_client()
    await client.stop()
    await asyncio.sleep(0.5)
    result = await client.start()
    result["status"] = await status()
    return result


async def status() -> Dict[str, Any]:
    cfg = _cfg()
    return {
        "enabled": bool(cfg.get("enabled")),
        "configured": configured(),
        "target_type": cfg.get("target_type") or "group",
        "target_id": cfg.get("target_id") or "",
        "token_cached": bool(_token_cache.get("token")),
        "token_expires_at": _token_cache.get("expires_at") or 0,
        "ws_enabled": bool(cfg.get("ws_enabled")),
        "ws_running": get_client().running,
        "ws_connected": get_client().connected,
        "ws_last_error": get_client().last_error,
        "cached_targets": {k: len(v) for k, v in _msg_cache.items()},
        "last_message": get_client().last_message,
        "command_enabled": bool(cfg.get("command_enabled", True)),
        "command_prefix": cfg.get("command_prefix") or "bgi",
        "commands_handled": get_client().commands_handled,
        "last_command": (list(get_client().command_log) or [{}])[-1],
    }


# ============= WebSocket 接收（官方 Bot Gateway） =============

class QQOfficialClient:
    """官方机器人 WebSocket 客户端：接收消息并缓存 msg_id"""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._ws = None
        self.connected = False
        self.last_error = ""
        self.last_message: Dict[str, Any] = {}
        self.messages = deque(maxlen=30)
        self.command_log = deque(maxlen=20)
        self.commands_handled = 0
        self._seq: Optional[int] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> Dict[str, Any]:
        cfg = _cfg()
        if not cfg.get("enabled"):
            return {"ok": False, "message": "请先启用 QQ 官方机器人"}
        if not configured():
            return {"ok": False, "message": "请先填写 AppID / AppSecret / 目标"}
        if self.running:
            return {"ok": True, "message": "接收客户端已在运行"}
        self._task = asyncio.get_running_loop().create_task(self._run())
        return {"ok": True, "message": "接收客户端已启动"}

    async def stop(self) -> Dict[str, Any]:
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self.connected = False
        return {"ok": True, "message": "接收客户端已停止"}

    async def _run(self) -> None:
        while True:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = str(e)
                logger.warning(f"[QQ官方] 连接异常: {e}")
                self.connected = False
            await asyncio.sleep(5)

    async def _connect_once(self) -> None:
        import websockets

        cfg = _cfg()
        token_result = await _get_token()
        if not token_result.get("ok"):
            raise RuntimeError(token_result.get("message") or "获取 token 失败")
        token = token_result["token"]
        base = str(cfg.get("api_base") or "https://api.sgroup.qq.com").rstrip("/")

        async with httpx.AsyncClient(timeout=float(cfg.get("timeout") or 20)) as client:
            resp = await client.get(f"{base}/gateway", headers={"Authorization": f"QQBot {token}"})
            data = resp.json() if resp.content else {}
        gateway = str((data or {}).get("url") or "")
        if not gateway:
            raise RuntimeError(f"获取网关地址失败: HTTP {resp.status_code} {str(data)[:200]}")

        async with websockets.connect(gateway, ping_interval=None, close_timeout=5) as ws:
            self._ws = ws
            self.last_error = ""
            logger.info(f"[QQ官方] 已连接网关 {gateway}")
            hello = json.loads(await ws.recv())
            interval = float((hello.get("d") or {}).get("heartbeat_interval") or 30000) / 1000.0
            self._heartbeat_task = asyncio.get_running_loop().create_task(self._heartbeat(ws, interval))
            try:
                await ws.send(
                    json.dumps(
                        {
                            "op": 2,
                            "d": {
                                "token": f"QQBot {token}",
                                "intents": int(cfg.get("intents") or 33554432),
                                "shard": [0, 1],
                                "properties": {"$os": "windows", "$browser": "zhiyan", "$device": "desktop"},
                            },
                        }
                    )
                )
                self.connected = True
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        continue
                    self._handle_payload(payload)
            finally:
                self.connected = False
                hb = self._heartbeat_task
                self._heartbeat_task = None
                if hb and not hb.done():
                    hb.cancel()

    async def _heartbeat(self, ws, interval: float) -> None:
        try:
            while True:
                await ws.send(json.dumps({"op": 1, "d": self._seq}))
                await asyncio.sleep(max(5.0, interval))
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    def _handle_payload(self, payload: Dict[str, Any]) -> None:
        op = payload.get("op")
        if op == 0:
            self._seq = payload.get("s") or self._seq
            event = str(payload.get("t") or "")
            data = payload.get("d") or {}
            self._cache_message(event, data)
        elif op == 11:
            pass

    def _cache_message(self, event: str, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        msg_id = str(data.get("id") or "")
        if not msg_id:
            return
        author = data.get("author") or {}
        content = str(data.get("content") or "").strip()
        item = {
            "ts": time.time(),
            "event": event,
            "msg_id": msg_id,
            "content": content,
            "group_openid": data.get("group_openid") or "",
            "user_openid": author.get("user_openid") or data.get("openid") or "",
        }
        if item["group_openid"]:
            _msg_cache["group"][item["group_openid"]] = msg_id
        if item["user_openid"]:
            _msg_cache["user"][item["user_openid"]] = msg_id
        self.last_message = item
        self.messages.append(item)
        logger.info(f"[QQ官方] 收到消息 {event}: {content[:60]!r}")

        if _cfg().get("command_enabled", True):
            try:
                asyncio.get_running_loop().create_task(self._handle_command(item))
            except RuntimeError:
                pass

    async def _handle_command(self, item: Dict[str, Any]) -> None:
        cfg = _cfg()
        prefix = str(cfg.get("command_prefix") or "bgi").strip().lower()
        raw = str(item.get("content") or "").strip()
        if raw.startswith("/"):
            raw = raw[1:].strip()
        if not prefix or not raw.lower().startswith(prefix):
            return

        allowed_groups = cfg.get("command_allowed_groups") or []
        allowed_users = cfg.get("command_allowed_users") or []
        gid = item.get("group_openid") or ""
        uid = item.get("user_openid") or ""
        if allowed_groups and gid not in allowed_groups:
            return
        if allowed_users and uid not in allowed_users:
            return

        body = raw[len(prefix):].strip()
        parts = body.replace("，", ",").split()
        cmd = (parts[0] if parts else "").lower()
        args = parts[1:]

        read_only = ("", "帮助", "help", "菜单", "状态", "status")
        if cmd not in read_only and not (allowed_groups or allowed_users):
            reply = "未授权：请先在官方机器人配置中填写允许的群/用户（command_allowed_groups / command_allowed_users）"
        else:
            reply = await self._run_command(cmd, args)

        self.commands_handled += 1
        self.command_log.append(
            {"ts": time.time(), "command": raw, "reply": reply, "group_openid": gid, "user_openid": uid}
        )
        await self._reply(item, reply)

    async def _run_command(self, cmd: str, args: list) -> str:
        from zhiyan_web.bettergi.service import get_bettergi_manager

        mgr = get_bettergi_manager()
        if cmd in ("", "帮助", "help", "菜单"):
            return (
                "BetterGI 命令：\n"
                "bgi状态 —— 查看运行/任务状态\n"
                "bgi启动 [normal|onedragon|groups|task_progress] —— 启动\n"
                "bgi一条龙 [配置名] —— 一条龙\n"
                "bgi配置组 组名1,组名2 —— 配置组\n"
                "bgi停止 —— 结束 BetterGI\n"
                "bgi兜底 —— 立即图像识别兜底\n"
                "bgi取消关机 —— 取消自动关机\n"
                "bgi定时 / bgi定时开 / bgi定时关 —— 定时任务\n"
                "bgi备份 / bgi重启 —— 备份配置 / 重启 BetterGI"
            )

        if cmd in ("状态", "status"):
            st = mgr.get_status()
            state_map = {"idle": "待机", "monitoring": "监控中", "running": "运行中", "starting": "启动中",
                         "restarting": "重启中", "alert": "异常", "stopped": "已停止"}
            task_map = {"idle": "空闲", "running": "进行中", "done": "已完成", "error": "异常", "canceled": "已取消"}
            return (
                "BetterGI 状态：%s\n进程：%s\n监控：%s\n任务：%s\n最近事件：%s\n最近结果：%s"
                % (
                    state_map.get(st.get("state"), st.get("state")),
                    "存活" if st.get("running") else "未运行",
                    "开启" if st.get("monitoring") else "关闭",
                    task_map.get(st.get("task_state"), st.get("task_state")),
                    st.get("last_event", {}).get("event") or "无",
                    st.get("last_task_result") or "无",
                )
            )

        if cmd in ("启动", "start"):
            mode = (args[0].lower() if args else "normal")
            if mode not in ("normal", "onedragon", "groups", "task_progress"):
                return "未知启动模式：%s" % mode
            result = mgr.launch(mode=mode, force_args=True)
            return "启动结果：%s" % (result.get("message") or "")

        if cmd in ("一条龙", "onedragon"):
            config_name = args[0] if args else ""
            result = mgr.launch(mode="onedragon", config_name=config_name, force_args=True)
            return "一条龙：%s" % (result.get("message") or "")

        if cmd in ("配置组", "groups", "组"):
            names = ",".join(args) if args else ""
            if not names:
                return "用法：bgi配置组 组名1,组名2"
            result = mgr.launch(mode="groups", group_names=names, force_args=True)
            return "配置组：%s" % (result.get("message") or "")

        if cmd in ("任务进度", "progress", "taskprogress"):
            names = ",".join(args) if args else ""
            result = mgr.launch(mode="task_progress", group_names=names, force_args=True)
            return "任务进度：%s" % (result.get("message") or "")

        if cmd in ("停止", "stop", "结束", "kill"):
            result = mgr.kill()
            return "停止结果：%s" % (result.get("message") or "")

        if cmd in ("兜底", "恢复", "recover"):
            result = await mgr.vision_recover_once()
            return "图像兜底：%s" % (result.get("message") or "")
        if cmd in ("取消关机", "cancel"):
            result = mgr.cancel_shutdown()
            return "取消关机：%s" % (result.get("message") or "")

        if cmd in ("定时", "定时状态", "schedule"):
            st = mgr.schedule_status()
            return (
                "定时任务：%s\n调度器：%s\n下次任务：%s\n任务数：%d"
                % (
                    "已启用" if st.get("enabled") else "未启用",
                    "运行中" if st.get("running") else "未运行",
                    st.get("next_run") or "无",
                    len(st.get("schedules") or []),
                )
            )
        if cmd in ("定时开", "定时开启"):
            mgr.update_config({"schedule_enabled": True})
            result = await mgr.start_scheduler()
            return "定时任务：%s" % (result.get("message") or "")
        if cmd in ("定时关", "定时关闭"):
            mgr.update_config({"schedule_enabled": False})
            result = await mgr.stop_scheduler()
            return "定时任务：%s" % (result.get("message") or "")
        if cmd in ("备份", "backup"):
            result = await asyncio.to_thread(mgr.update_backup)
            return "备份：%s" % (result.get("message") or "")
        if cmd in ("重启", "restart"):
            result = await mgr.restart_bgi()
            return "重启：%s" % (result.get("message") or "")
        return "未知命令：%s（发送 bgi帮助 查看用法）" % cmd

    async def _reply(self, item: Dict[str, Any], text: str) -> None:
        gid = item.get("group_openid") or ""
        uid = item.get("user_openid") or ""
        try:
            if gid:
                await send_text(text, target_type="group", target_id=gid, msg_id=item.get("msg_id") or "")
            elif uid:
                await send_text(text, target_type="user", target_id=uid, msg_id=item.get("msg_id") or "")
        except Exception as e:
            logger.warning(f"[QQ官方] 命令回复失败: {e}")


_client: Optional[QQOfficialClient] = None


def get_client() -> QQOfficialClient:
    global _client
    if _client is None:
        _client = QQOfficialClient()
    return _client


def recent_messages(limit: int = 20) -> list:
    return list(get_client().messages)[-limit:][::-1]
