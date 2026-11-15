"""
BetterGI 异常类型、动作映射与按自然日计数

职责边界：
- 只定义「异常是什么、该做什么、今天发生了几次」
- 不探测进程、不执行动作（探测在 health.py，执行在 service.py）

异常类型（对应手写稿 R9）：
- bgi_error：BetterGI 主动回报错误 → 同日累计到上限后重启一条龙副本
- game_dead：BetterGI 存活但原神进程已死 → 只回报，不继续处理
- not_started：规定时间段内 BetterGI 与原神都未启动 → 回报并启动一条龙副本
- task_timeout：任务异常退出、原神仍存活超过规定时长 → 自动关机并回报
- unresponsive：长时间无事件 / 日志不更新（R10 的模式决定触发阈值）→ 自愈链
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger("ZhiYanWeb.bettergi.errors")

ERROR_TYPES = ("bgi_error", "game_dead", "not_started", "task_timeout", "unresponsive")

ERROR_NAMES = {
    "bgi_error": "BetterGI 报错",
    "game_dead": "游戏进程已退出",
    "not_started": "时间段内未启动",
    "task_timeout": "任务超时",
    "unresponsive": "长时间无响应",
}

# 动作枚举：relaunch 重启一条龙 / report_only 只回报 / shutdown 关机 / self_heal 自愈链
ACTIONS = ("relaunch", "report_only", "shutdown", "self_heal")

DEFAULT_ACTION_MAP: Dict[str, str] = {
    "bgi_error": "relaunch",
    "game_dead": "report_only",
    "not_started": "relaunch",
    "task_timeout": "shutdown",
    "unresponsive": "self_heal",
}


def normalize_action_map(raw: Any) -> Dict[str, str]:
    """把配置里的映射补全成完整表（非法值回退默认）"""
    result = dict(DEFAULT_ACTION_MAP)
    if isinstance(raw, dict):
        for key, value in raw.items():
            k = str(key or "").strip().lower()
            v = str(value or "").strip().lower()
            if k in DEFAULT_ACTION_MAP and v in ACTIONS:
                result[k] = v
    return result


def parse_hhmm(text: str) -> Optional[int]:
    """把 HH:MM 解析成当天分钟数"""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        hour, minute = raw.replace("：", ":").split(":")
        h = int(hour)
        m = int(minute)
    except Exception:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def minutes_now(now: Optional[datetime] = None) -> int:
    now = now or datetime.now()
    return now.hour * 60 + now.minute


def in_active_window(start: str, end: str, now: Optional[datetime] = None) -> bool:
    """判断当前是否落在规定时间段内（支持跨午夜；留空视为不限制）"""
    start_min = parse_hhmm(start)
    end_min = parse_hhmm(end)
    if start_min is None or end_min is None:
        return False
    current = minutes_now(now)
    if start_min <= end_min:
        return start_min <= current <= end_min
    # 跨午夜，例如 22:00 - 06:00
    return current >= start_min or current <= end_min


class DailyErrorCounter:
    """按自然日累计错误次数，落盘跨重启保留"""

    def __init__(self, path: str):
        self.path = path
        self._data: Dict[str, Any] = {"date": "", "counts": {}}
        self.load()

    @staticmethod
    def today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def load(self) -> None:
        today = self.today()
        data: Dict[str, Any] = {"date": today, "counts": {}}
        try:
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and str(raw.get("date") or "") == today:
                    counts = raw.get("counts")
                    if isinstance(counts, dict):
                        data["counts"] = {str(k): int(v) for k, v in counts.items() if str(v).isdigit()}
        except Exception as e:
            logger.warning(f"读取错误计数失败: {e}")
        self._data = data

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"写入错误计数失败: {e}")

    def _ensure_today(self) -> None:
        today = self.today()
        if self._data.get("date") != today:
            self._data = {"date": today, "counts": {}}
            self.save()

    def bump(self, kind: str, limit: int = 3) -> Dict[str, Any]:
        """记一次错误，返回当前次数与是否达到上限"""
        self._ensure_today()
        counts = self._data.setdefault("counts", {})
        counts[kind] = int(counts.get(kind) or 0) + 1
        self.save()
        count = counts[kind]
        return {"kind": kind, "count": count, "limit": max(1, int(limit)), "reached": count >= max(1, int(limit))}

    def count(self, kind: Optional[str] = None) -> int:
        self._ensure_today()
        counts = self._data.get("counts") or {}
        if kind:
            return int(counts.get(kind) or 0)
        return sum(int(v or 0) for v in counts.values())

    def reset(self) -> Dict[str, Any]:
        self._data = {"date": self.today(), "counts": {}}
        self.save()
        return self.status()

    def status(self) -> Dict[str, Any]:
        self._ensure_today()
        counts = dict(self._data.get("counts") or {})
        return {"date": self._data.get("date"), "counts": counts, "total": sum(int(v or 0) for v in counts.values())}
