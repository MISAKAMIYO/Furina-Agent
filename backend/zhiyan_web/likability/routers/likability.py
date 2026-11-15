"""
好感度路由模块
提供好感度状态查询、历史记录、自动增减和情感联动功能

数据存储在 JSON 文件中，路径: data/likability_data.json

端点:
- GET /api/likability/status     - 获取好感度状态
- GET /api/likability/history    - 获取好感度变化历史
- POST /api/likability/interact  - 手动互动增加好感度
- POST /api/likability/reset     - 重置好感度
"""
from __future__ import annotations

import os
import json
import time
import logging
import shutil
from datetime import datetime, date

from fastapi import APIRouter
from pydantic import BaseModel

from zhiyan_web.config.settings import DATA_DIR

logger = logging.getLogger("ZhiYanWeb")

router = APIRouter(prefix="/api/likability", tags=["likability"])

# ============= 数据文件路径 =============

LIKABILITY_DATA_FILE = os.path.join(DATA_DIR, 'likability_data.json')

# ============= 等级配置 =============

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
DAILY_EXP_LIMIT = 50           # 每日通过对话最多获得 EXP
WEEKLY_EXP_LIMIT = 300          # 每周通过对话最多获得 EXP
INTERACT_COOLDOWN = 5           # 手动互动冷却时间（秒）

# 数据文件版本
STORAGE_VERSION = 1

# 最大历史记录条数
MAX_HISTORY = 200


# ============= 数据文件读写 =============

def _get_default_data() -> dict:
    """获取默认好感度数据"""
    return {
        "version": STORAGE_VERSION,
        "updated_at": datetime.now().isoformat(),
        "exp": 0,
        "level": 1,
        "history": [],
        "daily": {},
    }


def _read_data() -> dict:
    """从 JSON 文件读取好感度数据"""
    try:
        if not os.path.exists(LIKABILITY_DATA_FILE):
            return _get_default_data()
        with open(LIKABILITY_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 确保必要字段存在
        if not isinstance(data, dict):
            return _get_default_data()
        for key in ("exp", "level", "history", "daily"):
            if key not in data:
                return _get_default_data()
        return data
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"读取好感度数据文件失败: {e}")
        # 备份损坏文件
        try:
            if os.path.exists(LIKABILITY_DATA_FILE):
                backup_path = LIKABILITY_DATA_FILE + '.corrupted.' + datetime.now().strftime('%Y%m%d_%H%M%S')
                shutil.copy2(LIKABILITY_DATA_FILE, backup_path)
                logger.info(f"已备份损坏的好感度数据文件到: {backup_path}")
        except Exception:
            pass
        return _get_default_data()


def _write_data(data: dict) -> bool:
    """将好感度数据写入 JSON 文件（原子写入）"""
    try:
        data["updated_at"] = datetime.now().isoformat()
        temp_path = LIKABILITY_DATA_FILE + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, LIKABILITY_DATA_FILE)
        return True
    except Exception as e:
        logger.error(f"写入好感度数据文件失败: {e}")
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        return False


# ============= 核心逻辑 =============

def get_likability_record() -> dict:
    """获取当前好感度记录（供外部调用）"""
    data = _read_data()
    return {
        "exp": data["exp"],
        "level": data["level"],
    }


def _get_emotion_engine():
    """延迟获取情感引擎实例"""
    try:
        from zhiyan_web.core import pipeline
        if pipeline and hasattr(pipeline, 'context') and pipeline.context:
            return pipeline.context.emotion_engine
    except Exception:
        pass
    return None


def init_likability_data():
    """初始化好感度数据文件（创建默认文件）"""
    if not os.path.exists(LIKABILITY_DATA_FILE):
        _write_data(_get_default_data())
        logger.info("好感度数据文件已初始化")
    else:
        # 验证现有文件完整性
        data = _read_data()
        if data:
            logger.info(f"好感度数据文件已存在 (exp={data['exp']}, level={data['level']})")
        else:
            _write_data(_get_default_data())
            logger.info("好感度数据文件已重新初始化")


