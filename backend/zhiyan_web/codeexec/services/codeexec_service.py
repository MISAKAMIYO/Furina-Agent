"""
代码执行器服务层
提供沙箱Python代码执行的核心业务逻辑

功能:
- 执行代码
- 获取执行历史
"""
from __future__ import annotations

import os
import json
import logging
import io
import sys
import traceback
from datetime import datetime

from zhiyan_web.db.database import get_db

logger = logging.getLogger("ZhiYanWeb")

USER_ID = "default_user"  # 默认用户ID

# 安全限制
MAX_EXECUTION_TIME = 10  # 最大执行时间（秒）
MAX_OUTPUT_SIZE = 10240  # 最大输出大小（字节）
FORBIDDEN_MODULES = [
    "os",
    "sys",
    "subprocess",
    "importlib",
    "shutil",
    "glob",
    "socket",
    "http",
    "urllib",
    "requests",
    "threading",
    "multiprocessing",
    "pickle",
    "marshal",
    "ctypes",
    "winreg",
    "fcntl",
]


def init_codeexec_db():
    """
    初始化代码执行数据库表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 创建代码执行历史表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS code_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                code TEXT NOT NULL,
                output TEXT,
                error TEXT,
                created_at INTEGER NOT NULL
            )
        ''')
        
        conn.commit()
        logger.info("代码执行数据库初始化完成")
    except Exception as e:
        logger.error(f"代码执行数据库初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def execute_code(user_id: str, code: str):
    """
    执行Python代码
    
    参数:
        user_id: 用户ID
        code: 要执行的代码
    
    返回:
        dict: 执行结果
    """
    if not code or not code.strip():
        return {"success": False, "message": "代码不能为空"}
    
    # 安全检查
    for module in FORBIDDEN_MODULES:
        if module in code:
            return {"success": False, "message": f"禁止使用模块: {module}"}
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        now = int(datetime.now().timestamp())
        
        # 捕获输出
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        
        sys.stdout = stdout_buffer
        sys.stderr = stderr_buffer
        
        output = ""
        error = None
        
        try:
            # 使用exec执行代码
            exec(code, {})
        except Exception as e:
            error = traceback.format_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            output = stdout_buffer.getvalue()
            if stderr_buffer.getvalue():
                if error:
                    error += "\n" + stderr_buffer.getvalue()
                else:
                    error = stderr_buffer.getvalue()
        
        # 限制输出大小
        if len(output) > MAX_OUTPUT_SIZE:
            output = output[:MAX_OUTPUT_SIZE] + "\n...（输出已截断）"
        
        if error and len(error) > MAX_OUTPUT_SIZE:
            error = error[:MAX_OUTPUT_SIZE] + "\n...（错误信息已截断）"
        
        # 记录到历史
        cursor.execute(
            'INSERT INTO code_history (user_id, code, output, error, created_at) VALUES (?, ?, ?, ?, ?)',
            (user_id, code.strip(), output, error, now)
        )
        
        conn.commit()
        
        if error:
            return {
                "success": False,
                "message": "代码执行出错",
                "data": {
                    "code": code.strip(),
                    "output": output,
                    "error": error,
                    "created_at": now,
                }
            }
        else:
            return {
                "success": True,
                "message": "代码执行成功！",
                "data": {
                    "code": code.strip(),
                    "output": output,
                    "error": None,
                    "created_at": now,
                }
            }
    except Exception as e:
        logger.error(f"执行代码失败: {e}")
        return {"success": False, "message": "执行代码失败"}
    finally:
        conn.close()


def get_code_history(user_id: str):
    """
    获取执行历史
    
    参数:
        user_id: 用户ID
    
    返回:
        dict: 执行历史列表
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT * FROM code_history 
            WHERE user_id = ? 
            ORDER BY created_at DESC
            LIMIT 20
        ''', (user_id,))
        rows = cursor.fetchall()
        
        history = []
        for row in rows:
            history.append({
                "id": row["id"],
                "code": row["code"],
                "output": row["output"],
                "error": row["error"],
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
        logger.error(f"获取执行历史失败: {e}")
        return {"success": False, "message": "获取历史失败"}
    finally:
        conn.close()


def delete_code_history(user_id: str, history_id: int):
    """
    删除执行历史
    
    参数:
        user_id: 用户ID
        history_id: 历史记录ID
    
    返回:
        dict: 删除结果
    """
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('DELETE FROM code_history WHERE id = ? AND user_id = ?', (history_id, user_id))
        conn.commit()
        
        if cursor.rowcount > 0:
            return {"success": True, "message": "记录已删除"}
        else:
            return {"success": False, "message": "记录不存在"}
    except Exception as e:
        logger.error(f"删除执行历史失败: {e}")
        return {"success": False, "message": "删除记录失败"}
    finally:
        conn.close()
