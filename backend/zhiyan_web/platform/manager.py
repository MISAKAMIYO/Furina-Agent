"""
QQ 平台接入管理器
=================

统一管理 QQ 平台（NapCat OneBot 11）的：
- 配置加载 / 启停 / 重连
- 消息接收 → 分发到本软件既有对话管道（命令走 pipeline，普通消息走 Coordinator）
- 回复发送（私聊 / 群聊）
- 用户与群在线状态（presence）跟踪
- 断线自动恢复

接入方式与 zhiyan_web/chat/routers/websocket.py 的既有入口保持一致：
命令消息（/ ! 前缀 / @/私聊唤醒）→ chat_manager.process_via_pipeline
普通闲聊 → agents.coordinator 处理。
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional

from .onebot_client import OneBotClient, build_media_segment, path_to_qq_file
from .qq_event import QQMessageEvent, QQMessageEventAdapter
from .permission import (
    check_access,
    get_perm_cfg,
    is_admin_command,
    mutate_perm_list,
    user_level,
)

logger = logging.getLogger("ZhiYanWeb.QQPlatform")

# DeepSeek 思考模式泄漏标记（provider 会把每个 reasoning delta 包成 __REASONING__: 前缀）


def strip_reasoning(text: str) -> str:
    """移除回复正文中泄漏的 __REASONING__: 思考内容（防御性兜底）

    流式结构：多个 `__REASONING__:<推理片段>` 依次拼接，末尾为最终回复（无前缀）。
    处理：丢弃第一个标记之后、最后一个标记之前的推理片段；
    最后一个片段之后无法自动区分"推理尾"与"最终回复"，保守保留，
    避免像贪婪正则那样把最终回复一并误删。
    """
    if not text or "__REASONING__:" not in text:
        return text
    parts = text.split("__REASONING__:")
    return parts[0] + parts[-1]

# 媒体标记 → 媒体类型。agent / 插件可在回复文本中用
# [图片:路径] / [语音:路径] / [视频:路径] / [文件:路径]（兼容英文 image/record/video/file）
# 指示随文发送媒体。文本中原样携带的 [CQ:image,...] 等由 NapCat 直接解析，无需转换。
_MEDIA_MARKER_RE = re.compile(
    r"\[(图片|语音|视频|文件|image|record|video|file):([^\]]+?)\]", re.IGNORECASE
)
_MEDIA_TYPE_MAP = {
    "图片": "image", "image": "image",
    "语音": "record", "record": "record",
    "视频": "video", "video": "video",
    "文件": "file", "file": "file",
}

# 回复文本中的 @ 标记：插件通过 MessageChain + At 组件生成 `@{qq}`，
# 发送前解析为 OneBot 11 的 at 消息段（真实 @ 对方）。
_AT_MARKER_RE = re.compile(r"@\{(\d+)\}")
# 匹配 `@机器人` / `@全体成员` / `@<qq号>` 的开头标记（to_text 会把 at 机器人渲染成 "@机器人"）。
# 在主处理入口去除它，使"@机器人 /miao帮助"被识别为命令，而非因消息以 @ 开头而漏判。
_LEAD_AT_MENTION_RE = re.compile(r"^@(?:机器人|全体成员|\d+)\s*")

# ────────────── 会话历史 / 评价功能 ──────────────
_MAX_MSG_HISTORY = 30          # 每会话保留的最近消息事件条数
_EVAL_MAX_MSGS = 20            # 评价时读取的最大消息条数（上限 20 条）
_EVALUATION_KEYWORDS = ("评价", "点评", "评估", "review", "evaluate")
_MEDIA_NAME_MAP = {"image": "图片", "record": "语音", "video": "视频", "file": "文件"}


def extract_media_markers(text: str):
    """从回复文本中提取媒体标记，返回 (media_segments, clean_text)。

    媒体标记格式：
      [图片:本地路径或URL或base64://...]
      [语音:本地路径或URL]
      [视频:本地路径或URL]
    标记会被移除并转为 OneBot 11 媒体消息段；剩余文本作为正文。
    """
    if not text:
        return [], ""
    media_segments = []
    clean_parts = []
    last = 0
    for m in _MEDIA_MARKER_RE.finditer(text):
        clean_parts.append(text[last:m.start()])
        media_type = _MEDIA_TYPE_MAP.get(m.group(1).lower())
        file_ref = (m.group(2) or "").strip()
        if media_type and file_ref:
            media_segments.append(build_media_segment(media_type, path_to_qq_file(file_ref)))
        else:
            clean_parts.append(m.group(0))
        last = m.end()
    clean_parts.append(text[last:])
    return media_segments, "".join(clean_parts)


def parse_at_markers(text: str):
    """从回复文本中解析 `@{qq}` 标记，转为 OneBot 11 的 at 消息段。

    Returns:
        (segments, has_at)：segments 为混合文本/at 段列表；
        无 at 标记时返回 (None, False)。
    """
    if not text or "@{" not in text:
        return None, False
    segments = []
    last = 0
    for m in _AT_MARKER_RE.finditer(text):
        if m.start() > last:
            segments.append({"type": "text", "data": {"text": text[last:m.start()]}})
        segments.append({"type": "at", "data": {"qq": m.group(1)}})
        last = m.end()
    if last < len(text):
        segments.append({"type": "text", "data": {"text": text[last:]}})
    return segments, True

# 配置默认值（通过 settings 的 qq_platform 覆盖）
_DEFAULTS = {
    "enabled": False,
    "ws_url": "ws://127.0.0.1:3001",
    "access_token": "",
    "self_id": "",
    "wake_prefixes": ["/", "!"],
    # 群聊回应模式:
    #   all          —— 群内所有消息都回应
    #   at_or_command —— 被 @ 或命中命令前缀才回应（默认）
    #   command       —— 仅命中命令前缀才回应
    "group_respond_mode": "at_or_command",
    "owner_only_llm": False,        # 仅超级管理员（主人）的对话才调用 LLM；开启后非主人不触发 agent
    "max_message_chars": 500,
    "call_timeout": 30,
    "min_send_interval": 1.2,       # 全局发送最小间隔（秒），避免快速连发触发风控
    "max_send_per_minute": 40,      # 每分钟最大发送条数（超限自动额外等待）
    "user_cooldown": 2.0,           # 单用户消息处理最小间隔（秒），超频消息直接忽略（防刷屏）
    "reply_delay_min": 5,           # agent（LLM）回复的随机发送等待下限（秒）
    "reply_delay_max": 25,          # agent（LLM）回复的随机发送等待上限（秒）
    "plugin_reply_delay_enabled": True,  # 插件/命令回复是否也附加随机等待（避免"秒回"）
    "plugin_reply_delay_min": 3.0,   # 插件/命令回复随机等待下限（秒）
    "plugin_reply_delay_max": 10.0,  # 插件/命令回复随机等待上限（秒）
    "greet_on_connect": False,
    "greet_text": "芙宁娜已登台，有事唤本神便是。",
    # 洪水防护：短时间内大量请求全部忽略，仅处理超级管理员（master）请求
    "flood_window": 10,             # 洪水检测时间窗口（秒）
    "flood_threshold": 20,          # 窗口内全局消息数超过该值进入洪水模式
    "flood_cooldown": 30,           # 洪水模式持续时间（秒），期间仅处理 master
    "user_burst_limit": 5,          # 单用户在窗口内允许的最大消息数，超过则忽略该用户
}

# QQ 系统账号（入群欢迎 / 系统通知等消息体，全部忽略）
_SYSTEM_QQ_IDS = {"1000000", "80000000", "0", "-1"}


def _get_qq_platform_cfg() -> Dict[str, Any]:
    """从设置中读取 qq_platform 配置（与默认值合并）"""
    try:
        from zhiyan_web.config import get_settings
        raw = get_settings().get("qq_platform") or {}
    except Exception:
        raw = {}
    merged = dict(_DEFAULTS)
    merged.update(raw)
    return merged


class QQPlatformManager:
    """QQ 平台接入管理器单例"""

    def __init__(self) -> None:
        self.client: Optional[OneBotClient] = None
        self._state_log: List[Dict[str, Any]] = []
        self._running = False
        # presence 缓存
        self.presence: Dict[str, Any] = {
            "friends": [],       # [{user_id, nickname, remark}]
            "groups": [],        # [{group_id, group_name}]
            "presence": {},      # group/user 在线状态（预留）
        }
        self._last_presence_refresh = 0.0
        self.stats: Dict[str, Any] = {
            "total_messages": 0,
            "total_replies": 0,
            "last_message_at": None,
            "errors": 0,
        }
        # 发送限速（防封号）：串行化发送，保证间隔与每分钟上限
        self._send_lock = asyncio.Lock()
        self._last_send_time = 0.0
        self._send_times: List[float] = []
        # 无权限提示的冷却（防刷屏）
        self._deny_notify_at: Dict[str, float] = {}
        # 单用户消息处理冷却（消息频率限制）
        self._user_cooldown_at: Dict[str, float] = {}
        # 洪水防护状态
        self._msg_timestamps: List[float] = []              # 全局最近消息时间戳（滑动窗口）
        self._flood_until: float = 0.0                      # 洪水模式截止时间（期间仅处理 master）
        self._user_msg_times: Dict[str, List[float]] = {}   # 各用户最近消息时间戳（突发检测）
        # 消息事件历史（供 agent 评价会话历史；按会话隔离，上限 _MAX_MSG_HISTORY 条）
        self._msg_history: Dict[str, List[Dict[str, Any]]] = {}
        # 通过 napcat 校验得到的超级管理员账号信息
        self.master_info: Optional[Dict[str, Any]] = None

    # ──────────────── 生命周期 ────────────────

    async def start(self) -> Dict[str, Any]:
        cfg = _get_qq_platform_cfg()
        if self._running:
            return {"success": True, "message": "QQ 平台已在运行", "running": True}

        if not cfg.get("ws_url"):
            return {"success": False, "message": "未配置 WebSocket 地址"}

        self.client = OneBotClient(
            ws_url=cfg["ws_url"],
            access_token=cfg.get("access_token", ""),
            self_id=cfg.get("self_id", ""),
            on_event=self._on_event,
            on_state_change=self._on_state_change,
        )
        self._running = True
        self._push_state_log({"state": "starting", "time": time.time()})
        asyncio.create_task(self.client.start(), name="qq-platform-start")
        logger.info("QQ 平台管理器已启动")
        return {"success": True, "message": "QQ 平台启动任务已创建", "running": True}

    async def stop(self) -> Dict[str, Any]:
        if not self._running:
            return {"success": True, "message": "QQ 平台未在运行", "running": False}
        self._running = False
        if self.client:
            await self.client.stop()
        self.client = None
        self._push_state_log({"state": "stopped", "time": time.time()})
        logger.info("QQ 平台管理器已停止")
        return {"success": True, "message": "QQ 平台已停止", "running": False}

    async def restart(self) -> Dict[str, Any]:
        await self.stop()
        return await self.start()

    def reconfigure(self) -> None:
        """配置变更后由路由调用：若正在运行则重启客户端以应用新配置"""
        if self._running:
            asyncio.create_task(self.restart(), name="qq-platform-reconfigure")

    # ──────────────── 事件处理 ────────────────

    async def _on_state_change(self, state: str) -> None:
        self._push_state_log({"state": state, "time": time.time()})
        if state in ("connected", "connecting"):
            if state == "connected":
                await self._refresh_presence()
                await self._verify_master()
                cfg = _get_qq_platform_cfg()
                if cfg.get("greet_on_connect"):
                    await self._greet(cfg.get("greet_text", ""))

    async def _verify_master(self) -> None:
        """通过 napcat（get_stranger_info）校验超级管理员 QQ 号，记录账号信息"""
        try:
            if not self.client:
                return
            cfg = _get_qq_platform_cfg()
            master_id = str(cfg.get("master_id", "") or "")
            if not master_id:
                return
            info = await self.resolve_user_info(master_id)
            if info:
                self.master_info = info
                logger.info(
                    f"已通过 napcat 校验超级管理员: {master_id} ({info.get('nickname', '未知')})"
                )
        except Exception as e:
            logger.debug(f"校验超级管理员失败: {e}")

    async def resolve_user_info(self, user_id: Any) -> Optional[Dict[str, Any]]:
        """通过 napcat 查询 QQ 账号信息（get_stranger_info），失败时回退好友列表"""
        if not self.client:
            return None
        uid = str(user_id or "")
        if not uid:
            return None
        try:
            resp = await self.client.get_stranger_info(uid, timeout=8)
            if resp and resp.get("status") in ("ok", "success"):
                data = resp.get("data") or {}
                return {
                    "user_id": str(data.get("user_id", uid)),
                    "nickname": data.get("nickname", ""),
                    "remark": data.get("remark", ""),
                    "level": data.get("level", ""),
                }
        except Exception as e:
            logger.debug(f"get_stranger_info({uid}) 失败: {e}")
        # 回退：从好友列表匹配
        for f in self.presence.get("friends", []):
            if str(f.get("user_id", "")) == uid:
                return {
                    "user_id": uid,
                    "nickname": f.get("nickname", ""),
                    "remark": f.get("remark", ""),
                }
        return {"user_id": uid, "nickname": ""}

    async def _on_event(self, raw: dict) -> None:
        post_type = raw.get("post_type", "")
        try:
            if post_type == "message":
                await self._on_message(raw)
            elif post_type == "notice":
                await self._on_notice(raw)
            elif post_type == "request":
                await self._on_request(raw)
        except Exception as e:
            self.stats["errors"] += 1
            logger.error(f"QQ 平台事件处理异常: {e}", exc_info=True)

    async def _on_message(self, raw: dict) -> None:
        cfg = _get_qq_platform_cfg()
        self_id = cfg.get("self_id") or (self.client.self_id if self.client else "") or ""
        ev = QQMessageEvent.from_onebot(raw, self_id=cfg.get("self_id", ""))
        if self.client and self.client.self_id:
            ev.self_id = self.client.self_id

        # 忽略自己发来的消息
        if ev.user_id and str(ev.user_id) == str(ev.self_id):
            return

        # 忽略系统消息 / 入群欢迎等非用户主动消息
        if self._is_ignored_system_message(ev):
            logger.debug(
                f"QQ 系统/欢迎消息忽略 (type={ev.message_type}, user={ev.user_id}, "
                f"group={ev.group_id}, text={ev.to_text(self_id=ev.self_id)[:30]!r})"
            )
            return

        # 记录消息事件到会话历史（含图片/语音/视频/文件等非文本事件，供评价功能读取）
        self._record_message_history(ev)

        # 忽略无文本内容的消息（纯表情包/图片/视频/@等）：对所有人一律不回复
        if not self._has_text_content(ev):
            logger.debug(
                f"QQ 非文本消息忽略 (type={ev.message_type}, user={ev.user_id}, "
                f"group={ev.group_id}, text={ev.to_text(self_id=ev.self_id)[:30]!r})"
            )
            return

        self.stats["total_messages"] += 1
        self.stats["last_message_at"] = time.time()

        # 洪水防护：短时间内大量请求全部忽略，仅处理超级管理员（master）
        if self._is_flooding(ev, cfg):
            return

        # 权限与访问控制：黑名单 / 群管控 / 白名单模式
        perm_cfg = get_perm_cfg()
        allowed, level, deny_reason = check_access(ev, perm_cfg)
        if not allowed:
            # 非白名单用户：普通闲聊（如"不知道""区"）静默忽略；仅明确的命令/插件调用
            # （如 /miao、#miao、@bot /help）才提示无权限，避免骚扰。其他拒绝原因原样提示。
            if deny_reason != "not_whitelist" or self._is_key_usage(ev, cfg):
                await self._maybe_notify_denied(ev, cfg, deny_reason)
            logger.debug(
                f"QQ 消息被拒绝 (type={ev.message_type}, user={ev.user_id}, "
                f"group={ev.group_id}, reason={deny_reason}, level={level})"
            )
            return

        # 是否应该回应
        should_reply, reason = self._should_respond(ev, cfg)
        if not should_reply:
            logger.debug(f"QQ 消息不回应 (type={ev.message_type}, reason={reason})")
            return

        # 单用户消息频率限制（防刷屏 / 防 LLM 过载）：同一用户过频消息直接忽略
        now = time.time()
        cooldown = float(cfg.get("user_cooldown", 0.0))
        if cooldown > 0 and now - self._user_cooldown_at.get(ev.user_id, 0.0) < cooldown:
            logger.debug(
                f"QQ 用户 {ev.user_id} 消息过于频繁（冷却 {cooldown:.1f}s），忽略"
            )
            return
        self._user_cooldown_at[ev.user_id] = now

        text = ev.to_text(self_id=ev.self_id).strip()
        if not text:
            return
        # 长度保护
        max_chars = int(cfg.get("max_message_chars", 500))
        if len(text) > max_chars:
            text = text[:max_chars]

        # 异步执行，避免阻塞 WebSocket 接收循环
        asyncio.create_task(self._process_and_reply(ev, text, cfg), name="qq-process-reply")

    def _is_ignored_system_message(self, ev: QQMessageEvent) -> bool:
        """识别并忽略非用户主动的消息体（系统通知 / 入群欢迎等）。

        检测规则（参照 AstrBot / NapCat 常见实现）：
        - 发送者为 QQ 系统账号（1000000/80000000/0/-1）→ 忽略
        - 匿名消息（anonymous 字段存在）→ 忽略
        - 机器人自己被拉入群的欢迎消息：group_increase 由 notice 处理，
          此处对系统账号推送的「欢迎」文案同样忽略
        """
        if ev.user_id in _SYSTEM_QQ_IDS:
            return True
        sender = ev.raw.get("sender") or {}
        if sender.get("anonymous"):
            return True
        return False

    @staticmethod
    def _has_text_content(ev: QQMessageEvent) -> bool:
        """消息是否包含有效文本内容（用于过滤纯表情包/图片等非文本消息）"""
        for seg in ev.segments:
            if seg.type == "text":
                t = (seg.data.get("text") or "") if isinstance(seg.data, dict) else str(seg.data or "")
                if t.strip():
                    return True
        return False

    def _record_message_history(self, ev: QQMessageEvent) -> None:
        """记录消息事件到会话历史（按会话隔离，上限 _MAX_MSG_HISTORY 条）。

        含图片/语音/视频/文件等非文本事件，供超级管理员触发评价时读取。
        """
        try:
            seg_types = [s.type for s in ev.segments]
            entry: Dict[str, Any] = {
                "time": ev.time,
                "user_id": ev.user_id,
                "nickname": ev.nickname or ev.card or "",
                "message_type": ev.message_type,
                "group_id": ev.group_id,
                "message_id": ev.message_id,
                "text": ev.to_text(self_id=ev.self_id).strip(),
                "media": [t for t in seg_types if t in ("image", "record", "video", "file")],
            }
            history = self._msg_history.setdefault(ev.session_id, [])
            history.append(entry)
            if len(history) > _MAX_MSG_HISTORY:
                del history[:len(history) - _MAX_MSG_HISTORY]
        except Exception as e:
            logger.debug(f"记录 QQ 消息历史失败: {e}")

    async def _evaluate_and_reply(self, ev: QQMessageEvent, cfg: dict) -> bool:
        """超级管理员要求评价会话历史时，读取会话消息（上限 20 条）触发 LLM 评价。

        Returns: True 表示已处理（应停止后续流程）
        """
        try:
            from zhiyan_web.core import chat_manager as cm
            if not cm or not hasattr(cm, "one_shot_chat"):
                return False

            history = self._msg_history.get(ev.session_id, [])[-_EVAL_MAX_MSGS:]
            if not history:
                await self._send_reply(ev, "暂时没有可评价的会话记录。", cfg, humanized_delay=False)
                return True

            lines = []
            for h in history:
                name = h.get("nickname") or f"QQ{h.get('user_id')}"
                text = h.get("text") or ""
                media = h.get("media") or []
                if media:
                    media_desc = "、".join(_MEDIA_NAME_MAP.get(m, m) for m in media)
                    if text:
                        content = f"{text}（附{media_desc}）"
                    else:
                        content = f"发送了{media_desc}"
                else:
                    content = text or "[空白消息]"
                lines.append(f"- {name}: {content}")
            history_text = "\n".join(lines)

            system_prompt = (
                "你是一个客观、细致的会话分析助手。请对用户提供的 QQ 群聊/私聊消息记录进行评价。\n"
                "评价内容包括：\n"
                "1. 整体氛围与互动质量\n"
                "2. 各参与者的发言特点\n"
                "3. 是否有值得注意的话题或亮点\n"
                "4. 可以改进的地方（如有）\n"
                "语气自然、简洁，直接输出评价正文，控制在 200 字以内。"
            )
            user_content = (
                "以下是最新的 QQ 会话消息记录（共 {n} 条），请对这段会话进行评价：\n\n{history}".format(
                    n=len(history), history=history_text)
            )
            resp = await cm.one_shot_chat(system_prompt=system_prompt, user_content=user_content)
            resp = strip_reasoning((resp or "").strip())
            if not resp or resp.startswith("错误："):
                resp = resp or "评价生成失败，请稍后再试。"
            await self._send_reply(ev, resp, cfg, humanized_delay=True)
            return True
        except Exception as e:
            logger.error(f"QQ 会话评价失败: {e}", exc_info=True)
            await self._send_reply(ev, f"评价失败：{e}", cfg, humanized_delay=False)
            return True

    def _is_flooding(self, ev: QQMessageEvent, cfg: dict) -> bool:
        """洪水防护：短时间内大量请求全部忽略，只处理超级管理员（master）。

        两个维度：
        1. 全局洪水：窗口内全局消息数 ≥ flood_threshold → 进入洪水模式
           （flood_cooldown 秒），期间仅处理 master，其余全部忽略。
        2. 单用户突发：窗口内同一用户消息数 > user_burst_limit → 忽略该用户。

        Returns:
            True 表示本条消息应被忽略
        """
        if not ev.user_id:
            return False
        now = time.time()
        window = float(cfg.get("flood_window", 10))
        threshold = int(cfg.get("flood_threshold", 20))
        cooldown = float(cfg.get("flood_cooldown", 30))
        burst_limit = int(cfg.get("user_burst_limit", 5))

        level = user_level(ev.user_id, get_perm_cfg())

        # 记录时间戳
        self._msg_timestamps.append(now)
        self._msg_timestamps = [t for t in self._msg_timestamps if now - t < window]
        burst = self._user_msg_times.setdefault(ev.user_id, [])
        burst.append(now)
        burst[:] = [t for t in burst if now - t < window]

        # 已在洪水模式
        if now < self._flood_until:
            if level != "master":
                logger.info(
                    f"QQ 洪水模式中（剩余 {self._flood_until - now:.0f}s），"
                    f"用户 {ev.user_id} 请求忽略（仅处理超级管理员）"
                )
                return True
            return False

        # 触发洪水模式
        if len(self._msg_timestamps) >= threshold or len(burst) > burst_limit:
            self._flood_until = now + cooldown
            logger.warning(
                f"QQ 检测到消息洪泛（窗口 {window}s 内全局 {len(self._msg_timestamps)} 条，"
                f"用户 {ev.user_id} {len(burst)} 条），进入洪水模式 {cooldown:.0f}s，"
                f"期间仅处理超级管理员"
            )
            if level != "master":
                return True
        return False

    def _should_respond(self, ev: QQMessageEvent, cfg: dict) -> (bool, str):
        """决定一条消息是否需要回应（全局触发规则）。

        规则（群聊）：
        1. 被 @ 机器人 → 触发；但非白名单用户（level=none）即使 @ 也不响应（前置拦截）。
        2. @全体成员 → 放行进入处理，但只走插件系统（插件未回复则静默截停，agent 不触发）。
        3. 未被 @ → 仅超级管理员（master）可用 / 前缀触发（走 agent，不经过插件系统）。
        私聊：默认回应（受 check_access 白名单控制）。
        """
        if ev.is_private:
            return True, "private"

        level = user_level(ev.user_id, get_perm_cfg())
        is_at_self = ev.contains_at_self(self_id=ev.self_id)
        is_at_all = ev.contains_at_all()
        is_command = any(
            ev.raw_message.strip().startswith(p)
            for p in cfg.get("wake_prefixes", ["/", "!"])
        ) or ev.raw_message.strip().startswith("#")

        # 1) 被 @：非白名单用户前置拦截
        if is_at_self:
            if level == "none":
                return False, "not_whitelist"
            return True, "at_self"

        # 2) @全体：放行（仅插件系统处理，agent 不触发）
        if is_at_all:
            return True, "at_all"

        # 3) 未被 @：仅 master 的 / 前缀命令触发（走 agent）
        if level == "master" and is_command:
            return True, "master_command"

        return False, "not_wake"

    def _is_key_usage(self, ev: QQMessageEvent, cfg: dict) -> bool:
        """是否为"关键场景"（明确的命令/插件调用）。

        判断依据：剥离 @机器人/@全体/@<qq> 前缀后，剩余文本以触发符
        （/ ! #，其中 # 为 miao 等插件命令前缀）开头。
        普通闲聊（如"不知道""区"）返回 False，用于静默忽略非白名单用户。
        """
        try:
            text = ev.to_text(self_id=ev.self_id)
        except Exception:
            text = ""
        text = _LEAD_AT_MENTION_RE.sub("", (text or "").strip(), count=1).strip()
        if not text:
            return False
        prefixes = list(cfg.get("wake_prefixes", ["/", "!"]))
        return any(text.startswith(p) for p in prefixes) or text.startswith("#")

    async def _process_and_reply(self, ev: QQMessageEvent, text: str, cfg: dict) -> None:
        """处理消息并发送回复"""
        try:
            # 去掉开头的 @机器人/@全体成员/@<qq> 标记，使被 @ 的命令能被正确识别为
            # 命令（@ZHIYAN /miao伤害 … -> /miao伤害 …），从而进入插件系统。
            text = _LEAD_AT_MENTION_RE.sub("", text, count=1)
            # 管理命令（/qq_*）由本地处理，不进入对话引擎；非管理员调用时静默忽略
            level = user_level(ev.user_id, get_perm_cfg())
            is_command = any(
                text.startswith(p) for p in cfg.get("wake_prefixes", ["/", "!"])
            ) or text.startswith("#")
            is_at_self = ev.contains_at_self(self_id=ev.self_id)
            is_at_all = ev.contains_at_all()

            # 超级管理员面板 /command_control（仅 master 可用，其他人静默忽略）
            low = text.strip().lower()
            if low.startswith("/command_control") or low == "/cc":
                if level != "master":
                    return
                resp = await self._handle_super_admin_command(text, ev)
                if resp:
                    await self._send_reply(ev, resp, cfg, humanized_delay=False)
                return

            handled, cmd_resp = self._handle_admin_command(text, level)
            if handled:
                if cmd_resp:
                    await self._send_reply(ev, cmd_resp, cfg, humanized_delay=False)
                return

            # 超级管理员请求评价会话历史：@ 机器人 / / 前缀 / 私聊 且包含评价关键词
            # → 触发 LLM 对会话历史进行评价（读取消息，上限 20 条），不进入普通对话
            if level == "master" and (is_at_self or is_command or ev.is_private):
                low = low or text.strip().lower()
                if any(k in low for k in _EVALUATION_KEYWORDS):
                    if await self._evaluate_and_reply(ev, cfg):
                        return

            response, is_llm = await self._route_to_engine(
                text, ev, cfg,
                is_at_self=is_at_self,
                is_at_all=is_at_all,
                level=level,
                is_command=is_command,
            )
        except Exception as e:
            self.stats["errors"] += 1
            logger.error(f"QQ 消息处理失败: {e}", exc_info=True)
            response = f"处理消息时出错：{e}"
            is_llm = False

        response = strip_reasoning((response or "").strip())
        if not response:
            return

        # 仅 agent（LLM）回复应用随机等待延迟（5~25s），命令/插件回复即时发送
        await self._send_reply(ev, response, cfg, humanized_delay=is_llm)

    async def _route_to_engine(self, text: str, ev: QQMessageEvent, cfg: dict,
                               is_at_self: bool = False, is_at_all: bool = False,
                               level: str = "none", is_command: bool = False) -> (str, bool):
        """根据触发上下文选择处理通道。

        返回 (response, is_llm)：
          is_llm=True 表示该回复来自 agent（LLM），应用随机等待延迟。
        通道规则：
        - 私聊：命令 → pipeline（插件系统）；普通 → agent 非流式
        - 群聊 @全体：仅插件系统（无回复则静默截停）
        - 群聊被 @ 的命令：插件系统（插件未响应则静默）
        - 群聊被 @ 的普通消息：agent 非流式
        - 群聊 master 未被 @ 的 / 命令：agent 非流式（插件系统需 @ 才触发）
        - 白名单用户（whitelist）：只能使用插件系统，所有关键功能（agent）禁用；
          插件未处理则静默截停，不回复
        """
        from zhiyan_web.core import chat_manager as cm

        wake_prefixes = cfg.get("wake_prefixes", ["/", "!"])
        # 需要喂给管道的上下文键（用于插件过滤器 cfg）
        pipeline_kwargs = QQMessageEventAdapter.to_pipeline_kwargs(ev, wake_prefixes)
        pipeline_ok = bool(cm and hasattr(cm, "_pipeline") and cm._pipeline)

        # # 前缀命令为 miao 插件专属（由 TRSS-Yunzai 原生处理）。
        # ZhiYan 对该类命令永不调用 agent 回复，避免与 Yunzai 对同一 # 命令双重回复
        # （Yunzai 回复图片，ZhiYan 又回一条 LLM 文本）。
        is_miao_cmd = text.strip().startswith("#")

        use_pipeline = False
        use_agent = False
        # 主人（master）不带 @ 的 / 命令若插件未匹配/未响应，允许回退 agent
        fallback_agent_on_empty = False

        # 白名单用户：仅插件系统（agent 完全禁用）
        whitelist_only = (level == "whitelist")

        # owner_only_llm：仅超级管理员（主人）的对话才允许调用 LLM（agent）。
        # 开启后非主人一律不触发 agent（插件/pipeline 仍可响应，agent 不降级回退）。
        agent_allowed = not (bool(cfg.get("owner_only_llm", False)) and level != "master")

        # 插件/命令系统使用权限：all=允许所有人 / master=仅主人 / whitelist=主人+白名单。
        # 仅约束插件/命令触发（pipeline），不影响 agent/自然对话。
        _perm = cfg.get("plugin_use_permission") or "all"
        if _perm == "master":
            plugin_allowed = (level == "master")
        elif _perm == "whitelist":
            plugin_allowed = level in ("master", "whitelist")
        else:
            plugin_allowed = True

        if ev.is_private:
            if whitelist_only:
                # 白名单私聊：仅插件系统，插件未处理则静默
                use_pipeline = pipeline_ok and plugin_allowed
                use_agent = False
                if use_pipeline:
                    pipeline_kwargs["no_agent_fallback"] = True
            else:
                use_pipeline = bool(is_command and plugin_allowed and pipeline_ok)
                use_agent = (not use_pipeline) and agent_allowed
        elif is_at_all and not is_at_self:
            # @全体：仅插件系统有机会处理，agent 不触发；插件未处理则静默截停
            use_pipeline = pipeline_ok and plugin_allowed
            use_agent = False
            if use_pipeline:
                pipeline_kwargs["no_agent_fallback"] = True
        elif is_command and is_at_self:
            # 被 @ 的命令 → 插件系统（未响应则静默，不降级 agent）
            use_pipeline = pipeline_ok and plugin_allowed
            use_agent = False
        elif is_at_self:
            if whitelist_only:
                # 白名单被 @：仅插件系统，插件未处理则静默（agent 禁用）
                use_pipeline = pipeline_ok and plugin_allowed
                use_agent = False
                if use_pipeline:
                    pipeline_kwargs["no_agent_fallback"] = True
            else:
                use_agent = agent_allowed
        elif level == "master" and is_command:
            # 主人不带 @ 的 / 命令：优先插件系统（/miao帮助、/help 等命令名命中插件），
            # 插件未匹配/未响应时回退 agent（_agent_non_stream），保证自由 "/闲聊" 仍走 agent。
            use_pipeline = pipeline_ok and plugin_allowed
            use_agent = agent_allowed and not is_miao_cmd
            fallback_agent_on_empty = not is_miao_cmd
            if use_pipeline:
                pipeline_kwargs["no_agent_fallback"] = True
        else:
            return "", False

        if use_pipeline:
            try:
                result = await cm.process_via_pipeline(text, **pipeline_kwargs)
                response = result.get("response", "") if isinstance(result, dict) else str(result)
                response = (response or "").strip()
                if response or not fallback_agent_on_empty:
                    return response, False
                # 插件未响应且允许回退 → 继续走 agent
                use_agent = True
            except Exception as e:
                logger.warning(f"QQ pipeline 处理失败（{e}），回退 agent")
                # 白名单用户 / 非主人（owner_only_llm）/ # miao 命令不允许回退 agent
                if whitelist_only or not agent_allowed or is_miao_cmd:
                    return "", False
                use_agent = True

        if use_agent:
            return await self._agent_non_stream(text, ev), True

        return "", False

    async def _agent_non_stream(self, text: str, ev: QQMessageEvent) -> str:
        """QQ 平台 agent 回复完全走非流式。

        chat_with_tools 内部使用 provider.chat()（非流式），reasoning 与正文干净分离，
        同时保留工具调用（ReAct）与记忆/情感/羁绊上下文；按 QQ 会话隔离对话历史。
        """
        from zhiyan_web.core import chat_manager as cm
        conv_id = f"qq_{ev.session_id}"
        try:
            resp = await cm.chat_with_tools(text, conv_id=conv_id)
            return (resp or "").strip()
        except Exception as e:
            logger.warning(f"QQ agent 非流式回复失败（{e}），回退普通聊天")
            try:
                resp = await cm.chat(text, conv_id=conv_id)
                content = getattr(resp, "content", None)
                return (content or str(resp)).strip()
            except Exception as e2:
                logger.error(f"QQ agent 回退聊天也失败: {e2}")
                return f"处理消息时出错：{e2}"

    async def _maybe_notify_denied(self, ev: QQMessageEvent, cfg: dict, reason: str) -> None:
        """无权限时的处理：permission_reply=notify 时提示一次（带 30s 冷却防刷屏）"""
        try:
            if cfg.get("permission_reply") != "notify":
                return
            if not self.client or not self.client.is_online:
                return
            now = time.time()
            if now - self._deny_notify_at.get(ev.user_id, 0.0) < 30.0:
                return
            self._deny_notify_at[ev.user_id] = now
            tips = {
                "blacklisted": "你已被加入黑名单，无法使用该功能。",
                "private_disabled": "机器人暂未开放私聊功能。",
                "group_disabled": "机器人暂未开放群聊功能。",
                "group_not_allowed": "该群不在允许范围内，无法使用该功能。",
                "not_admin": "该功能仅限管理员使用。",
                "not_whitelist": "你不在白名单中，无法使用该功能。",
            }
            msg = tips.get(reason, "你没有权限使用该功能。")
            await self._pace_send(cfg)
            if ev.is_group:
                await self.client.send_group_msg(ev.group_id, msg, timeout=10)
            else:
                await self.client.send_private_msg(ev.user_id, msg, timeout=10)
        except Exception as e:
            logger.debug(f"无权限提示发送失败: {e}")

    def _handle_admin_command(self, text: str, level: str) -> (bool, str):
        """处理 /qq_* 管理命令。返回 (是否已处理, 回复内容)。

        非管理员/主人调用管理命令时静默忽略（返回空回复）。
        """
        if not is_admin_command(text):
            return False, None
        if level not in ("master", "admin"):
            return True, ""  # 无权限：忽略

        parts = text.strip().split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("/qq_help", "/qq_admin"):
            return True, (
                "管理命令：\n"
                "/qq_status 查看状态\n"
                "/qq_whitelist 白名单列表\n"
                "/qq_whitelist add <QQ> 添加白名单\n"
                "/qq_whitelist del <QQ> 移除白名单\n"
                "/qq_blacklist 黑名单列表\n"
                "/qq_blacklist add <QQ> 拉黑\n"
                "/qq_blacklist del <QQ> 取消拉黑\n"
                "/qq_block_group <群号> 屏蔽群\n"
                "/qq_allow_group <群号> 放行群"
            )
        if cmd == "/qq_status":
            st = self.get_status()
            perm = st.get("permission", {})
            return True, (
                f"QQ 平台状态\n"
                f"运行: {'是' if st['running'] else '否'} | 在线: {'是' if st['online'] else '否'}\n"
                f"账号: {st['self_id']} ({st['self_nickname'] or '未知'})\n"
                f"接入: {st['ws_url']}\n"
                f"访问模式: {st.get('access_mode')}\n"
                f"收到消息: {st['stats']['total_messages']} | 回复: {st['stats']['total_replies']}\n"
                f"白名单: {len(perm.get('whitelist', []))} | "
                f"管理员: {len(perm.get('admins', []))} | "
                f"黑名单: {len(perm.get('blacklist', []))}"
            )
        if cmd == "/qq_whitelist":
            if args and args[0] in ("add", "del"):
                if len(args) < 2:
                    return True, "用法: /qq_whitelist add <QQ> 或 /qq_whitelist del <QQ>"
                res = mutate_perm_list("whitelist", args[1], args[0])
                return True, (f"已更新白名单: {args[1]}") if res.get("success") else res.get("message", "操作失败")
            whitelist = get_perm_cfg().get("whitelist", [])
            return True, "白名单(" + str(len(whitelist)) + "): " + ("、".join(whitelist) if whitelist else "空")
        if cmd == "/qq_blacklist":
            if args and args[0] in ("add", "del"):
                if len(args) < 2:
                    return True, "用法: /qq_blacklist add <QQ> 或 /qq_blacklist del <QQ>"
                res = mutate_perm_list("blacklist", args[1], args[0])
                return True, (f"已更新黑名单: {args[1]}") if res.get("success") else res.get("message", "操作失败")
            blacklist = get_perm_cfg().get("blacklist", [])
            return True, "黑名单(" + str(len(blacklist)) + "): " + ("、".join(blacklist) if blacklist else "空")
        if cmd in ("/qq_block_group", "/qq_allow_group"):
            if not args:
                return True, f"用法: {cmd} <群号>"
            field = "blocked_groups" if cmd == "/qq_block_group" else "allowed_groups"
            res = mutate_perm_list(field, args[0], "add")
            return True, res.get("message", "操作完成")
        return True, "未知命令，输入 /qq_help 查看帮助。"

    # ──────────────── 超级管理员面板 /command_control ────────────────

    async def _handle_super_admin_command(self, text: str, ev: Optional[QQMessageEvent] = None) -> str:
        """超级管理员 /command_control 面板与分支控制命令（仅 master 调用）

        分支:
        - 无参数 / help  —— 帮助面板（含运行状态概览）
        - status          —— 完整运行状态
        - plugins         —— 插件列表
        - plugin enable/disable/reload <插件名> —— 插件控制
        - system          —— 系统资源
        - send <image|record|video> <路径/URL> —— 测试向当前会话发送媒体
        - restart/stop/start —— QQ 平台启停控制
        """
        parts = text.strip().split()
        sub = (parts[1].lower() if len(parts) > 1 else "").lstrip("-")
        if not sub or sub in ("help", "?"):
            return self._command_control_panel()
        if sub == "status":
            return self._command_control_status()
        if sub == "restart":
            await self.restart()
            return "QQ 平台正在重启，请稍候…"
        if sub == "stop":
            await self.stop()
            return "QQ 平台已停止。发送 /command_control start 可重新启动。"
        if sub == "start":
            res = await self.start()
            return res.get("message", "QQ 平台已启动")
        if sub == "plugins":
            return self._command_control_plugins()
        if sub == "plugin":
            return await self._command_control_plugin_action(parts)
        if sub == "system":
            return self._command_control_system()
        if sub == "send":
            return await self._command_control_send(parts, ev)
        return "未知子命令。发送 /command_control 查看帮助。"

    def _command_control_panel(self) -> str:
        """帮助面板 + 运行状态概览"""
        st = self.get_status()
        cfg = _get_qq_platform_cfg()
        perm = st.get("permission", {})
        plugins = self._get_plugin_stats()
        try:
            from zhiyan_web.config import get_settings
            settings = get_settings()
        except Exception:
            settings = {}
        master_nick = (self.master_info or {}).get("nickname", "")
        lines = [
            "══════ 枫丹剧场 · 超级管理员面板 ══════",
            f"管理员: {perm.get('master', '')}" + (f" ({master_nick})" if master_nick else ""),
            "",
            "【运行状态概览】",
            f"QQ平台: {'运行中' if st['running'] else '已停止'} | 在线: {'是' if st['online'] else '否'}",
            f"接入: {st['ws_url']} | 账号: {st['self_id']}",
            f"LLM: {settings.get('provider', 'unknown')} / {settings.get('model', '')}",
            f"插件: 已加载 {plugins['loaded']} | 失败 {plugins['failed']} | 共 {plugins['total']}",
            f"消息: 收到 {st['stats']['total_messages']} | 回复 {st['stats']['total_replies']}",
            f"访问模式: {perm.get('access_mode', 'whitelist_at_only')} | 白名单 {len(perm.get('whitelist', []))}人",
            f"回复节奏: 随机延迟 {cfg.get('reply_delay_min', 5)}~{cfg.get('reply_delay_max', 25)}s | 用户冷却 {cfg.get('user_cooldown', 2)}s",
            "",
            "【触发规则】",
            "· 群聊需 @机器人 才会回应（agent / 插件）",
            "· 非白名单用户 @ 也不回应",
            "· @全体成员消息默认不回应（插件可处理）",
            "· 超级管理员无需 @，/ 前缀直接调用 agent",
            "",
            "【控制命令】（仅超级管理员）",
            "/command_control               本面板",
            "/command_control status        完整状态",
            "/command_control plugins       插件列表",
            "/command_control plugin enable <插件名>   启用插件",
            "/command_control plugin disable <插件名>  停用插件",
            "/command_control plugin reload            重载插件",
            "/command_control system       系统资源",
            "/command_control send <image|record|video> <路径>  测试发送媒体",
            "/command_control restart      重启 QQ 平台",
            "/command_control stop         停止 QQ 平台",
            "/command_control start        启动 QQ 平台",
            "",
            "媒体发送: 回复中可用 [图片:路径]/[语音:路径]/[视频:路径] 随文发送媒体",
            "提示: 命令可用 /cc 简写，如 /cc status",
        ]
        return "\n".join(lines)

    def _command_control_status(self) -> str:
        """完整运行状态"""
        st = self.get_status()
        cfg = _get_qq_platform_cfg()
        perm = st.get("permission", {})
        try:
            from zhiyan_web.config import get_settings
            settings = get_settings()
        except Exception:
            settings = {}
        master_nick = (self.master_info or {}).get("nickname", "")
        lines = [
            "══════ 完整运行状态 ══════",
            f"QQ平台: 运行={st['running']} 在线={st['online']} 连接={st['connected']}",
            f"账号: {st['self_id']} ({st['self_nickname'] or '未知'})",
            f"接入地址: {st['ws_url']}",
            f"最后连接: {self._fmt_time(st.get('last_connected_at'))}",
            f"最后心跳: {self._fmt_time(st.get('last_heartbeat'))}",
            f"最近错误: {st.get('last_error') or '无'}",
            "",
            "【LLM】",
            f"提供商: {settings.get('provider', 'unknown')} | 模型: {settings.get('model', '')}",
            f"思考模式: {'开' if settings.get('deepseek_thinking_enabled', False) else '关'}",
            "",
            "【权限】",
            f"超级管理员: {perm.get('master', '')} ({master_nick or '未校验'})",
            f"管理员: {len(perm.get('admins', []))}人 | 白名单: {len(perm.get('whitelist', []))}人 | 黑名单: {len(perm.get('blacklist', []))}人",
            f"访问模式: {perm.get('access_mode')} | 私聊: {'开' if perm.get('private_enabled') else '关'} | 群聊: {'开' if perm.get('group_enabled') else '关'}",
            f"屏蔽群: {len(perm.get('blocked_groups', []))} | 允许群: {len(perm.get('allowed_groups', []))}",
            "",
            "【消息统计】",
            f"收到: {st['stats']['total_messages']} | 回复: {st['stats']['total_replies']} | 错误: {st['stats']['errors']}",
            f"回复节奏: 延迟 {cfg.get('reply_delay_min', 5)}~{cfg.get('reply_delay_max', 25)}s | 用户冷却 {cfg.get('user_cooldown', 2)}s | 发送间隔 {cfg.get('min_send_interval', 1.2)}s",
            f"洪水防护: {'激活中' if time.time() < self._flood_until else '正常'} | 窗口 {cfg.get('flood_window', 10)}s/阈值 {cfg.get('flood_threshold', 20)}条 | 突发上限 {cfg.get('user_burst_limit', 5)}条",
        ]
        return "\n".join(lines)

    def _command_control_plugins(self) -> str:
        """插件列表（含平台标签）"""
        try:
            from zhiyan_web.plugins.routers.plugins import get_plugin_manager
            pm = get_plugin_manager()
            if not pm:
                return "插件管理器尚未就绪。"
            statuses = pm.get_plugin_status()
        except Exception as e:
            return f"获取插件列表失败: {e}"
        if not statuses:
            return "没有已加载的插件。"
        lines = ["══════ 插件列表 ══════"]
        for s in statuses:
            mark = "✅" if s.get("activated") else ("❌" if s.get("error") else "⛔")
            plat = ",".join(s.get("platform_tags", ["通用"]))
            name = s.get("display_name") or s.get("name") or s.get("root_dir_name", "?")
            lines.append(f"{mark} {name} [v{s.get('version', '?')}] ({plat})")
            if s.get("error"):
                lines.append(f"   错误: {s.get('error')[:120]}")
        return "\n".join(lines)

    async def _command_control_plugin_action(self, parts: List[str]) -> str:
        """启用 / 停用 / 重载插件"""
        action = parts[2].lower() if len(parts) >= 3 else ""
        target = " ".join(parts[3:]) if len(parts) >= 4 else ""
        if action not in ("enable", "disable", "reload"):
            return "用法: /command_control plugin <enable|disable|reload> [插件名]"
        try:
            from zhiyan_web.plugins.routers.plugins import get_plugin_manager
            pm = get_plugin_manager()
            if not pm:
                return "插件管理器尚未就绪。"
            if action == "reload":
                ok, msg = await pm.reload()
                if ok:
                    return "插件已全部重载。"
                detail = ""
                if isinstance(msg, dict):
                    detail = "失败插件: " + ", ".join(str(k) for k in list(msg.keys())[:10])
                elif msg:
                    detail = str(msg)
                return f"插件重载完成，部分失败。{detail}"
            if not target:
                return f"用法: /command_control plugin {action} <插件名>"
            if action == "enable":
                ok = await pm.enable(target)
            else:
                ok = await pm.disable(target)
            return f"插件 {target} 已{'启用' if action == 'enable' else '停用'}。" if ok else \
                f"插件 {target} {action}失败，请检查插件名是否正确。"
        except Exception as e:
            return f"插件{action}失败: {e}"

    def _command_control_system(self) -> str:
        """系统资源信息"""
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            proc = psutil.Process()
            uptime = time.time() - proc.create_time()
            lines = [
                "══════ 系统资源 ══════",
                f"CPU 使用率: {cpu}%",
                f"内存: {mem.used / 1024**3:.1f}GB / {mem.total / 1024**3:.1f}GB ({mem.percent}%)",
                f"磁盘: {disk.used / 1024**3:.1f}GB / {disk.total / 1024**3:.1f}GB ({disk.percent}%)",
                f"本进程运行时长: {self._fmt_duration(uptime)}",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"获取系统信息失败: {e}"

    async def _command_control_send(self, parts: List[str],
                                    ev: Optional[QQMessageEvent]) -> str:
        """测试向当前会话发送媒体：/cc send <image|record|video> <路径或URL>"""
        if len(parts) < 4:
            return "用法: /command_control send <image|record|video|file> <文件路径或URL>"
        media_type = parts[2].lower()
        if media_type not in ("image", "record", "video", "file"):
            return "媒体类型仅支持 image / record / video / file"
        file_ref = " ".join(parts[3:]).strip()
        if not file_ref:
            return "缺少文件路径"
        if not self.client or not self.client.is_online:
            return "QQ 平台未在线，无法发送媒体"
        if not file_ref.startswith(("http://", "https://", "base64://")) and not os.path.exists(file_ref):
            return f"文件不存在: {file_ref}"
        try:
            await self.send_media(ev, media_type, file_ref)
            return f"已尝试发送 {media_type}: {file_ref}"
        except Exception as e:
            return f"媒体发送失败: {e}"

    def _get_plugin_stats(self) -> Dict[str, int]:
        """获取插件统计（已加载/失败/总数）"""
        try:
            from zhiyan_web.plugins.routers.plugins import get_plugin_manager
            pm = get_plugin_manager()
            if not pm:
                return {"loaded": 0, "failed": 0, "total": 0}
            statuses = pm.get_plugin_status()
            loaded = sum(1 for s in statuses if s.get("activated"))
            failed = sum(1 for s in statuses if s.get("error"))
            return {"loaded": loaded, "failed": failed, "total": len(statuses)}
        except Exception:
            return {"loaded": 0, "failed": 0, "total": 0}

    @staticmethod
    def _fmt_time(ts) -> str:
        if not ts:
            return "—"
        try:
            return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(ts)

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h}小时{m}分{s}秒"

    async def _pace_send(self, cfg: dict) -> None:
        """发送限速（防封号）：串行化发送，保证任意两条消息间隔与每分钟上限。

        内部锁在等待期间保持持有，因此所有回复会被排队串行发送，
        不会出现并发同时发出导致 QQ 风控的情况。
        """
        min_interval = max(0.3, float(cfg.get("min_send_interval", 1.2)))
        max_per_minute = max(1, int(cfg.get("max_send_per_minute", 40)))
        async with self._send_lock:
            now = time.time()
            # 滑动窗口：清除 60s 以前的发送记录
            self._send_times = [t for t in self._send_times if now - t < 60.0]
            if len(self._send_times) >= max_per_minute:
                wait_extra = 30.0
                logger.info(
                    f"QQ 每分钟发送已达上限({max_per_minute})，等待 {wait_extra:.0f}s 防风控"
                )
                await asyncio.sleep(wait_extra)
                now = time.time()
                self._send_times = [t for t in self._send_times if now - t < 60.0]
            wait = self._last_send_time + min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_send_time = time.time()
            self._send_times.append(self._last_send_time)

    async def _send_reply(self, ev: QQMessageEvent, response: str, cfg: dict,
                          humanized_delay: bool = False) -> None:
        """发送 QQ 回复（发送前经过限速，防快速连发触发封号）

        humanized_delay=True（agent/LLM 回复）时，先随机等待 5~25s 再发送，
        让回复节奏更自然并降低风控概率。

        回复文本中的媒体标记（[图片:路径] / [语音:路径] / [视频:路径]）会被解析为
        对应媒体消息段与正文一并发送；文本中原有的 [CQ:image/record/video,...] 直接透传。
        """
        if not self.client or not self.client.is_online:
            logger.warning("QQ 平台未在线，回复丢弃")
            return

        try:
            if humanized_delay:
                d_min = max(0.0, float(cfg.get("reply_delay_min", 5)))
                d_max = max(d_min, float(cfg.get("reply_delay_max", 25)))
                wait = random.uniform(d_min, d_max)
                logger.info(f"agent 回复随机等待 {wait:.1f}s 后发送")
                await asyncio.sleep(wait)
            elif cfg.get("plugin_reply_delay_enabled", True):
                # 插件/命令回复也附加随机等待，避免"秒回"显得机械
                p_min = max(0.0, float(cfg.get("plugin_reply_delay_min", 0.8)))
                p_max = max(p_min, float(cfg.get("plugin_reply_delay_max", 3.5)))
                wait = random.uniform(p_min, p_max)
                logger.debug(f"插件回复随机等待 {wait:.1f}s 后发送")
                await asyncio.sleep(wait)
            await self._pace_send(cfg)

            # 提取媒体标记 → 构造混合消息（文本 + at + image/record/video 段）
            media_segments, clean_text = extract_media_markers(response or "")
            at_segments, has_at = parse_at_markers(clean_text)
            if has_at or media_segments:
                message: Any = []
                if has_at:
                    message.extend(at_segments)
                elif clean_text:
                    message.append({"type": "text", "data": {"text": clean_text}})
                message.extend(media_segments)
            else:
                message = clean_text

            await self._send_payload(ev, message, cfg)
        except Exception as e:
            self.stats["errors"] += 1
            logger.error(f"QQ 回复发送失败: {e}")

    @staticmethod
    def _qq_upload_path(file_ref: str) -> str:
        """把 file:/// 形式还原为本地路径（NapCat 上传接口需要本地路径）"""
        p = str(file_ref or "")
        if p.startswith("file://"):
            p = p[len("file://"):].lstrip("/")
            try:
                from urllib.parse import unquote
                p = unquote(p)
            except Exception:
                pass
        return p

    async def _send_payload(self, ev: QQMessageEvent, message: Any, cfg: dict) -> None:
        """发送消息负载，自动把 file 段改为走 NapCat 上传接口（upload_*_file）。

        message 可以是纯文本字符串，或消息段列表（可含 text/at/image/record/video/file）。
        """
        timeout = float(cfg.get("call_timeout", 30))
        if isinstance(message, list):
            file_segs = [s for s in message if isinstance(s, dict) and s.get("type") == "file"]
            normal_segs = [s for s in message if not (isinstance(s, dict) and s.get("type") == "file")]
        else:
            file_segs, normal_segs = [], message

        if normal_segs:
            if ev.is_group:
                resp = await self.client.send_group_msg(ev.group_id, normal_segs, timeout=timeout)
            else:
                resp = await self.client.send_private_msg(ev.user_id, normal_segs, timeout=timeout)
            self.stats["total_replies"] += 1
            status = (resp or {}).get("status")
            if status not in ("ok", "success"):
                logger.warning(f"QQ 消息发送未成功: {resp}")

        for seg in file_segs:
            data = seg.get("data", {}) or {}
            file_ref = self._qq_upload_path(data.get("file", ""))
            if not file_ref:
                continue
            name = data.get("name") or os.path.basename(str(data.get("file", "")).rstrip("/"))
            try:
                if ev.is_group:
                    resp = await self.client.upload_group_file(ev.group_id, file_ref, name=name)
                else:
                    resp = await self.client.upload_private_file(ev.user_id, file_ref, name=name)
                self.stats["total_replies"] += 1
                status = (resp or {}).get("status")
                if status not in ("ok", "success"):
                    logger.warning(f"QQ 文件上传未成功: {resp}")
            except Exception as e:
                self.stats["errors"] += 1
                logger.error(f"QQ 文件上传失败: {e}")

    async def send_media(self, ev: QQMessageEvent, media_type: str, file: str,
                         text: str = "", **extra) -> Dict[str, Any]:
        """向会话发送媒体（image / record / video / file），供插件 / agent / 命令调用。

        Args:
            ev: 消息事件（决定私聊还是群聊通道）
            media_type: image / record / video / file
            file: 本地绝对路径 / http(s) URL / base64://...
            text: 可选的随附文本
            **extra: 媒体段的附加参数（如图片 flash、视频 thumb 等）
        """
        if not self.client or not self.client.is_online:
            logger.warning("QQ 平台未在线，媒体发送丢弃")
            return {"status": "failed", "error": "not_online"}
        cfg = _get_qq_platform_cfg()
        await self._pace_send(cfg)
        if text:
            await self.client.send_msg(
                "group" if ev.is_group else "private",
                ev.group_id if ev.is_group else ev.user_id,
                [{"type": "text", "data": {"text": text}}],
                timeout=float(cfg.get("call_timeout", 30)),
            )

        # 普通文件走 NapCat 上传接口（upload_*_file），其余媒体走消息段发送
        if media_type == "file":
            file_ref = self._qq_upload_path(file)
            name = (extra.get("name") or "") or os.path.basename(str(file_ref).rstrip("/"))
            try:
                if ev.is_group:
                    resp = await self.client.upload_group_file(ev.group_id, file_ref, name=name)
                else:
                    resp = await self.client.upload_private_file(ev.user_id, file_ref, name=name)
            except Exception as e:
                self.stats["errors"] += 1
                logger.error(f"QQ 文件上传失败: {e}")
                return {"status": "failed", "error": str(e)}
            self.stats["total_replies"] += 1
            return resp

        resp = await self.client.send_media(ev, media_type, file,
                                            timeout=float(cfg.get("call_timeout", 30)), **extra)
        self.stats["total_replies"] += 1
        return resp

    # ──────────────── notice / request ────────────────

    async def _on_notice(self, raw: dict) -> None:
        notice_type = raw.get("notice_type", "")
        user_id = raw.get("user_id")
        group_id = raw.get("group_id")
        self_id = raw.get("self_id") or (self.client.self_id if self.client else "")
        logger.info(f"QQ 平台 notice: {notice_type} user={user_id} group={group_id}")

        # 机器人自己被拉入群：只同步 presence（入群欢迎消息体已被 _on_message 忽略），不回复
        if notice_type == "group_increase" and user_id and str(user_id) == str(self_id):
            self._mark_presence_dirty()
            await self._refresh_presence()
            return

        # presence 同步：好友/群成员变动后刷新列表
        if notice_type in ("friend_add", "friend_recall", "group_increase", "group_decrease", "group_ban"):
            if notice_type in ("friend_add", "friend_recall"):
                self._mark_presence_dirty()
            await self._refresh_presence()

    async def _on_request(self, raw: dict) -> None:
        request_type = raw.get("request_type", "")
        logger.info(f"QQ 平台收到加好友/加群请求: {request_type}，默认忽略")

    # ──────────────── presence ────────────────

    def _mark_presence_dirty(self) -> None:
        self._last_presence_refresh = 0.0

    async def _refresh_presence(self) -> None:
        """刷新好友/群列表（presence 数据源），带节流"""
        if not self.client:
            return
        now = time.time()
        if now - self._last_presence_refresh < 5.0:
            return
        self._last_presence_refresh = now
        try:
            friends_resp = await self.client.get_friend_list(timeout=8)
            if friends_resp and friends_resp.get("status") in ("ok", "success"):
                friends = (friends_resp.get("data") or [])
                self.presence["friends"] = [
                    {
                        "user_id": str(f.get("user_id", "")),
                        "nickname": f.get("nickname", ""),
                        "remark": f.get("remark", ""),
                    }
                    for f in friends
                ]
        except Exception as e:
            logger.debug(f"拉取好友列表失败: {e}")

        try:
            groups_resp = await self.client.get_group_list(timeout=8)
            if groups_resp and groups_resp.get("status") in ("ok", "success"):
                groups = (groups_resp.get("data") or [])
                self.presence["groups"] = [
                    {
                        "group_id": str(g.get("group_id", "")),
                        "group_name": g.get("group_name", ""),
                    }
                    for g in groups
                ]
        except Exception as e:
            logger.debug(f"拉取群列表失败: {e}")

    async def _greet(self, message: str) -> None:
        """连接成功后（可选）发送上线问候，仅针对私聊好友（带限速避免刷屏）"""
        if not message or not self.client:
            return
        cfg = _get_qq_platform_cfg()
        # 私聊好友过多时避免打扰，仅问候最近 20 位
        for f in self.presence["friends"][:20]:
            try:
                await self._pace_send(cfg)
                await self.client.send_private_msg(
                    f["user_id"], message, timeout=10
                )
            except Exception as e:
                logger.debug(f"上线问候失败 ({f.get('nickname')}): {e}")

    # ──────────────── 状态 / 日志 ────────────────

    def _push_state_log(self, entry: dict) -> None:
        self._state_log.append(entry)
        if len(self._state_log) > 100:
            self._state_log = self._state_log[-100:]

    def get_status(self) -> Dict[str, Any]:
        cfg = _get_qq_platform_cfg()
        perm = get_perm_cfg()
        c = self.client
        return {
            "running": self._running,
            "enabled": bool(cfg.get("enabled")),
            "configured": bool(cfg.get("ws_url")),
            "connected": bool(c and c.connected),
            "online": bool(c and c.is_online),
            "connecting": bool(c and c.connecting),
            "ws_url": cfg.get("ws_url", ""),
            "self_id": (c.self_id if c else "") or cfg.get("self_id", ""),
            "self_nickname": (c.bot_nickname if c else ""),
            "last_connected_at": c.last_connected_at if c else None,
            "last_disconnected_at": c.last_disconnected_at if c else None,
            "last_heartbeat": c.last_heartbeat if c else None,
            "last_error": c.last_error if c else None,
            "group_respond_mode": cfg.get("group_respond_mode", "at_or_command"),
            "access_mode": perm.get("access_mode", "whitelist_at_only"),
            "permission": {
                "master": perm.get("master_id", ""),
                "admins": perm.get("admins_id", []),
                "whitelist": perm.get("whitelist", []),
                "blacklist": perm.get("blacklist", []),
                "blocked_groups": perm.get("blocked_groups", []),
                "allowed_groups": perm.get("allowed_groups", []),
                "private_enabled": perm.get("private_enabled", True),
                "group_enabled": perm.get("group_enabled", True),
                "permission_reply": perm.get("permission_reply", "ignore"),
            },
            "stats": self.stats,
            "presence": {
                "friends_count": len(self.presence["friends"]),
                "groups_count": len(self.presence["groups"]),
                "friends": self.presence["friends"][:50],
                "groups": self.presence["groups"][:50],
            },
            "recent_events": list(self._state_log[-20:]),
        }

    def get_config(self) -> Dict[str, Any]:
        cfg = dict(_get_qq_platform_cfg())
        cfg.update(get_perm_cfg())
        # 令牌脱敏
        if cfg.get("access_token"):
            token = cfg["access_token"]
            cfg["access_token"] = (token[:2] + "****" + token[-2:]) if len(token) >= 4 else "****"
        return cfg


# 模块级单例
_manager: Optional[QQPlatformManager] = None


def get_qq_platform_manager() -> QQPlatformManager:
    """获取 QQ 平台管理器单例"""
    global _manager
    if _manager is None:
        _manager = QQPlatformManager()
    return _manager