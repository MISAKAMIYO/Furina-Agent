"""
盲盒系统服务层
提供盲盒抽取、收藏、历史记录的核心业务逻辑

功能:
- 开启盲盒
- 获取收藏列表
- 获取开启历史
"""
from __future__ import annotations

import os
import json
import random
import logging
from datetime import datetime

from zhiyan_web.db.database import get_db

logger = logging.getLogger("ZhiYanWeb")

USER_ID = "default_user"  # 默认用户ID

# 盲盒物品池
BLIND_BOX_POOL = [
    # 普通物品 (60%)
    {"id": 1, "name": "芙宁娜的礼帽", "rarity": "common", "description": "芙宁娜演出时佩戴的精致礼帽"},
    {"id": 2, "name": "枫丹歌剧院门票", "rarity": "common", "description": "一张前排座位的歌剧门票"},
    {"id": 3, "name": "水元素结晶", "rarity": "common", "description": "凝结着水元素力的小小晶体"},
    {"id": 4, "name": "审判庭印章", "rarity": "common", "description": "枫丹审判庭的官方印章"},
    {"id": 5, "name": "枫丹马卡龙", "rarity": "common", "description": "茶会上常见的精致甜点"},
    # 稀有物品 (30%)
    {"id": 6, "name": "芙宁娜的权杖", "rarity": "rare", "description": "象征水神身份的华丽权杖（仿制）"},
    {"id": 7, "name": "审判庭卷宗", "rarity": "rare", "description": "记载着旧日审判记录的卷宗"},
    {"id": 8, "name": "歌剧之星礼服", "rarity": "rare", "description": "舞台明星登台时穿的华服"},
    {"id": 9, "name": "歌剧院留声机", "rarity": "rare", "description": "能回放歌剧唱段的古董留声机"},
    {"id": 10, "name": "水神的冠冕", "rarity": "rare", "description": "缀满水蓝宝石的冠冕"},
    # 史诗物品 (8%)
    {"id": 11, "name": "水神之心", "rarity": "epic", "description": "流淌着纯净水元素力的心脏"},
    {"id": 12, "name": "枫丹港口沙盘", "rarity": "epic", "description": "精细还原枫丹全城的沙盘模型"},
    {"id": 13, "name": "正义之秤", "rarity": "epic", "description": "审判庭中央那座巨大天平的缩小版"},
    {"id": 14, "name": "永恒剧院剧本", "rarity": "epic", "description": "记载着无数经典剧目的孤本剧本"},
    # 传说物品 (2%)
    {"id": 15, "name": "原始胎海之水", "rarity": "legendary", "description": "传说中孕育了枫丹的水源"},
    {"id": 16, "name": "枫丹全境地图", "rarity": "legendary", "description": "标注着枫丹每一处秘密的地图"},
]

# 稀有度概率
RARITY_WEIGHTS = {
    "common": 60,
    "rare": 30,
    "epic": 8,
    "legendary": 2,
}


