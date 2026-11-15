"""
经济系统服务层
提供经济系统的核心业务逻辑

功能:
- 用户经济状态管理
- 签到系统
- 打工系统
- 商店系统
- 背包系统
"""
from __future__ import annotations

import os
import json
import random
import logging
from datetime import datetime, timedelta

from zhiyan_web.db.database import get_db

logger = logging.getLogger("ZhiYanWeb")

USER_ID = "default_user"  # 默认用户ID


def init_economy_db():
    """
    初始化经济系统数据库表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 创建经济系统表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS economy (
                user_id TEXT UNIQUE NOT NULL,
                coins INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 1,
                exp INTEGER NOT NULL DEFAULT 0,
                last_checkin_date TEXT,
                last_work_date TEXT,
                work_count INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        ''')
        
        # 创建商店物品表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shop_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price INTEGER NOT NULL DEFAULT 0,
                rarity TEXT NOT NULL DEFAULT 'common',
                effect TEXT NOT NULL DEFAULT '{}',
                stock INTEGER NOT NULL DEFAULT -1,
                created_at INTEGER NOT NULL
            )
        ''')
        
        # 创建背包物品表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                acquired_at INTEGER NOT NULL
            )
        ''')
        
        # 初始化默认商店物品
        _init_shop_items(cursor)
        
        conn.commit()
        logger.info("经济系统数据库初始化完成")
    except Exception as e:
        logger.error(f"经济系统数据库初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def _init_shop_items(cursor):
    """
    初始化默认商店物品
    """
    cursor.execute('SELECT COUNT(*) FROM shop_items')
    count = cursor.fetchone()[0]
    
    if count == 0:
        items = [
            ("治愈糖果", "恢复少量好感度", 10, "common", '{"type": "likability", "value": 5}', -1),
            ("好感度药水", "恢复中等好感度", 50, "rare", '{"type": "likability", "value": 20}', -1),
            ("高级好感度药水", "恢复大量好感度", 150, "epic", '{"type": "likability", "value": 50}', -1),
            ("金币袋", "获得100金币", 80, "common", '{"type": "coins", "value": 100}', -1),
            ("金币宝箱", "获得500金币", 350, "rare", '{"type": "coins", "value": 500}', -1),
            ("神秘礼物", "随机获得物品", 200, "epic", '{"type": "random"}', -1),
            ("聊天次数卡", "增加每日互动次数", 100, "rare", '{"type": "daily_interactions", "value": 5}', -1),
        ]
        
        for item in items:
            cursor.execute(
                'INSERT INTO shop_items (name, description, price, rarity, effect, stock, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (item[0], item[1], item[2], item[3], item[4], item[5], int(datetime.now().timestamp()))
            )


def get_economy_status(user_id: str = USER_ID):
    """
    获取用户经济状态
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 经济状态信息
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM economy WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        
        if row:
            return {
                "coins": row["coins"],
                "level": row["level"],
                "exp": row["exp"],
                "exp_needed": _get_exp_needed(row["level"]),
                "last_checkin_date": row["last_checkin_date"],
                "last_work_date": row["last_work_date"],
                "work_count": row["work_count"],
                "can_checkin": _can_checkin(row["last_checkin_date"]),
                "can_work": _can_work(row["last_work_date"]),
            }
        
        # 创建新用户经济记录
        return _create_new_user(user_id)
    except Exception as e:
        logger.error(f"获取经济状态失败: {e}")
        return {"coins": 0, "level": 1, "exp": 0, "exp_needed": 100}
    finally:
        conn.close()


