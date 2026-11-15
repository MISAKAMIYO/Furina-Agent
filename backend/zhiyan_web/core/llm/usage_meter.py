"""
LLM Token 用量与费用记账。

设计目标：
- 零依赖（只用标准库），每次调用追加一行 JSONL，读取时按日/模型聚合；
- 金额按内置价目表估算（USD / 百万 token），价目表可在 summary 结果里一并返回，
  前端只负责展示，避免前后端价格不一致；
- 流式响应拿不到 usage 时按字符数估算，并在记录里标记 estimated=True。
"""
from __future__ import annotations

import datetime
import json
import os
import threading
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()

# USD / 1M tokens（in, out）。仅用于估算展示，不参与计费。
DEFAULT_PRICES: Dict[str, Dict[str, float]] = {
    "deepseek-flash": {"in": 0.10, "out": 0.30},
    "deepseek-chat": {"in": 0.27, "out": 1.10},
    "deepseek-reasoner": {"in": 0.55, "out": 2.19},
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
    "gpt-4.1": {"in": 2.00, "out": 8.00},
    "claude-3-5-sonnet": {"in": 3.00, "out": 15.00},
    "claude-3-7-sonnet": {"in": 3.00, "out": 15.00},
    "gemini-1.5-pro": {"in": 1.25, "out": 5.00},
    "qwen-max": {"in": 1.60, "out": 6.40},
    "_default": {"in": 0.50, "out": 1.50},
}


def _usage_dir() -> str:
    try:
        from zhiyan_web.config import DATA_DIR
        base = DATA_DIR
    except Exception:
        base = os.path.join(os.getcwd(), "data")
    path = os.path.join(base, "usage")
    os.makedirs(path, exist_ok=True)
    return path


def _usage_file() -> str:
    return os.path.join(_usage_dir(), "usage.jsonl")


def price_for(model: str) -> Dict[str, float]:
    """按模型名匹配价目表（前缀/包含匹配，取最长的命中项）。"""
    m = (model or "").lower()
    best_key = ""
    for key in DEFAULT_PRICES:
        if key == "_default":
            continue
        if key in m and len(key) > len(best_key):
            best_key = key
    return DEFAULT_PRICES.get(best_key) or DEFAULT_PRICES["_default"]


def record_usage(model: str, prompt_tokens: int = 0, completion_tokens: int = 0,
                 *, provider: str = "", source: str = "",
                 estimated: bool = False) -> None:
    """追加一条用量记录（失败静默，绝不影响主流程）。"""
    try:
        prompt_tokens = max(0, int(prompt_tokens or 0))
        completion_tokens = max(0, int(completion_tokens or 0))
        if prompt_tokens == 0 and completion_tokens == 0:
            return
        now = datetime.datetime.now()
        row = {
            "ts": now.isoformat(timespec="seconds"),
            "date": now.strftime("%Y-%m-%d"),
            "model": model or "",
            "provider": provider or "",
            "source": source or "",
            "in": prompt_tokens,
            "out": completion_tokens,
            "estimated": bool(estimated),
        }
        line = json.dumps(row, ensure_ascii=False)
        with _LOCK:
            with open(_usage_file(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文约 1.8 字符/token，其它约 4 字符/token）。"""
    if not text:
        return 0
    chinese = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - chinese
    return int(chinese / 1.8 + other / 4) + 1


def estimate_message_tokens(messages: Any) -> int:
    """估算一组消息的 prompt tokens（兼容 dict / 对象两种消息形态）。"""
    total = 0
    for m in (messages or []):
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        if isinstance(content, list):
            content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        total += _estimate_tokens(str(content or ""))
    return total


def summary(days: int = 30) -> Dict[str, Any]:
    """聚合最近 N 天的用量：总计、按日、按模型、价目表。"""
    try:
        days = max(1, min(int(days or 30), 365))
    except Exception:
        days = 30
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()

    rows: List[Dict[str, Any]] = []
    path = _usage_file()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(row.get("date") or "") >= since:
                        rows.append(row)
        except OSError:
            rows = []

    total_in = total_out = 0
    total_cost = 0.0
    by_day: Dict[str, Dict[str, Any]] = {}
    by_model: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        p_in = int(row.get("in") or 0)
        p_out = int(row.get("out") or 0)
        price = price_for(str(row.get("model") or ""))
        cost = p_in / 1_000_000 * price["in"] + p_out / 1_000_000 * price["out"]
        total_in += p_in
        total_out += p_out
        total_cost += cost
        day = str(row.get("date") or "")
        d = by_day.setdefault(day, {"date": day, "in": 0, "out": 0, "cost": 0.0, "calls": 0})
        d["in"] += p_in
        d["out"] += p_out
        d["cost"] += cost
        d["calls"] += 1
        model = str(row.get("model") or "unknown")
        m = by_model.setdefault(model, {"model": model, "in": 0, "out": 0, "cost": 0.0, "calls": 0})
        m["in"] += p_in
        m["out"] += p_out
        m["cost"] += cost
        m["calls"] += 1

    return {
        "days": days,
        "since": since,
        "totals": {
            "in": total_in,
            "out": total_out,
            "tokens": total_in + total_out,
            "cost": round(total_cost, 4),
            "calls": len(rows),
        },
        "by_day": sorted(by_day.values(), key=lambda x: x["date"]),
        "by_model": sorted(by_model.values(), key=lambda x: x["cost"], reverse=True),
        "prices": {k: v for k, v in DEFAULT_PRICES.items() if k != "_default"},
    }