def _get_level_info(exp: int) -> dict:
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


def _get_current_level(exp: int) -> int:
    """根据经验值获取当前等级"""
    level = 1
    for config in LEVEL_CONFIG:
        if exp >= config["exp_required"]:
            level = config["level"]
        else:
            break
    return level


def _check_daily_limit(data: dict) -> dict:
    """
    检查每日/每周 EXP 上限

    Args:
        data: 好感度数据

    Returns:
        dict: {"allowed": bool, "daily_used": int, "weekly_used": int, ...}
    """
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
            "allowed": False,
            "daily_used": daily_used,
            "weekly_used": weekly_used,
            "daily_limit": DAILY_EXP_LIMIT,
            "weekly_limit": WEEKLY_EXP_LIMIT,
            "reason": f"今日好感度经验已达上限 ({DAILY_EXP_LIMIT})"
        }
    if weekly_used >= WEEKLY_EXP_LIMIT:
        return {
            "allowed": False,
            "daily_used": daily_used,
            "weekly_used": weekly_used,
            "daily_limit": DAILY_EXP_LIMIT,
            "weekly_limit": WEEKLY_EXP_LIMIT,
            "reason": f"本周好感度经验已达上限 ({WEEKLY_EXP_LIMIT})"
        }

    return {
        "allowed": True,
        "daily_used": daily_used,
        "weekly_used": weekly_used,
        "daily_remaining": daily_remaining,
        "weekly_remaining": weekly_remaining,
        "daily_limit": DAILY_EXP_LIMIT,
        "weekly_limit": WEEKLY_EXP_LIMIT,
    }


def _update_daily_limit(data: dict, amount: int) -> dict:
    """更新每日 EXP 使用量，返回更新后的 data"""
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


def _apply_emotion_influence(exp: int, level: int):
    """将好感度变化联动到情感引擎"""
    try:
        engine = _get_emotion_engine()
        if engine and hasattr(engine, 'apply_bond_likability_influence'):
            from zhiyan_web.emotion.bond import BondEngine
            bond_engine = None
            try:
                from zhiyan_web.core import pipeline
                if pipeline and hasattr(pipeline, 'context') and pipeline.context:
                    bond_engine = pipeline.context.bond_engine
            except Exception:
                pass
            bond_status = bond_engine.get_status() if bond_engine else None
            engine.apply_bond_likability_influence(
                bond_status=bond_status,
                likability_level=level,
            )
    except Exception as e:
        logger.debug(f"好感度联动情感引擎失败: {e}")


def add_exp(amount: int, reason: str = "chat") -> dict:
    """
    增加好感度经验值（供内部调用）

    Args:
        amount: 增加的经验值（正数增加，负数减少）
        reason: 增加原因

    Returns:
        dict: 当前好感度状态
    """
    try:
        data = _read_data()

        # 检查每日/每周上限（仅对正向 EXP 生效）
        if amount > 0:
            limit_check = _check_daily_limit(data)
            if not limit_check["allowed"]:
                level_info = _get_level_info(data["exp"])
                return {
                    "success": True,
                    "limited": True,
                    "message": limit_check["reason"],
                    **level_info,
                    "exp_gained": 0,
                    "leveled_up": False,
                }

            # 限制本次增加量不超过剩余额度
            max_allowed = min(
                limit_check.get("daily_remaining", DAILY_EXP_LIMIT),
                limit_check.get("weekly_remaining", WEEKLY_EXP_LIMIT),
            )
            amount = min(amount, max_allowed)

        if amount <= 0 and reason in ("chat",):
            level_info = _get_level_info(data["exp"])
            return {
                "success": True,
                **level_info,
                "exp_gained": 0,
                "leveled_up": False,
            }

        old_level = data["level"]
        new_exp = max(0, data["exp"] + amount)
        new_level = _get_current_level(new_exp)
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
        if len(data["history"]) > MAX_HISTORY:
            data["history"] = data["history"][-MAX_HISTORY:]

        # 更新每日限额
        if amount > 0:
            data = _update_daily_limit(data, amount)

        # 写入文件
        _write_data(data)

        if amount > 0:
            logger.info(f"好感度经验 +{amount} (原因: {reason})")
        else:
            logger.debug(f"好感度经验 {amount} (原因: {reason})")

        level_info = _get_level_info(new_exp)

        # 联动情感引擎
        _apply_emotion_influence(new_exp, new_level)

        return {
            "success": True,
            **level_info,
            "exp_gained": amount,
            "leveled_up": leveled_up,
            "old_level": old_level,
        }

    except Exception as e:
        logger.error(f"增加好感度经验失败: {e}")
        return {"success": False, "message": str(e)}