def _create_new_user(user_id: str):
    """
    创建新用户经济记录
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 初始经济状态
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        now = int(datetime.now().timestamp())
        cursor.execute(
            'INSERT INTO economy (user_id, coins, level, exp, last_checkin_date, last_work_date, work_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (user_id, 100, 1, 0, "", "", 0, now, now)
        )
        conn.commit()
        
        return {
            "coins": 100,
            "level": 1,
            "exp": 0,
            "exp_needed": 100,
            "last_checkin_date": "",
            "last_work_date": "",
            "work_count": 0,
            "can_checkin": True,
            "can_work": True,
        }
    except Exception as e:
        logger.error(f"创建新用户经济记录失败: {e}")
        return {"coins": 0, "level": 1, "exp": 0, "exp_needed": 100}
    finally:
        conn.close()


def _get_exp_needed(level: int) -> int:
    """
    计算升级所需经验值
    
    参数:
        level: 当前等级
    
    返回:
        int: 升级所需经验值
    """
    return level * 100


def _can_checkin(last_checkin_date: str) -> bool:
    """
    判断是否可以签到
    
    参数:
        last_checkin_date: 上次签到日期
    
    返回:
        bool: 是否可以签到
    """
    if not last_checkin_date:
        return True
    
    today = datetime.now().strftime("%Y-%m-%d")
    return last_checkin_date != today


def _can_work(last_work_date: str) -> bool:
    """
    判断是否可以打工
    
    参数:
        last_work_date: 上次打工日期
    
    返回:
        bool: 是否可以打工
    """
    if not last_work_date:
        return True
    
    today = datetime.now().strftime("%Y-%m-%d")
    return last_work_date != today


def checkin(user_id: str = USER_ID):
    """
    每日签到
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 签到结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM economy WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        
        if not row:
            _create_new_user(user_id)
            row = cursor.execute('SELECT * FROM economy WHERE user_id = ?', (user_id,)).fetchone()
        
        if not _can_checkin(row["last_checkin_date"]):
            return {"success": False, "message": "今天已经签到过了"}
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        # 签到奖励：10-50金币，5-20经验
        coins_reward = random.randint(10, 50)
        exp_reward = random.randint(5, 20)
        
        new_coins = row["coins"] + coins_reward
        new_exp = row["exp"] + exp_reward
        new_level = row["level"]
        
        # 检查升级
        exp_needed = _get_exp_needed(new_level)
        while new_exp >= exp_needed:
            new_exp -= exp_needed
            new_level += 1
            exp_needed = _get_exp_needed(new_level)
        
        cursor.execute(
            'UPDATE economy SET coins = ?, level = ?, exp = ?, last_checkin_date = ?, updated_at = ? WHERE user_id = ?',
            (new_coins, new_level, new_exp, today, int(datetime.now().timestamp()), user_id)
        )
        conn.commit()
        
        return {
            "success": True,
            "message": f"签到成功！获得 {coins_reward} 金币和 {exp_reward} 经验",
            "data": {
                "coins_reward": coins_reward,
                "exp_reward": exp_reward,
                "new_coins": new_coins,
                "new_level": new_level,
                "new_exp": new_exp,
            }
        }
    except Exception as e:
        logger.error(f"签到失败: {e}")
        return {"success": False, "message": "签到失败"}
    finally:
        conn.close()


def work(user_id: str = USER_ID):
    """
    打工赚取金币
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 打工结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM economy WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        
        if not row:
            _create_new_user(user_id)
            row = cursor.execute('SELECT * FROM economy WHERE user_id = ?', (user_id,)).fetchone()
        
        if not _can_work(row["last_work_date"]):
            return {"success": False, "message": "今天已经打过工了"}
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        # 打工奖励：根据等级计算，20-100金币，10-30经验
        level_multiplier = row["level"] * 0.5
        coins_reward = random.randint(20, 50) + int(level_multiplier * 10)
        exp_reward = random.randint(10, 20) + int(level_multiplier * 5)
        
        new_coins = row["coins"] + coins_reward
        new_exp = row["exp"] + exp_reward
        new_level = row["level"]
        new_work_count = row["work_count"] + 1
        
        # 检查升级
        exp_needed = _get_exp_needed(new_level)
        while new_exp >= exp_needed:
            new_exp -= exp_needed
            new_level += 1
            exp_needed = _get_exp_needed(new_level)
        
        cursor.execute(
            'UPDATE economy SET coins = ?, level = ?, exp = ?, last_work_date = ?, work_count = ?, updated_at = ? WHERE user_id = ?',
            (new_coins, new_level, new_exp, today, new_work_count, int(datetime.now().timestamp()), user_id)
        )
        conn.commit()
        
        return {
            "success": True,
            "message": f"打工完成！获得 {coins_reward} 金币和 {exp_reward} 经验",
            "data": {
                "coins_reward": coins_reward,
                "exp_reward": exp_reward,
                "new_coins": new_coins,
                "new_level": new_level,
                "new_exp": new_exp,
                "total_work_count": new_work_count,
            }
        }
    except Exception as e:
        logger.error(f"打工失败: {e}")
        return {"success": False, "message": "打工失败"}
    finally:
        conn.close()


def get_shop_items():
    """
    获取商店物品列表
    
    返回:
        list: 物品列表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM shop_items ORDER BY rarity DESC, price ASC')
        rows = cursor.fetchall()
        
        items = []
        for row in rows:
            items.append({
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "price": row["price"],
                "rarity": row["rarity"],
                "effect": json.loads(row["effect"]),
                "stock": row["stock"],
            })
        
        return {"success": True, "data": items}
    except Exception as e:
        logger.error(f"获取商店物品失败: {e}")
        return {"success": False, "message": "获取商店物品失败"}
    finally:
        conn.close()