def init_blindbox_db():
    """
    初始化盲盒数据库表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 创建盲盒收藏表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blindbox_collection (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                item_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                rarity TEXT NOT NULL DEFAULT 'common',
                acquired_at INTEGER NOT NULL
            )
        ''')
        
        # 创建盲盒历史表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blindbox_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                item_name TEXT NOT NULL,
                rarity TEXT NOT NULL DEFAULT 'common',
                created_at INTEGER NOT NULL
            )
        ''')
        
        conn.commit()
        logger.info("盲盒系统数据库初始化完成")
    except Exception as e:
        logger.error(f"盲盒系统数据库初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def open_blindbox(user_id: str):
    """
    开启盲盒
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 开启结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 根据概率抽取物品
        item = _roll_item()
        now = int(datetime.now().timestamp())
        
        # 记录到收藏
        cursor.execute(
            'INSERT INTO blindbox_collection (user_id, item_id, item_name, rarity, acquired_at) VALUES (?, ?, ?, ?, ?)',
            (user_id, item["id"], item["name"], item["rarity"], now)
        )
        
        # 记录到历史
        cursor.execute(
            'INSERT INTO blindbox_history (user_id, item_name, rarity, created_at) VALUES (?, ?, ?, ?)',
            (user_id, item["name"], item["rarity"], now)
        )
        
        conn.commit()
        
        # 获取收藏数量统计
        cursor.execute('SELECT COUNT(*) FROM blindbox_collection WHERE user_id = ?', (user_id,))
        total_collected = cursor.fetchone()[0]
        
        rarity_names = {
            "common": "普通",
            "rare": "稀有",
            "epic": "史诗",
            "legendary": "传说",
        }
        
        return {
            "success": True,
            "message": f"恭喜获得【{rarity_names[item['rarity']]}】{item['name']}！",
            "data": {
                "item": {
                    "id": item["id"],
                    "name": item["name"],
                    "rarity": item["rarity"],
                    "rarity_name": rarity_names[item["rarity"]],
                    "description": item["description"],
                },
                "total_collected": total_collected,
                "acquired_at": now,
            }
        }
    except Exception as e:
        logger.error(f"开启盲盒失败: {e}")
        return {"success": False, "message": "开启盲盒失败"}
    finally:
        conn.close()


def _roll_item() -> dict:
    """
    根据概率抽取物品
    
    返回:
        dict: 抽到的物品
    """
    total_weight = sum(RARITY_WEIGHTS.values())
    rand = random.randint(1, total_weight)
    
    cumulative = 0
    for rarity, weight in RARITY_WEIGHTS.items():
        cumulative += weight
        if rand <= cumulative:
            # 从该稀有度的物品中随机选择一个
            items_of_rarity = [item for item in BLIND_BOX_POOL if item["rarity"] == rarity]
            return random.choice(items_of_rarity)
    
    # 默认返回普通物品
    return random.choice([item for item in BLIND_BOX_POOL if item["rarity"] == "common"])


def get_collection(user_id: str):
    """
    获取收藏列表
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 收藏列表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT * FROM blindbox_collection 
            WHERE user_id = ? 
            ORDER BY acquired_at DESC
        ''', (user_id,))
        rows = cursor.fetchall()
        
        items = []
        for row in rows:
            # 获取物品详情
            item_detail = next((item for item in BLIND_BOX_POOL if item["id"] == row["item_id"]), None)
            
            items.append({
                "id": row["id"],
                "item_id": row["item_id"],
                "name": row["item_name"],
                "rarity": row["rarity"],
                "description": item_detail["description"] if item_detail else "",
                "acquired_at": row["acquired_at"],
            })
        
        # 统计各稀有度数量
        cursor.execute('''
            SELECT rarity, COUNT(*) as count 
            FROM blindbox_collection 
            WHERE user_id = ? 
            GROUP BY rarity
        ''', (user_id,))
        stats_rows = cursor.fetchall()
        
        stats = {"common": 0, "rare": 0, "epic": 0, "legendary": 0}
        for row in stats_rows:
            stats[row["rarity"]] = row["count"]
        
        return {
            "success": True,
            "data": {
                "items": items,
                "stats": stats,
                "total": len(items),
            }
        }
    except Exception as e:
        logger.error(f"获取收藏列表失败: {e}")
        return {"success": False, "message": "获取收藏失败"}
    finally:
        conn.close()


def get_history(user_id: str):
    """
    获取开启历史
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 开启历史列表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT * FROM blindbox_history 
            WHERE user_id = ? 
            ORDER BY created_at DESC
            LIMIT 50
        ''', (user_id,))
        rows = cursor.fetchall()
        
        history = []
        for row in rows:
            history.append({
                "id": row["id"],
                "item_name": row["item_name"],
                "rarity": row["rarity"],
                "created_at": row["created_at"],
            })
        
        return {
            "success": True,
            "data": {
                "history": history,
                "total": len(history),
            }
        }
    except Exception as e:
        logger.error(f"获取开启历史失败: {e}")
        return {"success": False, "message": "获取历史失败"}
    finally:
        conn.close()
