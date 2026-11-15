"""
文本转图片服务层
提供使用API生成图片的核心业务逻辑

功能:
- 生成图片
- 获取生成历史
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime

from zhiyan_web.db.database import get_db

logger = logging.getLogger("ZhiYanWeb")

USER_ID = "default_user"  # 默认用户ID


def init_imagegen_db():
    """
    初始化图片生成数据库表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 创建图片生成历史表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS imagegen_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                image_url TEXT NOT NULL,
                local_path TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL
            )
        ''')
        
        conn.commit()
        logger.info("图片生成数据库初始化完成")
    except Exception as e:
        logger.error(f"图片生成数据库初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def generate_image(user_id: str, prompt: str):
    """
    生成图片
    
    参数:
        user_id: 用户ID
        prompt: 生成提示词
    
    返回:
        dict: 生成结果
    """
    if not prompt or not prompt.strip():
        return {"success": False, "message": "提示词不能为空"}
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        now = int(datetime.now().timestamp())
        
        # 模拟图片生成（实际应调用Liblib等API）
        # 这里使用模拟数据
        mock_image_url = f"https://picsum.photos/seed/{now}/512/512"
        status = "completed"
        
        cursor.execute(
            'INSERT INTO imagegen_history (user_id, prompt, image_url, local_path, status, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (user_id, prompt.strip(), mock_image_url, None, status, now)
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "图片生成成功！",
            "data": {
                "prompt": prompt.strip(),
                "image_url": mock_image_url,
                "status": status,
                "created_at": now,
            }
        }
    except Exception as e:
        logger.error(f"生成图片失败: {e}")
        return {"success": False, "message": "生成图片失败"}
    finally:
        conn.close()


def get_imagegen_history(user_id: str):
    """
    获取生成历史
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 生成历史列表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT * FROM imagegen_history 
            WHERE user_id = ? 
            ORDER BY created_at DESC
            LIMIT 50
        ''', (user_id,))
        rows = cursor.fetchall()
        
        history = []
        for row in rows:
            history.append({
                "id": row["id"],
                "prompt": row["prompt"],
                "image_url": row["image_url"],
                "local_path": row["local_path"],
                "status": row["status"],
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
        logger.error(f"获取生成历史失败: {e}")
        return {"success": False, "message": "获取历史失败"}
    finally:
        conn.close()


def delete_imagegen_history(user_id: str, history_id: int):
    """
    删除生成历史
    
    参数:
        user_id: 用户ID
        history_id: 历史记录ID
    
    返回:
        dict: 删除结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM imagegen_history WHERE id = ? AND user_id = ?', (history_id, user_id))
        conn.commit()
        
        if cursor.rowcount > 0:
            return {"success": True, "message": "记录已删除"}
        else:
            return {"success": False, "message": "记录不存在"}
    except Exception as e:
        logger.error(f"删除生成历史失败: {e}")
        return {"success": False, "message": "删除记录失败"}
    finally:
        conn.close()
