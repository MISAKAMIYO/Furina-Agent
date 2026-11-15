"""
漂流瓶服务层
提供漂流瓶系统的核心业务逻辑

功能:
- 扔瓶子
- 捡瓶子
- 回复瓶子
- 获取我的瓶子
"""
from __future__ import annotations

import os
import json
import uuid
import random
import logging
from datetime import datetime

from zhiyan_web.db.database import get_db

logger = logging.getLogger("ZhiYanWeb")

USER_ID = "default_user"  # 默认用户ID


def init_bottle_db():
    """
    初始化漂流瓶数据库表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 创建漂流瓶表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bottles (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                is_anonymous INTEGER NOT NULL DEFAULT 1,
                picked_up INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        ''')
        
        # 创建漂流瓶回复表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bottle_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bottle_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                is_anonymous INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL
            )
        ''')
        
        conn.commit()
        logger.info("漂流瓶数据库初始化完成")
    except Exception as e:
        logger.error(f"漂流瓶数据库初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def throw_bottle(user_id: str, content: str, is_anonymous: bool = True):
    """
    扔瓶子
    
    参数:
        user_id: 用户ID
        content: 瓶子内容
        is_anonymous: 是否匿名
    
    返回:
        dict: 扔瓶子结果
    """
    if not content or not content.strip():
        return {"success": False, "message": "内容不能为空"}
    
    if len(content) > 500:
        return {"success": False, "message": "内容不能超过500字"}
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        bottle_id = str(uuid.uuid4())
        now = int(datetime.now().timestamp())
        
        cursor.execute(
            'INSERT INTO bottles (id, user_id, content, is_anonymous, picked_up, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (bottle_id, user_id, content.strip(), 1 if is_anonymous else 0, 0, now)
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "瓶子已扔出，希望有人能捡到它",
            "data": {
                "bottle_id": bottle_id,
                "content": content.strip(),
                "is_anonymous": is_anonymous,
                "created_at": now,
            }
        }
    except Exception as e:
        logger.error(f"扔瓶子失败: {e}")
        return {"success": False, "message": "扔瓶子失败"}
    finally:
        conn.close()


def pick_bottle(user_id: str):
    """
    捡瓶子
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 捡到的瓶子信息
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 获取未被捡起的瓶子（排除自己扔的）
        cursor.execute('''
            SELECT * FROM bottles 
            WHERE picked_up = 0 AND user_id != ? 
            ORDER BY RANDOM() LIMIT 1
        ''', (user_id,))
        bottle = cursor.fetchone()
        
        if not bottle:
            return {"success": False, "message": "海里暂时没有瓶子了"}
        
        # 标记为已捡起
        cursor.execute('UPDATE bottles SET picked_up = 1 WHERE id = ?', (bottle["id"],))
        conn.commit()
        
        # 获取回复数量
        cursor.execute('SELECT COUNT(*) FROM bottle_replies WHERE bottle_id = ?', (bottle["id"],))
        reply_count = cursor.fetchone()[0]
        
        return {
            "success": True,
            "message": "捡到一个瓶子！",
            "data": {
                "bottle_id": bottle["id"],
                "content": bottle["content"],
                "is_anonymous": bool(bottle["is_anonymous"]),
                "reply_count": reply_count,
                "created_at": bottle["created_at"],
            }
        }
    except Exception as e:
        logger.error(f"捡瓶子失败: {e}")
        return {"success": False, "message": "捡瓶子失败"}
    finally:
        conn.close()


def get_bottle_replies(bottle_id: str):
    """
    获取瓶子回复
    
    参数:
        bottle_id: 瓶子ID
    
    返回:
        dict: 回复列表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 检查瓶子是否存在
        cursor.execute('SELECT * FROM bottles WHERE id = ?', (bottle_id,))
        bottle = cursor.fetchone()
        
        if not bottle:
            return {"success": False, "message": "瓶子不存在"}
        
        # 获取回复列表
        cursor.execute('''
            SELECT * FROM bottle_replies 
            WHERE bottle_id = ? 
            ORDER BY created_at ASC
        ''', (bottle_id,))
        rows = cursor.fetchall()
        
        replies = []
        for row in rows:
            replies.append({
                "id": row["id"],
                "content": row["content"],
                "is_anonymous": bool(row["is_anonymous"]),
                "created_at": row["created_at"],
            })
        
        return {
            "success": True,
            "data": {
                "bottle_id": bottle_id,
                "bottle_content": bottle["content"],
                "replies": replies,
            }
        }
    except Exception as e:
        logger.error(f"获取瓶子回复失败: {e}")
        return {"success": False, "message": "获取回复失败"}
    finally:
        conn.close()


def reply_bottle(user_id: str, bottle_id: str, content: str, is_anonymous: bool = True):
    """
    回复瓶子
    
    参数:
        user_id: 用户ID
        bottle_id: 瓶子ID
        content: 回复内容
        is_anonymous: 是否匿名
    
    返回:
        dict: 回复结果
    """
    if not content or not content.strip():
        return {"success": False, "message": "回复内容不能为空"}
    
    if len(content) > 200:
        return {"success": False, "message": "回复内容不能超过200字"}
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 检查瓶子是否存在
        cursor.execute('SELECT * FROM bottles WHERE id = ?', (bottle_id,))
        bottle = cursor.fetchone()
        
        if not bottle:
            return {"success": False, "message": "瓶子不存在"}
        
        # 添加回复
        now = int(datetime.now().timestamp())
        cursor.execute(
            'INSERT INTO bottle_replies (bottle_id, user_id, content, is_anonymous, created_at) VALUES (?, ?, ?, ?, ?)',
            (bottle_id, user_id, content.strip(), 1 if is_anonymous else 0, now)
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "回复成功！",
            "data": {
                "bottle_id": bottle_id,
                "content": content.strip(),
                "is_anonymous": is_anonymous,
                "created_at": now,
            }
        }
    except Exception as e:
        logger.error(f"回复瓶子失败: {e}")
        return {"success": False, "message": "回复失败"}
    finally:
        conn.close()


def get_my_bottles(user_id: str):
    """
    获取我的瓶子
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 瓶子列表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 获取用户扔出的瓶子
        cursor.execute('''
            SELECT b.*, COUNT(r.id) as reply_count
            FROM bottles b
            LEFT JOIN bottle_replies r ON b.id = r.bottle_id
            WHERE b.user_id = ?
            GROUP BY b.id
            ORDER BY b.created_at DESC
        ''', (user_id,))
        rows = cursor.fetchall()
        
        bottles = []
        for row in rows:
            bottles.append({
                "id": row["id"],
                "content": row["content"],
                "is_anonymous": bool(row["is_anonymous"]),
                "picked_up": bool(row["picked_up"]),
                "reply_count": row["reply_count"],
                "created_at": row["created_at"],
            })
        
        return {"success": True, "data": bottles}
    except Exception as e:
        logger.error(f"获取我的瓶子失败: {e}")
        return {"success": False, "message": "获取瓶子失败"}
    finally:
        conn.close()
