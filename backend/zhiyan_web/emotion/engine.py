"""
情感引擎（神经化学模型）
基于多巴胺、血清素、皮质醇、催产素的四维情绪系统
"""
from __future__ import annotations

import json
import os
import re
import time
import logging
from dataclasses import dataclass, asdict
from typing import Dict

logger = logging.getLogger("ZhiYanWeb.emotion")


@dataclass
class NeurochemicalState:
    """神经化学状态"""
    dopamine: float = 0.5
    serotonin: float = 0.5
    cortisol: float = 0.3
    oxytocin: float = 0.5
    dopamine_decay: float = 0.02
    serotonin_decay: float = 0.01
    cortisol_decay: float = 0.03
    oxytocin_decay: float = 0.015

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'NeurochemicalState':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class EmotionState:
    """情绪状态"""
    mood: str = "平和"
    pleasure: float = 0.5
    arousal: float = 0.45
    dominance: float = 0.55
    intensity: str = "温和"

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'EmotionState':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class EmotionEngine:
    """情感引擎：基于神经化学模型的情绪状态机"""

    MOOD_EMOJI_MAP = {
        "兴奋": "⚡", "紧张": "💢", "低落": "😔", "自信": "😎",
        "平和": "😊", "害羞": "😳", "困惑": "🤔", "开心": "😄",
        "生气": "😡", "惊讶": "😲", "满足": "😌", "沮丧": "😞",
    }

    MOOD_DESCRIPTION_MAP = {
        "兴奋": "心情雀跃，电磁能力涌动",
        "紧张": "略感紧张，眉头微皱",
        "低落": "情绪低落，声音放轻",
        "自信": "充满自信，目光坚定",
        "平和": "心情平和，神态自然",
        "害羞": "脸颊微红，目光躲闪",
        "困惑": "歪头思考，感到困惑",
        "开心": "笑容满面，心情愉悦",
        "生气": "眉头紧锁，电弧微闪",
        "惊讶": "睁大眼睛，感到惊讶",
        "满足": "满足地微笑，心情舒畅",
        "沮丧": "垂头丧气，无精打采",
    }

    INTENSITY_LEVELS = [(0.8, "强烈"), (0.6, "明显"), (0.4, "温和"), (0.2, "轻微"), (0.0, "平静")]

    # 情感关键词模式
    POSITIVE_PATTERNS = [
        (r"(谢谢|感谢|多谢|感恩)", 0.12),
        (r"(开心|高兴|快乐|愉快|喜悦)", 0.15),
        (r"(喜欢|爱|好爱|最爱|超爱)", 0.18),
        (r"(棒|厉害|太棒了|厉害了|好厉害)", 0.15),
        (r"(可爱|萌|好看|漂亮|美|帅)", 0.10),
        (r"(好|很好|非常好|真不错|不错|挺好的)", 0.08),
        (r"(一起|陪伴|陪你|聊天|说话)", 0.08),
        (r"(哈哈|呵呵|嘻嘻|hh|haha)", 0.05),
        (r"(温柔|体贴|关心|在乎|理解)", 0.12),
        (r"(有趣|好玩|有意思|搞笑)", 0.08),
        (r"(抱抱|抱|摸摸|贴贴)", 0.12),
    ]

    NEGATIVE_PATTERNS = [
        (r"(讨厌|烦|好烦|烦躁|烦死了)", 0.15),
        (r"(糟糕|坏了|完了|不好|不妙)", 0.12),
        (r"(生气|怒了|气死|火大|可恶)", 0.18),
        (r"(难过|伤心|悲伤|痛苦|心痛)", 0.18),
        (r"(抱歉|对不起|不好意思|sry)", 0.08),
        (r"(错误|出错了|有问题|bug|故障)", 0.10),
        (r"(失败|输了|不行|不好|没用)", 0.12),
        (r"(无聊|没意思|无趣|乏味)", 0.10),
        (r"(累|好累|累了|疲惫|困)", 0.08),
        (r"(失望|绝望|失落)", 0.15),
        (r"(可恶|该死|混蛋)", 0.15),
        (r"(哭|哭了|呜呜|想哭|泪)", 0.12),
        (r"(差|烂|垃圾|废物|糟糕)", 0.15),
    ]

    SOCIAL_PATTERNS = [
        (r"(一起|陪伴|聊天|说话|交流|分享)", 0.08),
        (r"(你|你在|你在吗|在吗|来了)", 0.04),
        (r"(想|想念|想你了|想你)", 0.12),
        (r"(关心|在乎|担心|牵挂)", 0.10),
    ]

    AFFECTION_PATTERNS = [
        (r"(亲|爱|喜欢你|我爱你|喜欢|爱死|最爱|超喜欢)", 0.20),
        (r"(抱抱|抱|贴贴|亲亲|摸摸)", 0.15),
        (r"(想你|想你了|想见你|陪伴|陪你)", 0.12),
        (r"(温柔|可爱|乖|听话|贴心)", 0.10),
    ]

    NEGATION_WORDS = r"(不|没|不会|没有|别|不要|不是|并非|不太)"

    def __init__(self, state_path: str = None):
        if state_path is None:
            from zhiyan_web.config import DATA_DIR
            state_path = os.path.join(DATA_DIR, "emotion_state.json")

        self.state_path = state_path
        self.neurochemistry = NeurochemicalState()
        self.emotion = EmotionState()
        self._last_update_time = time.time()
        self._load_state()
        logger.info("情感引擎（神经化学模型）已初始化")

    def _load_state(self):
        if not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if 'neurochemistry' in data:
                self.neurochemistry = NeurochemicalState.from_dict(data['neurochemistry'])
            if 'emotion' in data:
                self.emotion = EmotionState.from_dict(data['emotion'])
            if 'last_update_time' in data:
                self._last_update_time = data['last_update_time']
            logger.info(f"情感状态已加载: {self.state_path}")
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logger.error(f"加载情感状态失败: {e}")

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            data = {
                'neurochemistry': self.neurochemistry.to_dict(),
                'emotion': self.emotion.to_dict(),
                'last_update_time': self._last_update_time,
                'saved_at': time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            temp_path = self.state_path + '.tmp'
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if os.path.exists(self.state_path):
                os.remove(self.state_path)
            os.rename(temp_path, self.state_path)
        except Exception as e:
            logger.error(f"保存情感状态失败: {e}")

    def _apply_decay(self):
        now = time.time()
        hours_elapsed = max(0, (now - self._last_update_time) / 3600)
        if hours_elapsed <= 0:
            return
        self.neurochemistry.dopamine = max(0.0, min(1.0,
            self.neurochemistry.dopamine - self.neurochemistry.dopamine_decay * hours_elapsed))
        self.neurochemistry.serotonin = max(0.0, min(1.0,
            self.neurochemistry.serotonin - self.neurochemistry.serotonin_decay * hours_elapsed))
        self.neurochemistry.cortisol = max(0.0, min(1.0,
            self.neurochemistry.cortisol - self.neurochemistry.cortisol_decay * hours_elapsed))
        self.neurochemistry.oxytocin = max(0.0, min(1.0,
            self.neurochemistry.oxytocin - self.neurochemistry.oxytocin_decay * hours_elapsed))
        self._last_update_time = now

    def _map_neurochemistry_to_emotion(self):
        d = self.neurochemistry.dopamine
        s = self.neurochemistry.serotonin
        c = self.neurochemistry.cortisol
        o = self.neurochemistry.oxytocin

        pleasure = max(0.0, min(1.0, d * 0.4 + s * 0.4 + o * 0.2 - c * 0.3))
        arousal = max(0.0, min(1.0, d * 0.6 + c * 0.4))
        dominance = max(0.0, min(1.0, s * 0.4 + o * 0.4 - c * 0.2))

        mood = self._determine_mood(pleasure, arousal, dominance, d, c, o)
        intensity = self._get_intensity_level(arousal)

        self.emotion = EmotionState(mood=mood, pleasure=pleasure, arousal=arousal, dominance=dominance, intensity=intensity)

    def _determine_mood(self, pleasure: float, arousal: float, dominance: float,
                        dopamine: float, cortisol: float, oxytocin: float) -> str:
        if pleasure < 0.25 and cortisol > 0.6:
            return "生气"
        if cortisol > 0.5 and 0.4 < arousal < 0.7:
            return "紧张"
        if pleasure < 0.35 and dopamine < 0.3:
            return "低落"
        if pleasure < 0.35 and 0.4 < arousal < 0.7:
            return "困惑"
        if dopamine > 0.7 and pleasure > 0.7:
            return "兴奋"
        if pleasure > 0.65 and oxytocin > 0.6 and arousal < 0.4:
            return "害羞"
        if pleasure > 0.7 and 0.5 < arousal < 0.75:
            return "开心"
        if arousal > 0.85:
            return "惊讶"
        if dopamine > 0.5 and oxytocin > 0.5 and pleasure > 0.5:
            return "自信"
        if pleasure > 0.6 and arousal < 0.4:
            return "满足"
        if dopamine < 0.3 and cortisol > 0.4:
            return "沮丧"
        return "平和"

    def _get_intensity_level(self, arousal: float) -> str:
        for threshold, level in self.INTENSITY_LEVELS:
            if arousal >= threshold:
                return level
        return "平静"

    def _score_patterns(self, text: str, patterns: list) -> float:
        total = 0.0
        text_lower = text.lower()
        for pattern, weight in patterns:
            matches = re.findall(pattern, text_lower)
            if matches:
                total += len(matches) * weight
        return total

    def _update_neurochemicals_from_text(self, text: str):
        positive_score = self._score_patterns(text, self.POSITIVE_PATTERNS)
        negative_score = self._score_patterns(text, self.NEGATIVE_PATTERNS)
        social_score = self._score_patterns(text, self.SOCIAL_PATTERNS)
        affection_score = self._score_patterns(text, self.AFFECTION_PATTERNS)

        # 否定词处理
        negation_count = len(re.findall(self.NEGATION_WORDS, text))
        if negation_count > 0:
            neg_factor = max(0.5, 1.0 - negation_count * 0.15)
            positive_score *= neg_factor
            negative_score *= neg_factor

        if positive_score > 0:
            self.neurochemistry.dopamine = min(1.0, self.neurochemistry.dopamine + positive_score * 0.12)
            self.neurochemistry.serotonin = min(1.0, self.neurochemistry.serotonin + positive_score * 0.08)
        if negative_score > 0:
            self.neurochemistry.cortisol = min(1.0, self.neurochemistry.cortisol + negative_score * 0.15)
            self.neurochemistry.serotonin = max(0.0, self.neurochemistry.serotonin - negative_score * 0.08)
        if social_score > 0:
            self.neurochemistry.oxytocin = min(1.0, self.neurochemistry.oxytocin + social_score * 0.06)
        if affection_score > 0:
            self.neurochemistry.oxytocin = min(1.0, self.neurochemistry.oxytocin + affection_score * 0.15)
            self.neurochemistry.dopamine = min(1.0, self.neurochemistry.dopamine + affection_score * 0.08)

    def update(self, event_type: str = None, value: float = 0.0, text: str = None) -> Dict:
        """
        更新情绪状态

        Args:
            event_type: 事件类型 (positive/negative/social/stress/achievement/affection/neutral)
            value: 事件值 (-1.0 到 1.0)
            text: 文本内容（用于情感关键词检测）
        """
        self._apply_decay()

        if text:
            self._update_neurochemicals_from_text(text)

        if event_type:
            if event_type == 'positive':
                self.neurochemistry.dopamine = min(1.0, self.neurochemistry.dopamine + 0.15)
                self.neurochemistry.serotonin = min(1.0, self.neurochemistry.serotonin + 0.1)
                self.neurochemistry.cortisol = max(0.0, self.neurochemistry.cortisol - 0.1)
            elif event_type == 'negative':
                self.neurochemistry.dopamine = max(0.0, self.neurochemistry.dopamine - 0.2)
                self.neurochemistry.serotonin = max(0.0, self.neurochemistry.serotonin - 0.15)
                self.neurochemistry.cortisol = min(1.0, self.neurochemistry.cortisol + 0.15)
            elif event_type == 'social':
                self.neurochemistry.oxytocin = min(1.0, self.neurochemistry.oxytocin + 0.1)
                self.neurochemistry.serotonin = min(1.0, self.neurochemistry.serotonin + 0.05)
            elif event_type == 'stress':
                self.neurochemistry.cortisol = min(1.0, self.neurochemistry.cortisol + 0.2)
                self.neurochemistry.serotonin = max(0.0, self.neurochemistry.serotonin - 0.1)
            elif event_type == 'achievement':
                self.neurochemistry.dopamine = min(1.0, self.neurochemistry.dopamine + 0.25)
                self.neurochemistry.serotonin = min(1.0, self.neurochemistry.serotonin + 0.15)
            elif event_type == 'affection':
                self.neurochemistry.oxytocin = min(1.0, self.neurochemistry.oxytocin + 0.2)
                self.neurochemistry.dopamine = min(1.0, self.neurochemistry.dopamine + 0.1)
                self.neurochemistry.serotonin = min(1.0, self.neurochemistry.serotonin + 0.1)

        if value != 0.0:
            self.neurochemistry.dopamine = max(0.0, min(1.0, self.neurochemistry.dopamine + value * 0.1))
            self.neurochemistry.serotonin = max(0.0, min(1.0, self.neurochemistry.serotonin + value * 0.05))

        self._map_neurochemistry_to_emotion()
        self._save_state()

        return self.get_emotion()

    def get_emotion(self) -> Dict:
        return {
            "mood": self.emotion.mood,
            "pleasure": round(self.emotion.pleasure, 3),
            "arousal": round(self.emotion.arousal, 3),
            "dominance": round(self.emotion.dominance, 3),
            "intensity": self.emotion.intensity,
            "emoji": self.MOOD_EMOJI_MAP.get(self.emotion.mood, "😊"),
            "description": self.MOOD_DESCRIPTION_MAP.get(self.emotion.mood, "神态自然"),
            "neurochemistry": {
                "dopamine": round(self.neurochemistry.dopamine, 3),
                "serotonin": round(self.neurochemistry.serotonin, 3),
                "cortisol": round(self.neurochemistry.cortisol, 3),
                "oxytocin": round(self.neurochemistry.oxytocin, 3),
            },
        }

    def format_for_prompt(self) -> str:
        emotion = self.get_emotion()
        return (f"【当前情绪状态】{emotion['mood']}（{emotion['intensity']}） - {emotion['description']}。"
                f"请在回复中自然体现这一情绪状态。")

    def get_history(self, limit: int = 12) -> list:
        """获取情绪历史"""
        if not os.path.exists(self.state_path):
            return []
        try:
            with open(self.state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            history = data.get('history', [])
            return history[-limit:]
        except Exception:
            return []

    def reset(self):
        self.neurochemistry = NeurochemicalState()
        self.emotion = EmotionState()
        self._last_update_time = time.time()
        self._save_state()
        logger.info("情感状态已重置")

    def get_statistics(self) -> Dict:
        emotion = self.get_emotion()
        return {
            "current_mood": emotion["mood"],
            "emoji": emotion["emoji"],
            "intensity": emotion["intensity"],
            "pad": {
                "pleasure": emotion["pleasure"],
                "arousal": emotion["arousal"],
                "dominance": emotion["dominance"],
            },
            "neurochemistry": emotion["neurochemistry"],
        }

    def apply_bond_likability_influence(
        self, bond_status: dict = None, likability_level: int = None
    ) -> None:
        """
        应用羁绊和好感度对情绪状态的影响

        羁绊影响:
        - 温暖度(warmth) → 催产素↑, 皮质醇↓
        - 信任度(trust) → 血清素↑, 皮质醇↓

        好感度影响:
        - 等级越高 → 多巴胺↑, 催产素↑
        - 等级越低 → 皮质醇微微↑（陌生感）
        """
        changed = False

        if bond_status:
            warmth = float(bond_status.get("warmth", 0.5))
            trust = float(bond_status.get("trust", 0.5))

            if warmth > 0.5:
                boost = (warmth - 0.5) * 0.15
                self.neurochemistry.oxytocin = min(1.0, self.neurochemistry.oxytocin + boost)
                self.neurochemistry.cortisol = max(0.0, self.neurochemistry.cortisol - boost * 0.5)
                changed = True
            elif warmth < 0.3:
                penalty = (0.3 - warmth) * 0.08
                self.neurochemistry.cortisol = min(1.0, self.neurochemistry.cortisol + penalty)
                changed = True

            if trust > 0.5:
                boost = (trust - 0.5) * 0.12
                self.neurochemistry.serotonin = min(1.0, self.neurochemistry.serotonin + boost)
                self.neurochemistry.cortisol = max(0.0, self.neurochemistry.cortisol - boost * 0.3)
                changed = True
            elif trust < 0.3:
                penalty = (0.3 - trust) * 0.06
                self.neurochemistry.cortisol = min(1.0, self.neurochemistry.cortisol + penalty)
                changed = True

        if likability_level is not None:
            if likability_level >= 5:
                boost = (likability_level - 4) * 0.03
                self.neurochemistry.dopamine = min(1.0, self.neurochemistry.dopamine + boost)
                self.neurochemistry.oxytocin = min(1.0, self.neurochemistry.oxytocin + boost * 0.5)
                changed = True
            elif likability_level <= 2:
                penalty = (3 - likability_level) * 0.02
                self.neurochemistry.cortisol = min(1.0, self.neurochemistry.cortisol + penalty)
                changed = True

        if changed:
            self._map_neurochemistry_to_emotion()
            self._save_state()