"""
好感度引擎模块
提供好感度经验值管理、等级系统、情感联动功能

功能:
- 好感度经验值管理
- 等级系统（10级）
- 每日/每周 EXP 上限
- 情感引擎联动
- 羁绊联动
- AI 主动增减好感度
"""
from __future__ import annotations

import json
import os
import time
import logging
import math
from datetime import datetime, date
from typing import Dict, List, Optional, Any

from zhiyan_web.config.settings import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.Likability")

# 数据文件路径
LIKABILITY_DATA_FILE = os.path.join(DATA_DIR, 'likability_data.json')

# 等级配置
LEVEL_CONFIG = [
    {"level": 1, "name": "陌生人", "emoji": "😐", "exp_required": 0},
    {"level": 2, "name": "熟客", "emoji": "🙂", "exp_required": 100},
    {"level": 3, "name": "朋友", "emoji": "😊", "exp_required": 300},
    {"level": 4, "name": "好友", "emoji": "😄", "exp_required": 600},
    {"level": 5, "name": "挚友", "emoji": "😆", "exp_required": 1000},
    {"level": 6, "name": "知己", "emoji": "🥰", "exp_required": 1500},
    {"level": 7, "name": "亲密", "emoji": "💕", "exp_required": 2100},
    {"level": 8, "name": "伴侣", "emoji": "❤️", "exp_required": 2800},
    {"level": 9, "name": "灵魂伴侣", "emoji": "💖", "exp_required": 3600},
    {"level": 10, "name": "永恒", "emoji": "💎", "exp_required": 5000},
]

# EXP 限制配置
DAILY_EXP_LIMIT = 50
WEEKLY_EXP_LIMIT = 300

# 等级对应的特殊福利
LEVEL_BENEFITS = {
    1: {"proactive_interval": 480, "description": "基础功能"},
    3: {"proactive_interval": 240, "description": "解锁主动问候"},
    5: {"proactive_interval": 120, "description": "解锁亲密对话"},
    7: {"proactive_interval": 60, "description": "解锁特殊互动"},
    10: {"proactive_interval": 30, "description": "解锁所有功能"},
}


