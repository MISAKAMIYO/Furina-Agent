"""
决策轮盘服务层
提供随机决策、预设选项的核心业务逻辑

功能:
- 转动轮盘
- 获取预设选项
- 创建自定义轮盘
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

# 默认预设选项
DEFAULT_PRESETS = {
    "food": {
        "name": "今天吃什么",
        "options": ["火锅", "烧烤", "寿司", "汉堡", "披萨", "中餐", "西餐", "日料", "韩料", "快餐"],
    },
    "movie": {
        "name": "看什么电影",
        "options": ["动作片", "喜剧片", "爱情片", "科幻片", "恐怖片", "动画片", "纪录片", "悬疑片", "战争片", "文艺片"],
    },
    "activity": {
        "name": "周末做什么",
        "options": ["逛街", "看电影", "运动", "宅家", "旅行", "聚会", "读书", "游戏", "做饭", "睡觉"],
    },
    "drink": {
        "name": "喝什么饮料",
        "options": ["可乐", "雪碧", "奶茶", "咖啡", "果汁", "茶", "牛奶", "水", "啤酒", "红酒"],
    },
}


def spin_wheel(options: list):
    """
    转动轮盘
    
    参数:
        options: 选项列表
    
    返回:
        dict: 转动结果
    """
    if not options or len(options) < 2:
        return {"success": False, "message": "选项至少需要2个"}
    
    # 随机选择一个选项
    result = random.choice(options)
    
    # 生成转动动画数据（模拟轮盘旋转）
    total_options = len(options)
    degrees_per_option = 360 / total_options
    selected_index = options.index(result)
    
    # 随机旋转圈数（3-5圈）+ 目标角度
    spins = random.randint(3, 5)
    total_degrees = spins * 360 + (360 - selected_index * degrees_per_option - degrees_per_option / 2)
    
    return {
        "success": True,
        "message": f"轮盘停止！结果是：{result}",
        "data": {
            "result": result,
            "options": options,
            "total_spins": spins,
            "total_degrees": round(total_degrees, 2),
            "selected_index": selected_index,
        }
    }


def get_preset_options(category: str = None):
    """
    获取预设选项
    
    参数:
        category: 分类名称（可选）
    
    返回:
        dict: 预设选项列表
    """
    if category:
        preset = DEFAULT_PRESETS.get(category)
        if preset:
            return {
                "success": True,
                "data": {
                    "category": category,
                    "name": preset["name"],
                    "options": preset["options"],
                }
            }
        else:
            return {"success": False, "message": "预设分类不存在"}
    
    # 返回所有预设
    presets = []
    for key, value in DEFAULT_PRESETS.items():
        presets.append({
            "category": key,
            "name": value["name"],
            "option_count": len(value["options"]),
        })
    
    return {"success": True, "data": presets}


def create_custom_wheel(user_id: str, name: str, options: list):
    """
    创建自定义轮盘
    
    参数:
        user_id: 用户ID
        name: 轮盘名称
        options: 选项列表
    
    返回:
        dict: 创建结果
    """
    if not name or not name.strip():
        return {"success": False, "message": "轮盘名称不能为空"}
    
    if not options or len(options) < 2:
        return {"success": False, "message": "选项至少需要2个"}
    
    if len(options) > 20:
        return {"success": False, "message": "选项不能超过20个"}
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 创建自定义轮盘表（如果不存在）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS custom_wheels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                options TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        ''')
        
        now = int(datetime.now().timestamp())
        
        cursor.execute(
            'INSERT INTO custom_wheels (user_id, name, options, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
            (user_id, name.strip(), json.dumps(options), now, now)
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": f"自定义轮盘「{name}」创建成功！",
            "data": {
                "wheel_id": cursor.lastrowid,
                "name": name.strip(),
                "options": options,
                "created_at": now,
            }
        }
    except Exception as e:
        logger.error(f"创建自定义轮盘失败: {e}")
        return {"success": False, "message": "创建轮盘失败"}
    finally:
        conn.close()


def get_custom_wheels(user_id: str):
    """
    获取用户自定义轮盘列表
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 自定义轮盘列表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT * FROM custom_wheels 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,))
        rows = cursor.fetchall()
        
        wheels = []
        for row in rows:
            wheels.append({
                "id": row["id"],
                "name": row["name"],
                "options": json.loads(row["options"]),
                "option_count": len(json.loads(row["options"])),
                "created_at": row["created_at"],
            })
        
        return {"success": True, "data": wheels}
    except Exception as e:
        logger.error(f"获取自定义轮盘失败: {e}")
        return {"success": False, "message": "获取轮盘失败"}
    finally:
        conn.close()


def delete_custom_wheel(user_id: str, wheel_id: int):
    """
    删除自定义轮盘
    
    参数:
        user_id: 用户ID
        wheel_id: 轮盘ID
    
    返回:
        dict: 删除结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM custom_wheels WHERE id = ? AND user_id = ?', (wheel_id, user_id))
        conn.commit()
        
        if cursor.rowcount > 0:
            return {"success": True, "message": "轮盘已删除"}
        else:
            return {"success": False, "message": "轮盘不存在"}
    except Exception as e:
        logger.error(f"删除自定义轮盘失败: {e}")
        return {"success": False, "message": "删除轮盘失败"}
    finally:
        conn.close()


def spin_custom_wheel(user_id: str, wheel_id: int):
    """
    转动自定义轮盘
    
    参数:
        user_id: 用户ID
        wheel_id: 轮盘ID
    
    返回:
        dict: 转动结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT * FROM custom_wheels WHERE id = ? AND user_id = ?', (wheel_id, user_id))
        wheel = cursor.fetchone()
        
        if not wheel:
            return {"success": False, "message": "轮盘不存在"}
        
        options = json.loads(wheel["options"])
        
        # 调用通用转动函数
        result = spin_wheel(options)
        
        if result["success"]:
            result["data"]["wheel_name"] = wheel["name"]
        
        return result
    except Exception as e:
        logger.error(f"转动自定义轮盘失败: {e}")
        return {"success": False, "message": "转动轮盘失败"}
    finally:
        conn.close()