class InteractRequest(BaseModel):
    type: str = "chat"


@router.get("/status")
async def get_likability_status():
    """获取好感度状态"""
    try:
        data = _read_data()
        level_info = _get_level_info(data["exp"])
        limit_info = _check_daily_limit(data)

        return {
            "success": True,
            **level_info,
            "daily": limit_info,
        }
    except Exception as e:
        logger.error(f"获取好感度状态失败: {e}")
        return {"success": False, "message": str(e)}


# 互动类型映射
INTERACT_CONFIG = {
    "chat":       {"exp": 1, "cooldown": 0,   "label": "聊天"},
    "praise":     {"exp": 3, "cooldown": 5,   "label": "称赞"},
    "compliment": {"exp": 3, "cooldown": 5,   "label": "赞美"},
    "pet":        {"exp": 2, "cooldown": 3,   "label": "摸头"},
    "play":       {"exp": 3, "cooldown": 10,  "label": "玩耍"},
    "gift":       {"exp": 5, "cooldown": 30,  "label": "送礼"},
    "date":       {"exp": 8, "cooldown": 120, "label": "约会"},
}

# 互动冷却状态
_last_interact_time = {}


@router.post("/interact")
async def interact(request: InteractRequest):
    """
    手动互动增加好感度

    支持类型: chat(聊天), praise(称赞), pet(摸头), play(玩耍), gift(送礼)
    """
    config = INTERACT_CONFIG.get(request.type)
    if not config:
        return {"success": False, "message": f"无效的互动类型: {request.type}"}

    # 冷却检查
    now = time.time()
    last_time = _last_interact_time.get(request.type, 0)
    if config["cooldown"] > 0 and (now - last_time) < config["cooldown"]:
        remaining = int(config["cooldown"] - (now - last_time))
        return {
            "success": False,
            "message": f"互动过于频繁，请 {remaining} 秒后再试",
            "cooldown_remaining": remaining,
        }

    _last_interact_time[request.type] = now
    result = add_exp(config["exp"], request.type)
    return result


@router.get("/interact/config")
async def get_interact_config():
    """获取互动类型配置"""
    return {
        "success": True,
        "types": {
            k: {
                "exp": v["exp"],
                "cooldown": v["cooldown"],
                "label": v["label"],
            }
            for k, v in INTERACT_CONFIG.items()
        }
    }


@router.get("/history")
async def get_likability_history():
    """获取好感度变化历史"""
    try:
        data = _read_data()
        # 返回最新的 50 条，按时间倒序
        history = list(reversed(data.get("history", [])))[:50]
        return {"success": True, "data": history}
    except Exception as e:
        logger.error(f"获取好感度历史失败: {e}")
        return {"success": False, "data": [], "message": str(e)}


@router.post("/reset")
async def reset_likability():
    """重置好感度（危险操作）"""
    try:
        _write_data(_get_default_data())
        logger.warning("好感度数据已重置")
        return {"success": True, "message": "好感度已重置"}
    except Exception as e:
        logger.error(f"重置好感度失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/limits")
async def get_exp_limits():
    """获取 EXP 上限配置"""
    return {
        "success": True,
        "daily_limit": DAILY_EXP_LIMIT,
        "weekly_limit": WEEKLY_EXP_LIMIT,
        "interact_cooldown": INTERACT_COOLDOWN,
    }


# 模块加载时自动初始化
init_likability_data()