class LikabilityEngine:
    """好感度引擎"""

    def __init__(self):
        self._data = self._load_data()

    def _get_default_data(self) -> dict:
        return {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "exp": 0,
            "level": 1,
            "history": [],
            "daily": {},
        }

    def _load_data(self) -> dict:
        """从 JSON 文件读取好感度数据"""
        try:
            if not os.path.exists(LIKABILITY_DATA_FILE):
                default = self._get_default_data()
                self._write_data(default)
                return default
            with open(LIKABILITY_DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return self._get_default_data()
            for key in ("exp", "level", "history", "daily"):
                if key not in data:
                    return self._get_default_data()
            return data
        except Exception as e:
            logger.error(f"读取好感度数据失败: {e}")
            return self._get_default_data()

    def _write_data(self, data: dict) -> bool:
        """写入好感度数据（原子写入）"""
        try:
            data["updated_at"] = datetime.now().isoformat()
            temp_path = LIKABILITY_DATA_FILE + '.tmp'
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, LIKABILITY_DATA_FILE)
            return True
        except Exception as e:
            logger.error(f"写入好感度数据失败: {e}")
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            return False

    def _get_level_info(self, exp: int) -> dict:
        """根据经验值获取等级信息"""
        current_level = LEVEL_CONFIG[0]
        next_level = LEVEL_CONFIG[1] if len(LEVEL_CONFIG) > 1 else LEVEL_CONFIG[0]

        for i, config in enumerate(LEVEL_CONFIG):
            if exp >= config["exp_required"]:
                current_level = config
                next_level = LEVEL_CONFIG[i + 1] if i + 1 < len(LEVEL_CONFIG) else config

        current_exp = current_level["exp_required"]
        next_exp = next_level["exp_required"] if next_level != current_level else current_exp + 100

        if next_exp > current_exp:
            progress = (exp - current_exp) / (next_exp - current_exp) * 100
        else:
            progress = 100

        return {
            "level": current_level["level"],
            "level_name": current_level["name"],
            "emoji": current_level["emoji"],
            "exp": exp,
            "next_exp": next_exp,
            "progress": min(progress, 100),
            "max_level": len(LEVEL_CONFIG),
        }

    def _get_current_level(self, exp: int) -> int:
        """根据经验值获取当前等级"""
        level = 1
        for config in LEVEL_CONFIG:
            if exp >= config["exp_required"]:
                level = config["level"]
            else:
                break
        return level

    def _check_daily_limit(self, data: dict) -> dict:
        """检查每日/每周 EXP 上限"""
        today = date.today()
        today_str = today.isoformat()
        week_start = date.fromordinal(today.toordinal() - today.weekday()).isoformat()

        daily = data.get("daily", {})
        daily_used = 0
        weekly_used = 0

        for day_str, day_data in daily.items():
            exp_gained = day_data.get("exp_gained", 0) if isinstance(day_data, dict) else day_data
            if day_str == today_str:
                daily_used = exp_gained
            if day_str >= week_start:
                weekly_used += exp_gained

        daily_remaining = max(0, DAILY_EXP_LIMIT - daily_used)
        weekly_remaining = max(0, WEEKLY_EXP_LIMIT - weekly_used)

        if daily_used >= DAILY_EXP_LIMIT:
            return {
                "allowed": False, "daily_used": daily_used, "weekly_used": weekly_used,
                "daily_limit": DAILY_EXP_LIMIT, "weekly_limit": WEEKLY_EXP_LIMIT,
                "reason": f"今日好感度经验已达上限 ({DAILY_EXP_LIMIT})"
            }
        if weekly_used >= WEEKLY_EXP_LIMIT:
            return {
                "allowed": False, "daily_used": daily_used, "weekly_used": weekly_used,
                "daily_limit": DAILY_EXP_LIMIT, "weekly_limit": WEEKLY_EXP_LIMIT,
                "reason": f"本周好感度经验已达上限 ({WEEKLY_EXP_LIMIT})"
            }

        return {
            "allowed": True, "daily_used": daily_used, "weekly_used": weekly_used,
            "daily_remaining": daily_remaining, "weekly_remaining": weekly_remaining,
            "daily_limit": DAILY_EXP_LIMIT, "weekly_limit": WEEKLY_EXP_LIMIT,
        }

    def _update_daily_limit(self, data: dict, amount: int) -> dict:
        """更新每日 EXP 使用量"""
        today = date.today().isoformat()
        daily = data.get("daily", {})
        today_data = daily.get(today, {})
        if isinstance(today_data, dict):
            today_data["exp_gained"] = today_data.get("exp_gained", 0) + amount
        else:
            today_data = {"exp_gained": amount}
        daily[today] = today_data
        data["daily"] = daily
        return data

    def _apply_emotion_influence(self, exp: int, level: int):
        """将好感度变化联动到情感引擎"""
        try:
            from zhiyan_web.emotion.engine import EmotionEngine
            engine = EmotionEngine()
            from zhiyan_web.emotion.bond import BondEngine
            bond_engine = BondEngine()
            if hasattr(engine, 'apply_bond_likability_influence'):
                bond_status = bond_engine.get_status() if bond_engine else None
                engine.apply_bond_likability_influence(
                    bond_status=bond_status,
                    likability_level=level,
                )
        except Exception as e:
            logger.debug(f"好感度联动情感引擎失败: {e}")

    def add_exp(self, amount: int, reason: str = "chat") -> dict:
        """
        增加好感度经验值

        Args:
            amount: 增加的经验值（正数增加，负数减少）
            reason: 增加原因

        Returns:
            dict: 当前好感度状态
        """
        try:
            data = self._load_data()

            # 检查每日/每周上限（仅对正向 EXP 生效）
            if amount > 0:
                limit_check = self._check_daily_limit(data)
                if not limit_check["allowed"]:
                    level_info = self._get_level_info(data["exp"])
                    return {
                        "success": True, "limited": True,
                        "message": limit_check["reason"],
                        **level_info, "exp_gained": 0, "leveled_up": False,
                    }

                max_allowed = min(
                    limit_check.get("daily_remaining", DAILY_EXP_LIMIT),
                    limit_check.get("weekly_remaining", WEEKLY_EXP_LIMIT),
                )
                amount = min(amount, max_allowed)

            if amount <= 0 and reason in ("chat",):
                level_info = self._get_level_info(data["exp"])
                return {"success": True, **level_info, "exp_gained": 0, "leveled_up": False}

            old_level = data["level"]
            new_exp = max(0, data["exp"] + amount)
            new_level = self._get_current_level(new_exp)
            leveled_up = new_level > old_level

            # 更新数据
            data["exp"] = new_exp
            data["level"] = new_level

            # 添加历史记录
            history_entry = {
                "change": amount,
                "reason": reason,
                "exp_after": new_exp,
                "level_after": new_level,
                "created_at": datetime.now().isoformat(),
            }
            data["history"].append(history_entry)

            # 限制历史记录数量
            if len(data["history"]) > 200:
                data["history"] = data["history"][-200:]

            # 更新每日限额
            if amount > 0:
                data = self._update_daily_limit(data, amount)

            # 写入文件
            self._write_data(data)

            if amount > 0:
                logger.info(f"好感度经验 +{amount} (原因: {reason})")
            else:
                logger.debug(f"好感度经验 {amount} (原因: {reason})")

            level_info = self._get_level_info(new_exp)

            # 联动情感引擎
            self._apply_emotion_influence(new_exp, new_level)

            return {
                "success": True, **level_info,
                "exp_gained": amount, "leveled_up": leveled_up, "old_level": old_level,
            }

        except Exception as e:
            logger.error(f"增加好感度经验失败: {e}")
            return {"success": False, "message": str(e)}

    def get_status(self) -> dict:
        """获取当前好感度状态"""
        data = self._load_data()
        level_info = self._get_level_info(data["exp"])
        limit_info = self._check_daily_limit(data)
        benefits = self._get_benefits(level_info["level"])
        return {
            "success": True,
            **level_info,
            "daily": limit_info,
            "benefits": benefits,
        }

    def _get_benefits(self, level: int) -> List[Dict]:
        """获取当前等级已解锁的福利"""
        unlocked = []
        for lvl, benefit in LEVEL_BENEFITS.items():
            if level >= lvl:
                unlocked.append({
                    "level": lvl,
                    "description": benefit["description"],
                    "proactive_interval": benefit["proactive_interval"],
                })
        return unlocked

    def get_history(self, limit: int = 50) -> List[Dict]:
        """获取好感度变化历史"""
        data = self._load_data()
        return list(reversed(data.get("history", [])))[:limit]

    def get_level_config(self) -> List[Dict]:
        """获取等级配置"""
        return LEVEL_CONFIG

    def get_proactive_interval(self) -> int:
        """根据好感度等级获取主动交互间隔（分钟）"""
        data = self._load_data()
        level = data["level"]
        # 从高到低查找匹配的等级福利
        for lvl in sorted(LEVEL_BENEFITS.keys(), reverse=True):
            if level >= lvl:
                return LEVEL_BENEFITS[lvl]["proactive_interval"]
        return 480

    def reset(self):
        """重置好感度"""
        self._write_data(self._get_default_data())
        logger.warning("好感度数据已重置")


# 全局单例
_likability_engine: Optional[LikabilityEngine] = None


def get_likability_engine() -> LikabilityEngine:
    global _likability_engine
    if _likability_engine is None:
        _likability_engine = LikabilityEngine()
    return _likability_engine