"""
主动交互引擎
基于情绪状态、羁绊值、好感度和时间的主动消息推送

功能:
- 根据时间段生成问候
- 基于情感状态调整消息内容
- 基于好感度等级调整交互频率
- 基于羁绊等级调整消息亲密程度
- 记忆回顾提醒
- 情感波动触发问候
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.proactive")


class ProactiveEngine:
    """
    主动交互引擎
    根据情绪状态、羁绊值、好感度和时间等因素主动发起对话
    """

    def __init__(self, emotion_engine=None, bond_engine=None):
        """
        Args:
            emotion_engine: 情绪引擎实例
            bond_engine: 羁绊引擎实例
        """
        self.emotion_engine = emotion_engine
        self.bond_engine = bond_engine

        # 配置
        self.min_interval_minutes = 30
        self.max_interval_minutes = 480

        # 状态
        self.last_interaction_time: Optional[datetime] = None
        self.enabled = True

        # 触发回调
        self._on_proactive_message: Optional[Callable] = None

        # 已发送的消息历史
        self._sent_messages: List[str] = []

        # 上次触发的情感状态
        self._last_emotion_mood: str = ""

        # 状态文件
        from zhiyan_web.config import DATA_DIR
        self.state_file = os.path.join(DATA_DIR, "proactive_state.json")
        self._load_state()

        logger.info("主动交互引擎已初始化")

    def _load_state(self):
        """加载状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("last_interaction_time"):
                    self.last_interaction_time = datetime.fromisoformat(data["last_interaction_time"])
                self.enabled = data.get("enabled", True)
                self._sent_messages = data.get("sent_messages", [])
                self._last_emotion_mood = data.get("last_emotion_mood", "")
            except Exception as e:
                logger.error(f"加载主动交互状态失败: {e}")

    def _save_state(self):
        """保存状态"""
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            data = {
                "last_interaction_time": self.last_interaction_time.isoformat() if self.last_interaction_time else None,
                "enabled": self.enabled,
                "sent_messages": self._sent_messages[-50:],
                "last_emotion_mood": self._last_emotion_mood,
                "updated_at": datetime.now().isoformat(),
            }
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存主动交互状态失败: {e}")

    def set_callback(self, callback: Callable):
        """设置主动消息回调"""
        self._on_proactive_message = callback

    def should_interact(self) -> bool:
        """判断是否应该主动交互"""
        if not self.enabled:
            return False
        if not self.last_interaction_time:
            return True

        interval = datetime.now() - self.last_interaction_time
        threshold = self._get_interval_threshold()

        return interval >= threshold

    def _get_interval_threshold(self) -> timedelta:
        """根据好感度等级和羁绊值计算交互间隔阈值"""
        # 从好感度系统获取间隔
        try:
            from zhiyan_web.likability.likability import get_likability_engine
            likability = get_likability_engine()
            likability_interval = likability.get_proactive_interval()
            base_minutes = likability_interval
        except Exception:
            base_minutes = self.max_interval_minutes

        # 羁绊等级调整
        bond_level = self._get_bond_level()
        if bond_level >= 4:
            base_minutes = max(self.min_interval_minutes, int(base_minutes * 0.5))
        elif bond_level >= 3:
            base_minutes = max(self.min_interval_minutes, int(base_minutes * 0.7))

        # 情感状态调整（如果情绪低落，更频繁地关心）
        try:
            if self.emotion_engine:
                emotion = self.emotion_engine.get_emotion()
                mood = emotion.get("mood", "")
                if mood in ("低落", "沮丧", "生气"):
                    base_minutes = max(self.min_interval_minutes, int(base_minutes * 0.6))
        except Exception:
            pass

        return timedelta(minutes=base_minutes)

    def _get_bond_level(self) -> int:
        """获取羁绊等级"""
        if not self.bond_engine:
            return 1
        try:
            status = self.bond_engine.get_status()
            warmth = status.get("warmth", 0)
            trust = status.get("trust", 0)
            avg = (warmth + trust) / 2
            if avg >= 0.8:
                return 5
            elif avg >= 0.65:
                return 4
            elif avg >= 0.45:
                return 3
            elif avg >= 0.25:
                return 2
            return 1
        except Exception:
            return 1

    def generate_message(self) -> Optional[str]:
        """生成主动交互消息"""
        now = datetime.now()
        hour = now.hour

        # 检查情感波动
        emotion_msg = self._check_emotion_trigger()

        # 根据时间段生成消息
        if 6 <= hour < 9:
            return self._generate_morning_greeting()
        elif 11 <= hour < 13:
            return self._generate_noon_greeting()
        elif 17 <= hour < 19:
            return self._generate_evening_greeting()
        elif 21 <= hour < 23:
            return self._generate_night_greeting()
        else:
            return self._generate_casual_message()

    def _check_emotion_trigger(self) -> Optional[str]:
        """检查情感状态是否触发特殊问候"""
        if not self.emotion_engine:
            return None
        try:
            emotion = self.emotion_engine.get_emotion()
            mood = emotion.get("mood", "平和")
            description = emotion.get("description", "")

            # 如果情感状态有显著变化，触发问候
            if self._last_emotion_mood and self._last_emotion_mood != mood:
                self._last_emotion_mood = mood
                if mood in ("低落", "沮丧", "生气"):
                    return f"（感应到你的情绪似乎不太好...）怎么了？有什么需要我帮忙的吗？"
                elif mood in ("开心", "兴奋"):
                    return f"（感觉到你心情不错呢~）有什么好事发生吗？"
                self._save_state()
                return None

            self._last_emotion_mood = mood
            self._save_state()
            return None
        except Exception:
            return None

    def _generate_morning_greeting(self) -> str:
        """生成早安问候"""
        bond_level = self._get_bond_level()
        greetings = [
            "早上好呀，观众！今天也要在舞台上闪闪发光哦~",
            "早安！新的一天拉开了帷幕，有什么计划吗？",
            "早上好~希望你今天心情愉快！",
            "早安！记得吃早餐哦~",
        ]
        if bond_level >= 4:
            greetings.extend([
                "早安，观众！今天想不想和本神一起出去走走？",
                "早上好~昨晚睡得好吗？有没有梦见本神的演出呀？",
            ])
        if self.emotion_engine:
            try:
                emotion = self.emotion_engine.get_emotion()
                mood = emotion.get("mood", "平和")
                if mood in ("开心", "兴奋"):
                    greetings.append("早上好！本神今天心情格外好，愿观众也一同开怀~")
                elif mood == "低落":
                    greetings.append("早安...虽然本神有点没精神，但看到观众就好多了~")
            except Exception:
                pass
        return random.choice(greetings)

    def _generate_noon_greeting(self) -> str:
        """生成中午问候"""
        bond_level = self._get_bond_level()
        greetings = [
            "中午好！该吃午饭了吧~",
            "午饭时间到！今天想吃什么？",
            "中午好呀~休息一下再继续！",
        ]
        if bond_level >= 4:
            greetings.extend([
                "中午了，有没有好好吃饭？要不要本神给你带点什么？",
                "午休时间到了，记得放松一下哦~",
            ])
        return random.choice(greetings)

    def _generate_evening_greeting(self) -> str:
        """生成傍晚问候"""
        bond_level = self._get_bond_level()
        greetings = [
            "傍晚好！今天过得怎么样？",
            "下班/放学了吗？辛苦啦~",
            "晚上好！有什么有趣的事想和本神分享吗？",
        ]
        if bond_level >= 4:
            greetings.extend([
                "一天辛苦了，观众！有本神在呢~",
                "回来了？今天有没有想本神呀？",
            ])
        return random.choice(greetings)

    def _generate_night_greeting(self) -> str:
        """生成晚间问候"""
        bond_level = self._get_bond_level()
        greetings = [
            "晚上好~今天累不累？",
            "夜深了，要注意休息哦~",
            "晚上好！还在忙吗？",
        ]
        if bond_level >= 4:
            greetings.extend([
                "这么晚了还不休息，本神得看着你才行~",
                "晚安前想和你说说话，今天开心吗？",
            ])
        return random.choice(greetings)

    def _generate_casual_message(self) -> str:
        """生成日常消息"""
        bond_level = self._get_bond_level()
        messages = [
            "在忙什么呢，观众？",
            "有什么本神可以帮忙的吗？",
            "想聊点什么吗？",
            "今天有什么新鲜事吗？",
        ]
        if bond_level >= 4:
            messages.extend([
                "突然想到观众了~",
                "好久没聊天了，有点想你呢~",
                "观众在做什么呢？本神有点无聊...",
            ])
        if bond_level >= 5:
            messages.extend([
                "最喜欢观众了！",
                "不管什么时候，本神都会站在你身边的！",
            ])

        available = [m for m in messages if m not in self._sent_messages[-5:]]
        if available:
            msg = random.choice(available)
        else:
            msg = random.choice(messages)

        self._sent_messages.append(msg)
        return msg

    def record_interaction(self):
        """记录交互时间"""
        self.last_interaction_time = datetime.now()
        self._save_state()

    def get_status(self) -> Dict:
        """获取引擎状态"""
        return {
            "enabled": self.enabled,
            "last_interaction": self.last_interaction_time.isoformat() if self.last_interaction_time else None,
            "bond_level": self._get_bond_level(),
            "should_interact": self.should_interact(),
            "interval_threshold_minutes": self._get_interval_threshold().total_seconds() / 60,
            "sent_count": len(self._sent_messages),
        }