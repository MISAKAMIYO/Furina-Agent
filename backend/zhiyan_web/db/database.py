"""
数据库操作模块
提供 SQLite 数据库的连接和基础操作

核心功能:
- 获取数据库连接
- 初始化数据库表
- 基础数据库操作
"""
from __future__ import annotations

import os
import sqlite3
import logging
from typing import Optional

from zhiyan_web.config.settings import DATA_DIR

logger = logging.getLogger("ZhiYanWeb")


def get_db():
    """
    获取数据库连接
    
    返回:
        sqlite3.Connection: 数据库连接对象
    """
    db_path = os.path.join(DATA_DIR, 'zhiyan.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    初始化数据库表
    
    创建必要的数据库表（如果不存在）
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 创建会话表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建设置表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def execute_query(query: str, params: tuple = None, fetch_one: bool = False):
    """
    执行数据库查询
    
    参数:
        query: SQL 查询语句
        params: 查询参数
        fetch_one: 是否只返回一条记录
    
    返回:
        list or dict: 查询结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        conn.commit()
        
        if fetch_one:
            result = cursor.fetchone()
            return dict(result) if result else None
        else:
            results = cursor.fetchall()
            return [dict(row) for row in results]
    except Exception as e:
        logger.error(f"数据库查询失败: {query}, 错误: {e}")
        return []
    finally:
        conn.close()


def execute_insert(query: str, params: tuple = None) -> Optional[int]:
    """
    执行数据库插入操作
    
    参数:
        query: SQL 插入语句
        params: 插入参数
    
    返回:
        Optional[int]: 插入的记录 ID
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error(f"数据库插入失败: {query}, 错误: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def execute_update(query: str, params: tuple = None) -> int:
    """
    执行数据库更新操作
    
    参数:
        query: SQL 更新语句
        params: 更新参数
    
    返回:
        int: 受影响的行数
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        conn.commit()
        return cursor.rowcount
    except Exception as e:
        logger.error(f"数据库更新失败: {query}, 错误: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()


def execute_delete(query: str, params: tuple = None) -> int:
    """
    执行数据库删除操作
    
    参数:
        query: SQL 删除语句
        params: 删除参数
    
    返回:
        int: 受影响的行数
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        conn.commit()
        return cursor.rowcount
    except Exception as e:
        logger.error(f"数据库删除失败: {query}, 错误: {e}")
        conn.rollback()
        return 0
    finally:
        conn.close()
