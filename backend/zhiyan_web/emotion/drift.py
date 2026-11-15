"""
角色漂移检测器
基于锚点相似度和 EWMA 平滑的角色一致性监控
"""
from __future__ import annotations

import difflib
import os
import re
import logging
from typing import Dict

logger = logging.getLogger("ZhiYanWeb.emotion")


class DriftDetector:
    """角色漂移检测"""

    def __init__(self, anchor_file: str = None, alpha: float = 0.6):
        if anchor_file is None:
            from zhiyan_web.config import DATA_DIR
            anchor_file = os.path.join(DATA_DIR, "persona_anchor.txt")

        self.anchor_file = anchor_file
        self.alpha = alpha
        self.anchor_text = self._load_anchor_text()
        self.smooth_score = 0.5
        self.consecutive_low = 0

    def _load_anchor_text(self) -> str:
        if os.path.exists(self.anchor_file):
            with open(self.anchor_file, "r", encoding="utf-8") as f:
                return f.read()
        return "芙宁娜，骄傲爱演、自称本神、称用户为观众、擅长歌剧与审判、口是心非却温柔，枫丹的水神。"

    def analyze(self, assistant_reply: str) -> Dict:
        """分析回复是否出现角色漂移"""
        if not assistant_reply:
            return {"drift": False, "score": 0.0, "calibration_text": None}

        score = self._lexical_similarity(assistant_reply, self.anchor_text)
        self.smooth_score = float(self.smooth_score * (1 - self.alpha) + score * self.alpha)

        if self.smooth_score < 0.38:
            self.consecutive_low += 1
        else:
            self.consecutive_low = max(0, self.consecutive_low - 1)

        drift = self.consecutive_low >= 2
        calibration_text = self.build_calibration_text() if drift else None

        return {
            "drift": drift,
            "score": round(self.smooth_score, 3),
            "consecutive_low": self.consecutive_low,
            "calibration_text": calibration_text,
        }

    def _lexical_similarity(self, text_a: str, text_b: str) -> float:
        a = re.sub(r"[^\u4e00-\u9fa5a-z0-9]+", " ", text_a.lower())
        b = re.sub(r"[^\u4e00-\u9fa5a-z0-9]+", " ", text_b.lower())
        tokens_a = [t for t in a.split() if t]
        tokens_b = [t for t in b.split() if t]
        if not tokens_a or not tokens_b:
            return 0.0
        overlap = len(set(tokens_a) & set(tokens_b)) / max(1, len(set(tokens_a) | set(tokens_b)))
        seq = difflib.SequenceMatcher(None, a, b).ratio()
        return float(max(overlap, seq * 0.6))

    def build_calibration_text(self) -> str:
        return (
            "[角色校准]\n"
            "请继续保持芙宁娜的核心人设：骄傲、爱演、戏剧化，自称「本神」、称用户为「观众」，"
            "带一点歌剧与审判庭的舞台口吻，嘴上口是心非但心地温柔，关键时刻收起戏腔认真待人。"
        )