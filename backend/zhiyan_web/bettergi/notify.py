# -*- coding: utf-8 -*-
"""BetterGI 通知中枢：去重、限速、静默时段、重试、投递日志。

设计成纯逻辑 + 注入式发送器，方便单测（不碰网络/QQ）。

用法：
    hub = NotifyHub(sender=manager._send_channel, config_getter=manager.get_config)
    await hub.send("任务完成", level="info", event="task.end")
    hub.recent(20)      # 投递日志
    await hub.test("napcat")
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional

LEVELS = {"info": 10, "warning": 20, "error": 30, "critical": 40}


def _parse_hhmm(value: str) -> Optional[int]:
    """'23:30' -> 1410（当天分钟数）；非法返回 None。"""
    value = (value or "").strip()
    if not value or ":" not in value:
        return None
    try:
        h, m = value.split(":", 1)
        h, m = int(h), int(m)
    except (TypeError, ValueError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


class NotifyHub:
    def __init__(
        self,
        sender: Callable[[str, str], Awaitable[Dict[str, Any]]],
        config_getter: Callable[[], Dict[str, Any]],
        log_size: int = 200,
    ) -> None:
        self._sender = sender
        self._config = config_getter
        self._log: Deque[Dict[str, Any]] = deque(maxlen=log_size)
        self._last_at: Dict[str, float] = {}
        self._last_text: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ── 配置读取 ────────────────────────────────────────────────
    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            return (self._config() or {}).get(key, default)
        except Exception:
            return default

    def _channels(self) -> List[str]:
        ch = []
        if self._cfg("notify_qq"):
            ch.append("napcat")
        if self._cfg("notify_official"):
            ch.append("official")
        if self._cfg("notify_webhook_url"):
            ch.append("webhook")
        return ch

    def _quiet_now(self, now_minute: Optional[int] = None) -> bool:
        """是否处于静默时段（支持跨零点，如 23:30-08:00）。"""
        start = _parse_hhmm(self._cfg("notify_quiet_start", ""))
        end = _parse_hhmm(self._cfg("notify_quiet_end", ""))
        if start is None or end is None or start == end:
            return False
        now = time.localtime() if now_minute is None else None
        cur = now_minute if now_minute is not None else now.tm_hour * 60 + now.tm_min
        if start < end:
            return start <= cur < end
        return cur >= start or cur < end

    def _level_ok(self, level: str) -> bool:
        threshold = str(self._cfg("notify_min_level", "info") or "info").lower()
        return LEVELS.get(level, 10) >= LEVELS.get(threshold, 10)

    def _dedupe_hit(self, key: str) -> bool:
        window = float(self._cfg("notify_dedupe_window", 30) or 0)
        if window <= 0:
            return False
        last = self._last_text.get(key, 0.0)
        return (time.time() - last) < window

    # ── 发送 ────────────────────────────────────────────────────
    async def send(
        self,
        text: str,
        level: str = "info",
        event: str = "",
        channels: Optional[List[str]] = None,
        force: bool = False,
        retries: int = 1,
    ) -> Dict[str, Any]:
        """发送通知。返回 {sent: [...], skipped: [...], failed: [...]}。

        force=True 时忽略静默时段、最小等级与去重（用于测试与紧急告警）。
        """
        text = (text or "").strip()
        if not text:
            return {"sent": [], "skipped": [{"reason": "empty"}], "failed": []}

        channels = list(channels or self._channels())
        if not channels:
            return {"sent": [], "skipped": [{"reason": "no-channel"}], "failed": []}

        if not force:
            if not self._level_ok(level):
                self._record("", text, level, event, False, "below-min-level", 0)
                return {"sent": [], "skipped": [{"reason": "below-min-level"}], "failed": []}
            if self._quiet_now() and level != "critical":
                self._record("", text, level, event, False, "quiet-hours", 0)
                return {"sent": [], "skipped": [{"reason": "quiet-hours"}], "failed": []}
            if self._dedupe_hit(f"{event}|{text}"):
                self._record("", text, level, event, False, "duplicate", 0)
                return {"sent": [], "skipped": [{"reason": "duplicate"}], "failed": []}

        sent, failed = [], []
        async with self._lock:
            for channel in channels:
                attempts = 0
                last_error = ""
                ok = False
                while attempts <= max(0, retries):
                    attempts += 1
                    try:
                        result = await self._sender(channel, text)
                        ok = bool(result.get("ok"))
                        last_error = "" if ok else str(result.get("error") or result.get("message") or "send-failed")
                    except Exception as exc:  # 发送器异常不打断其他渠道
                        ok = False
                        last_error = f"{type(exc).__name__}: {exc}"
                    if ok:
                        break
                    if attempts <= retries:
                        await asyncio.sleep(0.4 * attempts)
                self._record(channel, text, level, event, ok, last_error, attempts)
                (sent if ok else failed).append(channel)

            if sent:
                self._last_text[f"{event}|{text}"] = time.time()
                self._last_at[event or "generic"] = time.time()

        return {"sent": sent, "skipped": [], "failed": failed}

    def _record(self, channel: str, text: str, level: str, event: str, ok: bool, error: str, attempts: int) -> None:
        self._log.append(
            {
                "ts": time.time(),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "channel": channel,
                "level": level,
                "event": event,
                "ok": ok,
                "error": error,
                "attempts": attempts,
                "text": text[:500],
            }
        )

    # ── 查询 / 测试 ────────────────────────────────────────────
    def recent(self, limit: int = 30) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 30), 200))
        return list(self._log)[-limit:][::-1]

    def clear(self) -> None:
        self._log.clear()

    def stats(self) -> Dict[str, Any]:
        items = list(self._log)
        return {
            "total": len(items),
            "ok": sum(1 for i in items if i["ok"]),
            "failed": sum(1 for i in items if not i["ok"]),
            "channels": self._channels(),
            "quiet_now": self._quiet_now(),
        }

    async def test(self, channel: str = "") -> Dict[str, Any]:
        target = [channel] if channel else self._channels()
        if not target:
            return {"ok": False, "message": "没有启用的通知渠道（先勾选 NapCat / QQ 官方 / Webhook）"}
        result = await self.send(
            f"[测试] Furina Agent 通知链路正常 · {time.strftime('%H:%M:%S')}",
            level="info",
            event="selftest",
            channels=target,
            force=True,
            retries=0,
        )
        return {"ok": bool(result["sent"]), **result}
