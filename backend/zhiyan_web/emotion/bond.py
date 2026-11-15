"""
羁绊系统
四维羁绊模型：warmth / trust / formality / humor
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import logging
from typing import Dict

logger = logging.getLogger("ZhiYanWeb.emotion")


class BondEngine:
    """四维羁绊系统"""

    def __init__(self, state_file: str = None):
        if state_file is None:
            from zhiyan_web.config import DATA_DIR
            state_file = os.path.join(DATA_DIR, "bond_state.json")

        self.state_file = state_file
        self.state = self._load_state()
        logger.info("羁绊系统已初始化")

    def _load_state(self) -> Dict:
        default_state = {
            "warmth": 0.52,
            "trust": 0.5,
            "formality": 0.55,
            "humor": 0.45,
            "level": "熟悉",
            "updated_at": time.time(),
            "history": [],
        }
        if not os.path.exists(self.state_file):
            return default_state
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for key, default in default_state.items():
                loaded.setdefault(key, default)
            return loaded
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("羁绊状态文件不存在或格式错误，使用默认状态")
            return default_state

    def _save_state(self) -> None:
        directory = os.path.dirname(self.state_file)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def update(self, user_message: str, assistant_reply: str = "") -> Dict:
        """根据对话更新羁绊值"""
        text = f"{user_message}\n{assistant_reply}".lower()

        positive_hits = len(re.findall(r"(谢谢|感谢|喜欢|好|可爱|开心|一起|棒|厉害|亲|亲爱的|帮我)", text))
        negative_hits = len(re.findall(r"(讨厌|烦|糟糕|坏|错误|问题|抱歉|麻烦|气)", text))

        warm_change = min(0.05, max(-0.05, (positive_hits - negative_hits) * 0.008))
        trust_change = min(0.05, max(-0.05, (positive_hits - negative_hits) * 0.006))
        humor_change = min(0.05, max(-0.05, positive_hits * 0.004 - negative_hits * 0.003))
        formality_change = -0.003 * max(0, positive_hits - negative_hits)

        self._apply_decay()

        self.state["warmth"] = self._clamp(float(self.state.get("warmth", 0.52)) + warm_change)
        self.state["trust"] = self._clamp(float(self.state.get("trust", 0.5)) + trust_change)
        self.state["humor"] = self._clamp(float(self.state.get("humor", 0.45)) + humor_change)
        self.state["formality"] = self._clamp(float(self.state.get("formality", 0.55)) + formality_change)
        self.state["level"] = self._classify_level(
            float(self.state.get("warmth", 0.52)),
            float(self.state.get("trust", 0.5)),
        )
        self.state["updated_at"] = time.time()

        self.state.setdefault("history", []).append({
            "timestamp": int(time.time()),
            "warmth": round(float(self.state["warmth"]), 3),
            "trust": round(float(self.state["trust"]), 3),
            "formality": round(float(self.state["formality"]), 3),
            "humor": round(float(self.state["humor"]), 3),
            "level": self.state["level"],
        })
        self.state["history"] = self.state["history"][-12:]

        self._save_state()
        return self.get_status()

    def _apply_decay(self) -> None:
        last_time = float(self.state.get("updated_at", time.time()))
        idle_days = max(0.0, (time.time() - last_time) / 86400.0)
        if idle_days <= 0:
            return
        warmth_decay = math.pow(0.5, idle_days / 14.0)
        trust_decay = math.pow(0.5, idle_days / 180.0)
        self.state["warmth"] = self._clamp(float(self.state.get("warmth", 0.52)) * warmth_decay)
        self.state["trust"] = self._clamp(float(self.state.get("trust", 0.5)) * trust_decay)

    def _classify_level(self, warmth: float, trust: float) -> str:
        score = (warmth + trust) / 2.0
        if score > 0.8:
            return "亲密"
        if score > 0.65:
            return "熟悉"
        if score > 0.45:
            return "普通"
        return "陌生"

    def _clamp(self, value: float) -> float:
        return max(0.0, min(1.0, value))

    def get_status(self) -> Dict:
        return {
            "level": self.state.get("level", "熟悉"),
            "warmth": round(float(self.state.get("warmth", 0.52)), 3),
            "trust": round(float(self.state.get("trust", 0.5)), 3),
            "formality": round(float(self.state.get("formality", 0.55)), 3),
            "humor": round(float(self.state.get("humor", 0.45)), 3),
        }

    def get_history(self, limit: int = 12) -> list:
        return self.state.get("history", [])[-limit:]

    def reset(self):
        self.state = {
            "warmth": 0.52,
            "trust": 0.5,
            "formality": 0.55,
            "humor": 0.45,
            "level": "熟悉",
            "updated_at": time.time(),
            "history": [],
        }
        self._save_state()
        logger.info("羁绊状态已重置")