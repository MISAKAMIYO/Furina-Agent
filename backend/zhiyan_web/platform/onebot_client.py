"""
QQ 平台 OneBot 11 正向 WebSocket 客户端
========================================

实现「客户端模式」的 OneBot 11 协议（NapCat / go-cqhttp 等均兼容）：
- 以 WebSocket **客户端** 身份连接 NapCat 的「正向 WebSocket」服务端
- 支持 Access Token 鉴权（Authorization 请求头 + access_token 查询参数）
- 通过 echo 关联码实现请求/响应（call_api）
- 分发服务端主动推送的事件（message / notice / request / meta_event）
- 心跳与生命周期事件驱动的在线状态/presence 维护
- 断线自动重连（指数退避）

不依赖任何第三方 QQ 库，仅使用标准 asyncio + websockets。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Coroutine, Dict, Optional

logger = logging.getLogger("ZhiYanWeb.QQPlatform")

# 单条消息/响应的最大字节数（QQ 图片等大段消息放行）
MAX_FRAME_SIZE = 64 * 1024 * 1024

# 「原始事件回调」签名：async def on_event(raw: dict, client) -> None
MessageHandler = Callable[..., Awaitable[None]]


def _generate_echo() -> str:
    """生成唯一的请求关联码（echo）"""
    return uuid.uuid4().hex


def build_media_segment(media_type: str, file: str, **extra) -> Dict[str, Any]:
    """构建 OneBot 11 媒体消息段（发送用）。

    支持的 media_type：
      image  —— 图片，file 可为本地绝对路径 / http(s) URL / base64://... / file:///
      record —— 语音，file 同上（NapCat 需符合 QQ 语音格式，如 amr/silk/mp3/wav）
      video  —— 短视频，file 同上
      file   —— 普通文件
    与 NapCat 消息格式兼容（见 napneko 文档 image/record/video）。
    """
    data: Dict[str, Any] = {"file": file}
    data.update(extra or {})
    return {"type": media_type, "data": data}


def path_to_qq_file(file: str) -> str:
    """把本地文件路径规整为 NapCat 可识别的 file 参数：
    - URL / base64:// 原样返回
    - file:// 本地 URL → 还原为普通绝对路径
    - 本地绝对路径 → 正斜杠绝对路径（NapCat 直接读取本地文件；
      不能转成 file:///，否则 NapCat 会把路径当作相对路径拼到自身工作目录下，
      导致找不到文件，例如 G:\\d:\\Python\\...\\xx.mp4）
    """
    s = (file or "").strip()
    if not s:
        return s
    if s.startswith(("http://", "https://", "base64://")):
        return s
    if s.startswith("file://"):
        p = s[len("file://"):].lstrip("/")
        try:
            from urllib.parse import unquote
            p = unquote(p)
        except Exception:
            pass
        return p.replace("\\", "/")
    try:
        return os.path.abspath(s).replace("\\", "/")
    except Exception:
        return s.replace("\\", "/")


def append_query_token(ws_url: str, access_token: str) -> str:
    """把 access_token 追加到 ws url 的查询参数中（兼容仅支持 query 鉴权的实现）"""
    if not access_token or ws_url.startswith("ws://"):
        pass
    separator = "&" if "?" in ws_url else "?"
    return f"{ws_url}{separator}access_token={access_token}"


class OneBotClient:
    """OneBot 11 正向 WebSocket 客户端

    属性:
        ws_url: OneBot WebSocket 服务地址，如 ws://127.0.0.1:3001
        access_token: 鉴权令牌（可为空）
        self_id: 机器人 QQ 号（可选，用于 at 唤醒判定）
        on_event: 事件回调（可选），async def on_event(raw: dict)
        on_state_change: 连接状态变化回调（可选）async def on_state_change(state: str)
        on_request: 请求回调（可选）async def on_request(action, params)
                  —— 用于在收到 API 请求（NapCat「客户端收发」模式返回）时统一发送
    """

    def __init__(
        self,
        ws_url: str = "ws://127.0.0.1:3001",
        access_token: str = "",
        self_id: str = "",
        on_event: Optional[MessageHandler] = None,
        on_state_change: Optional[Callable[[str], Awaitable[None]]] = None,
        on_request: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        self.ws_url = ws_url
        self.access_token = access_token
        self.self_id = self_id
        self.on_event = on_event
        self.on_state_change = on_state_change
        self.on_request = on_request

        self._ws: Any = None
        self._task: Optional[asyncio.Task] = None
        self._stopped = False
        self._send_lock: asyncio.Lock = asyncio.Lock()
        self._pending: Dict[str, asyncio.Future] = {}  # echo -> Future
        self._consecutive_failures = 0

        # 连接状态
        self.connected = False
        self.connecting = False
        self.last_connected_at: Optional[float] = None
        self.last_disconnected_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.meta_online = False      # 依据 meta_event 的在线状态
        self.self_info: Optional[dict] = None
        self.last_heartbeat: Optional[float] = None
        self.bot_nickname: str = ""
        self.protocol_version: str = ""

    # ──────────────── 生命周期 ────────────────

    @property
    def is_online(self) -> bool:
        return self.connected and self.meta_online

    async def start(self) -> None:
        """启动客户端（创建后台重连任务），若已启动则忽略"""
        if self._task and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run_loop(), name="onebot-client")
        logger.info(f"QQ 平台客户端已启动，目标: {self.ws_url}")

    async def stop(self) -> None:
        """停止客户端并关闭连接"""
        self._stopped = True
        await self._close_ws()
        if self._task and not self._task.done():
            try:
                self._task.cancel()
            except Exception:
                pass
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        await self._fail_all_pending("client stopped")
        self._notify_state("stopped")
        logger.info("QQ 平台客户端已停止")

    async def _run_loop(self) -> None:
        """主循环：连接 → 接收 → 断线重连（指数退避）"""
        backoff = 1.0
        while not self._stopped:
            try:
                await self._connect_and_receive()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_error = str(e)
                self._consecutive_failures += 1
                logger.warning(f"QQ 平台连接异常: {e}")

            if self._stopped:
                break

            # 断线退避
            # 注意：_notify_state 是同步函数（内部用 create_task 触发回调），不能 await
            self._notify_state("reconnecting")
            delay = min(backoff, 60.0)
            logger.info(f"QQ 平台将在 {delay:.1f}s 后重连（第 {self._consecutive_failures} 次失败）")
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
            backoff *= 2 if self._consecutive_failures > 1 else 1
            if delay >= 60.0:
                # 持续失败时周期性重置退避，避免永远停留在最大值
                backoff = 2.0

    async def _connect_and_receive(self) -> None:
        """建立连接并进入接收循环（正常关闭/异常均返回，由外层决定是否重连）
        """
        if not self.ws_url:
            raise RuntimeError("未配置 WebSocket 地址 (ws_url)")

        self.connecting = True
        self._notify_state("connecting")

        headers: Dict[str, str] = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        uri = append_query_token(self.ws_url, self.access_token)

        try:
            from websockets.asyncio.client import connect as ws_connect
            from websockets.exceptions import InvalidStatusCode
        except Exception as e:  # pragma: no cover
            self.last_error = f"websockets 库不可用: {e}"
            logger.error(self.last_error)
            raise

        self._ws = await ws_connect(
            uri,
            additional_headers=headers,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_size=MAX_FRAME_SIZE,
        )

        self.connected = True
        self.connecting = False
        self._consecutive_failures = 0
        self.last_connected_at = time.time()
        self.last_error = None
        self._notify_state("connected")

        logger.info(f"QQ 平台 WebSocket 已连接: {self.ws_url}")

        # 后台异步刷新登录信息（不阻断接收循环，避免错过来自服务端的事件）
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._safe_refresh_self_info(), name="onebot-refresh-self")
        except RuntimeError:
            pass

        try:
            while not self._stopped:
                raw = await self._ws.recv()
                if raw is None:
                    continue
                await self._dispatch_raw(raw)
        except (asyncio.CancelledError, Exception):
            raise
        finally:
            await self._close_ws()

    async def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        was_connected = self.connected
        self.connected = False
        self.connecting = False
        if was_connected:
            self.last_disconnected_at = time.time()
            self._notify_state("disconnected")

    def _notify_state(self, state: str) -> None:
        if self.on_state_change:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.on_state_change(state))
            except Exception:
                try:
                    asyncio.ensure_future(self.on_state_change(state))
                except Exception:
                    pass

    # ──────────────── 数据分发 ────────────────

    async def _dispatch_raw(self, raw) -> None:
        try:
            if isinstance(raw, bytes):
                payload = json.loads(raw.decode("utf-8"))
            else:
                payload = json.loads(raw)
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"QQ 平台收到无法解析的数据帧: {e}")
            return

        if isinstance(payload, list):
            for item in payload:
                await self._dispatch_payload(item)
            return

        await self._dispatch_payload(payload)

    async def _dispatch_payload(self, payload: dict) -> None:
        # 1) API 响应：带有 echo 关联码
        if "echo" in payload:
            await self._resolve_pending(payload)
            return

        post_type = payload.get("post_type")

        # 2) meta_event：生命周期 + 心跳（在线状态 / presence）
        if post_type == "meta_event":
            await self._handle_meta_event(payload)
            return

        # 3) 其他会话事件：message / notice / request
        if self.on_event:
            try:
                await self.on_event(payload)
            except Exception as e:
                logger.error(f"QQ 平台事件回调异常: {e}", exc_info=True)

    async def _handle_meta_event(self, payload: dict) -> None:
        meta_type = payload.get("meta_event_type", "")
        if meta_type == "lifecycle":
            sub_type = payload.get("sub_type", "")
            if sub_type == "connect":
                self.meta_online = True
                logger.info("QQ 平台 meta_event.lifecycle.connect")
            elif sub_type == "enable":
                self.meta_online = True
                logger.info("QQ 平台 meta_event.lifecycle.enable")
            elif sub_type == "disable":
                self.meta_online = False
                logger.info("QQ 平台 meta_event.lifecycle.disable")
            return

        if meta_type == "heartbeat":
            self.last_heartbeat = time.time()
            status = payload.get("status") or {}
            online = bool(status.get("online", True)) if status else True
            self.meta_online = online
            return

        # 其他 meta 事件原样上报
        if self.on_event:
            try:
                await self.on_event(payload)
            except Exception as e:
                logger.error(f"QQ 平台 meta_event 回调异常: {e}")

    # ──────────────── 请求 / 响应 关联 ────────────────

    async def call_api(self, action: str, params: Optional[dict] = None, timeout: float = 30.0) -> Dict[str, Any]:
        """调用 OneBot 11 API，并等待响应。

        返回:
            响应 payload（含 data/status/retcode）。超时或未连接时抛异常。
        """
        params = params or {}
        if not self._ws or not self.connected:
            raise ConnectionError("QQ 平台未连接")

        echo = _generate_echo()
        request = {"action": action, "params": params, "echo": echo}
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future

        try:
            async with self._send_lock:
                await self._ws.send(json.dumps(request, ensure_ascii=False))
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(f"QQ 平台 API 调用超时: {action}")
        finally:
            self._pending.pop(echo, None)

    async def _resolve_pending(self, payload: dict) -> None:
        echo = payload.get("echo")
        if echo is None:
            return
        future = self._pending.get(echo)
        if future and not future.done():
            future.set_result(payload)
            return
        # 未知 echo 的响应：可能是服务端对某些动作的被动确认，忽略

    async def _fail_all_pending(self, reason: str) -> None:
        for echo, future in list(self._pending.items()):
            if not future.done():
                future.set_exception(ConnectionError(f"QQ 平台连接已断开: {reason}"))
        self._pending.clear()

    # ──────────────── 便捷 API 封装 ────────────────

    async def send_private_msg(self, user_id, message, timeout: float = 30.0) -> Dict[str, Any]:
        return await self.call_api("send_private_msg", {"user_id": user_id, "message": message}, timeout=timeout)

    async def send_group_msg(self, group_id, message, timeout: float = 30.0) -> Dict[str, Any]:
        if isinstance(group_id, str) and group_id.lstrip("-").isdigit() and len(group_id) >= 5:
            group_id = int(group_id)
        return await self.call_api("send_group_msg", {"group_id": group_id, "message": message}, timeout=timeout)

    async def send_msg(self, message_type: str, target_id, message, timeout: float = 30.0) -> Dict[str, Any]:
        params: Dict[str, Any] = {"message_type": message_type, "message": message}
        if message_type == "group":
            params["group_id"] = target_id
        else:
            params["user_id"] = target_id
        return await self.call_api("send_msg", params, timeout=timeout)

    async def send_private_media(self, user_id, media_type: str, file: str,
                                 timeout: float = 30.0, **extra) -> Dict[str, Any]:
        """私聊发送媒体（image / record / video / file）。file 可为本地路径 / URL / base64://"""
        seg = build_media_segment(media_type, path_to_qq_file(file), **extra)
        return await self.send_private_msg(user_id, [seg], timeout=timeout)

    async def send_group_media(self, group_id, media_type: str, file: str,
                               timeout: float = 30.0, **extra) -> Dict[str, Any]:
        """群聊发送媒体（image / record / video / file）。file 可为本地路径 / URL / base64://"""
        seg = build_media_segment(media_type, path_to_qq_file(file), **extra)
        return await self.send_group_msg(group_id, [seg], timeout=timeout)

    async def send_media(self, ev, media_type: str, file: str,
                         timeout: float = 30.0, **extra) -> Dict[str, Any]:
        """按消息事件（私聊/群聊）自动选择通道发送媒体"""
        if ev and getattr(ev, "is_group", False):
            return await self.send_group_media(ev.group_id, media_type, file, timeout=timeout, **extra)
        user_id = getattr(ev, "user_id", None) or ev
        return await self.send_private_media(user_id, media_type, file, timeout=timeout, **extra)

    async def upload_private_file(self, user_id, file: str, name: str = "",
                                  timeout: float = 60.0) -> Dict[str, Any]:
        """上传私聊文件（NapCat upload_private_file）

        Args:
            user_id: 对方 QQ 号
            file: 本地文件路径
            name: 文件名（可选，默认取路径 basename）
        """
        params: Dict[str, Any] = {"user_id": user_id, "file": file}
        if name:
            params["name"] = name
        return await self.call_api("upload_private_file", params, timeout=timeout)

    async def upload_group_file(self, group_id, file: str, name: str = "",
                                timeout: float = 60.0) -> Dict[str, Any]:
        """上传群文件（NapCat upload_group_file）

        Args:
            group_id: 群号
            file: 本地文件路径
            name: 文件名（可选，默认取路径 basename）
        """
        if isinstance(group_id, str) and group_id.lstrip("-").isdigit() and len(group_id) >= 5:
            group_id = int(group_id)
        params: Dict[str, Any] = {"group_id": group_id, "file": file}
        if name:
            params["name"] = name
        return await self.call_api("upload_group_file", params, timeout=timeout)

    async def get_login_info(self, timeout: float = 10.0) -> Dict[str, Any]:
        return await self.call_api("get_login_info", {}, timeout=timeout)

    async def get_friend_list(self, timeout: float = 15.0) -> Dict[str, Any]:
        return await self.call_api("get_friend_list", {}, timeout=timeout)

    async def get_group_list(self, timeout: float = 15.0) -> Dict[str, Any]:
        return await self.call_api("get_group_list", {}, timeout=timeout)

    async def get_group_member_info(self, group_id, user_id, timeout: float = 15.0) -> Dict[str, Any]:
        return await self.call_api(
            "get_group_member_info",
            {"group_id": group_id, "user_id": user_id},
            timeout=timeout,
        )

    async def get_stranger_info(self, user_id, timeout: float = 10.0) -> Dict[str, Any]:
        """查询 QQ 账号信息（get_stranger_info），用于校验超级管理员等真实账号"""
        return await self.call_api("get_stranger_info", {"user_id": user_id}, timeout=timeout)

    async def _safe_refresh_self_info(self) -> None:
        """后台刷新登录信息的包装：捕获异常避免影响连接任务"""
        try:
            await self._refresh_self_info()
        except Exception as e:
            logger.debug(f"QQ 平台后台刷新登录信息失败: {e}")

    async def _refresh_self_info(self) -> None:
        resp = await self.get_login_info(timeout=10)
        data = (resp or {}).get("data") or {}
        login_ok = (resp or {}).get("status") in ("ok", "success")
        if data or login_ok:
            self.self_info = data
            self.self_id = str(data.get("user_id", self.self_id))
            self.bot_nickname = data.get("nickname", self.bot_nickname)
        else:
            logger.warning(f"QQ 平台 get_login_info 未返回有效数据: {resp}")