def buy_item(user_id: str, item_id: int):
    """
    购买物品
    
    参数:
        user_id: 用户ID
        item_id: 物品ID
    
    返回:
        dict: 购买结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 获取物品信息
        cursor.execute('SELECT * FROM shop_items WHERE id = ?', (item_id,))
        item = cursor.fetchone()
        
        if not item:
            return {"success": False, "message": "物品不存在"}
        
        # 检查库存
        if item["stock"] == 0:
            return {"success": False, "message": "物品已售罄"}
        
        # 获取用户经济状态
        cursor.execute('SELECT * FROM economy WHERE user_id = ?', (user_id,))
        user = cursor.fetchone()
        
        if not user:
            _create_new_user(user_id)
            user = cursor.execute('SELECT * FROM economy WHERE user_id = ?', (user_id,)).fetchone()
        
        # 检查金币
        if user["coins"] < item["price"]:
            return {"success": False, "message": "金币不足"}
        
        # 扣除金币
        new_coins = user["coins"] - item["price"]
        cursor.execute(
            'UPDATE economy SET coins = ?, updated_at = ? WHERE user_id = ?',
            (new_coins, int(datetime.now().timestamp()), user_id)
        )
        
        # 扣减库存（如果不是无限库存）
        if item["stock"] > 0:
            cursor.execute(
                'UPDATE shop_items SET stock = stock - 1 WHERE id = ?',
                (item_id,)
            )
        
        # 添加到背包
        cursor.execute('SELECT * FROM inventory WHERE user_id = ? AND item_id = ?', (user_id, item_id))
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute(
                'UPDATE inventory SET quantity = quantity + 1 WHERE id = ?',
                (existing["id"],)
            )
        else:
            cursor.execute(
                'INSERT INTO inventory (user_id, item_id, quantity, acquired_at) VALUES (?, ?, ?, ?)',
                (user_id, item_id, 1, int(datetime.now().timestamp()))
            )
        
        conn.commit()
        
        return {
            "success": True,
            "message": f"购买成功！获得 {item['name']}",
            "data": {
                "item": {
                    "id": item["id"],
                    "name": item["name"],
                    "description": item["description"],
                },
                "remaining_coins": new_coins,
            }
        }
    except Exception as e:
        logger.error(f"购买物品失败: {e}")
        return {"success": False, "message": "购买失败"}
    finally:
        conn.close()


def get_inventory(user_id: str = USER_ID):
    """
    获取背包物品
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 背包物品列表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT i.*, s.name, s.description, s.effect, s.rarity
            FROM inventory i
            JOIN shop_items s ON i.item_id = s.id
            WHERE i.user_id = ?
            ORDER BY s.rarity DESC, i.acquired_at DESC
        ''', (user_id,))
        rows = cursor.fetchall()
        
        items = []
        for row in rows:
            items.append({
                "id": row["id"],
                "item_id": row["item_id"],
                "name": row["name"],
                "description": row["description"],
                "effect": json.loads(row["effect"]),
                "rarity": row["rarity"],
                "quantity": row["quantity"],
                "acquired_at": row["acquired_at"],
            })
        
        return {"success": True, "data": items}
    except Exception as e:
        logger.error(f"获取背包失败: {e}")
        return {"success": False, "message": "获取背包失败"}
    finally:
        conn.close()


def use_item(user_id: str, item_id: int):
    """
    使用物品
    
    参数:
        user_id: 用户ID
        item_id: 物品ID
    
    返回:
        dict: 使用结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 获取物品信息
        cursor.execute('''
            SELECT i.*, s.name, s.description, s.effect
            FROM inventory i
            JOIN shop_items s ON i.item_id = s.id
            WHERE i.user_id = ? AND i.id = ?
        ''', (user_id, item_id))
        inventory_item = cursor.fetchone()
        
        if not inventory_item:
            return {"success": False, "message": "背包中没有该物品"}
        
        if inventory_item["quantity"] <= 0:
            return {"success": False, "message": "物品数量不足"}
        
        effect = json.loads(inventory_item["effect"])
        effect_type = effect.get("type", "")
        
        # 应用效果
        results = []
        
        if effect_type == "coins":
            # 增加金币
            coins_added = effect.get("value", 0)
            cursor.execute(
                'UPDATE economy SET coins = coins + ?, updated_at = ? WHERE user_id = ?',
                (coins_added, int(datetime.now().timestamp()), user_id)
            )
            results.append(f"获得 {coins_added} 金币")
        
        elif effect_type == "likability":
            # 增加好感度
            likability_added = effect.get("value", 0)
            # 这里需要调用好感度系统
            results.append(f"好感度增加 {likability_added}")
        
        elif effect_type == "daily_interactions":
            # 增加每日互动次数
            interactions_added = effect.get("value", 0)
            results.append(f"每日互动次数增加 {interactions_added}")
        
        elif effect_type == "random":
            # 随机奖励
            rand_type = random.choice(["coins", "likability"])
            rand_value = random.randint(50, 200) if rand_type == "coins" else random.randint(10, 30)
            
            if rand_type == "coins":
                cursor.execute(
                    'UPDATE economy SET coins = coins + ?, updated_at = ? WHERE user_id = ?',
                    (rand_value, int(datetime.now().timestamp()), user_id)
                )
                results.append(f"获得 {rand_value} 金币")
            else:
                results.append(f"好感度增加 {rand_value}")
        
        # 减少物品数量
        new_quantity = inventory_item["quantity"] - 1
        if new_quantity <= 0:
            cursor.execute('DELETE FROM inventory WHERE id = ?', (item_id,))
        else:
            cursor.execute('UPDATE inventory SET quantity = ? WHERE id = ?', (new_quantity, item_id))
        
        conn.commit()
        
        return {
            "success": True,
            "message": f"使用 {inventory_item['name']} 成功！" + (" ".join(results) if results else ""),
            "data": {
                "item_name": inventory_item["name"],
                "effects": results,
            }
        }
    except Exception as e:
        logger.error(f"使用物品失败: {e}")
        return {"success": False, "message": "使用失败"}
    finally:
        conn.close()
