"""
知识库路由模块
提供知识条目的增删改查功能

端点:
- GET /api/knowledge/list - 获取知识列表
- GET /api/knowledge/tags - 获取所有标签
- POST /api/knowledge/search - 搜索知识
- POST /api/knowledge/add - 添加知识
- DELETE /api/knowledge/delete/{id} - 删除知识
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from collections import Counter

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zhiyan_web.db.database import get_db, execute_query, execute_insert, execute_delete

logger = logging.getLogger("ZhiYanWeb")

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class AddKnowledgeRequest(BaseModel):
    content: str
    tags: list = []
    priority: int = 5


class SearchRequest(BaseModel):
    query: str


def init_knowledge_table():
    """初始化知识库数据库表"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                priority INTEGER DEFAULT 5,
                source TEXT DEFAULT 'manual',
                view_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        logger.info("知识库数据库表初始化完成")
    except Exception as e:
        logger.error(f"知识库数据库表初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


@router.get("/list")
async def list_knowledge():
    """获取知识列表"""
    try:
        rows = execute_query("SELECT * FROM knowledge ORDER BY priority DESC, updated_at DESC")
        data = []
        for row in rows:
            try:
                tags = json.loads(row.get("tags", "[]"))
            except (json.JSONDecodeError, TypeError):
                tags = []
            data.append({
                "id": row["id"],
                "content": row["content"],
                "tags": tags,
                "priority": row["priority"],
                "source": row.get("source", "manual"),
                "view_count": row.get("view_count", 0),
                "created_at": row.get("created_at", ""),
                "updated_at": row.get("updated_at", ""),
            })
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"获取知识列表失败: {e}")
        return {"success": False, "data": [], "message": str(e)}


@router.get("/tags")
async def get_knowledge_tags():
    """获取所有标签及其计数"""
    try:
        rows = execute_query("SELECT tags FROM knowledge")
        all_tags = []
        for row in rows:
            try:
                tags = json.loads(row.get("tags", "[]"))
                all_tags.extend(tags)
            except (json.JSONDecodeError, TypeError):
                pass
        
        tag_counts = Counter(all_tags)
        tags_data = [{"name": name, "count": count} for name, count in tag_counts.most_common()]
        return {"success": True, "data": tags_data}
    except Exception as e:
        logger.error(f"获取标签列表失败: {e}")
        return {"success": False, "data": [], "message": str(e)}


@router.post("/search")
async def search_knowledge(request: SearchRequest):
    """搜索知识"""
    if not request.query.strip():
        return await list_knowledge()
    
    try:
        query = "SELECT * FROM knowledge WHERE content LIKE ? ORDER BY priority DESC, updated_at DESC"
        rows = execute_query(query, (f"%{request.query}%",))
        data = []
        for row in rows:
            try:
                tags = json.loads(row.get("tags", "[]"))
            except (json.JSONDecodeError, TypeError):
                tags = []
            data.append({
                "id": row["id"],
                "content": row["content"],
                "tags": tags,
                "priority": row["priority"],
                "source": row.get("source", "manual"),
                "view_count": row.get("view_count", 0),
                "created_at": row.get("created_at", ""),
                "updated_at": row.get("updated_at", ""),
            })
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"搜索知识失败: {e}")
        return {"success": False, "data": [], "message": str(e)}


@router.post("/add")
async def add_knowledge(request: AddKnowledgeRequest):
    """添加知识"""
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="知识内容不能为空")
    
    try:
        now = datetime.now().isoformat()
        tags_json = json.dumps(request.tags, ensure_ascii=False)
        query = '''INSERT INTO knowledge (content, tags, priority, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)'''
        params = (request.content.strip(), tags_json, request.priority, "manual", now, now)
        row_id = execute_insert(query, params)
        
        if row_id:
            return {"success": True, "message": "知识添加成功", "id": row_id}
        else:
            return {"success": False, "message": "添加知识失败"}
    except Exception as e:
        logger.error(f"添加知识失败: {e}")
        return {"success": False, "message": f"添加知识失败: {str(e)}"}


@router.delete("/delete/{knowledge_id}")
async def delete_knowledge(knowledge_id: int):
    """删除知识"""
    if knowledge_id <= 0:
        raise HTTPException(status_code=400, detail="无效的知识ID")
    
    try:
        affected = execute_delete("DELETE FROM knowledge WHERE id = ?", (knowledge_id,))
        if affected > 0:
            return {"success": True, "message": "知识已删除"}
        else:
            return {"success": False, "message": "知识不存在"}
    except Exception as e:
        logger.error(f"删除知识失败: {e}")
        return {"success": False, "message": f"删除失败: {str(e)}"}