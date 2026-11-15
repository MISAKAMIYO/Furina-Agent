"""
记忆管理路由模块
提供记忆的增删改查功能 + 对话历史管理功能

端点:
- GET /api/memory/list                - 获取记忆列表
- POST /api/memory/search             - 搜索记忆
- POST /api/memory/add                - 添加记忆
- DELETE /api/memory/delete/{id}      - 删除记忆
- GET /api/conversations/list         - 获取对话列表
- GET /api/conversations/{id}         - 获取对话详情
- DELETE /api/conversations/{id}      - 删除对话
- POST /api/conversations/search      - 搜索对话历史
- GET /api/conversations/summary/{id} - 获取对话摘要
- POST /api/conversations/save        - 手动保存当前对话
- GET /api/profile/context            - 获取用户画像上下文
- GET /api/profile                    - 获取完整用户画像
- POST /api/profile/reset             - 重置用户画像
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zhiyan_web.db.database import get_db, execute_query, execute_insert, execute_delete
from zhiyan_web.memory.conversation_store import (
    save_conversation, load_conversation, delete_conversation,
    list_conversations, search_conversations, get_conversation_summary
)
from zhiyan_web.memory.vector_memory import get_vector_memory, MemoryEntry
from zhiyan_web.memory.user_profile import get_profile_context, get_profile, reset_profile, update_profile_from_messages

logger = logging.getLogger("ZhiYanWeb")

router = APIRouter(prefix="/api", tags=["memory"])


class AddMemoryRequest(BaseModel):
    content: str
    memory_type: str = "fact"
    layer: str = "active"
    importance: float = 0.5


class UpdateMemoryRequest(BaseModel):
    content: str = None
    memory_type: str = None
    layer: str = None
    importance: float = None


class BatchDeleteRequest(BaseModel):
    ids: list


class SearchRequest(BaseModel):
    query: str


class ImportMemoryRequest(BaseModel):
    items: list
    mode: str = "append"  # append=合并追加, replace=覆盖清空后导入


class ProfileUpdateRequest(BaseModel):
    preferences: dict = None
    personality: dict = None


class AutoMemoryConfigRequest(BaseModel):
    enabled: bool = None
    min_interval_seconds: int = None
    max_daily_writes: int = None
    summary_trigger_count: int = None
    summary_min_interval_hours: float = None


def init_memory_table():
    """初始化记忆数据库表"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                memory_type TEXT DEFAULT 'fact',
                layer TEXT DEFAULT 'active',
                importance REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        logger.info("记忆数据库表初始化完成")
    except Exception as e:
        logger.error(f"记忆数据库表初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


# ============= 记忆管理（原有） =============

# 记忆列表允许的排序方式
_MEMORY_SORT_MAP = {
    "updated_at": "updated_at DESC",
    "created_at": "created_at DESC",
    "importance": "importance DESC, updated_at DESC",
    "access_count": "access_count DESC, updated_at DESC",
    "importance_asc": "importance ASC, updated_at DESC",
}


@router.get("/memory/list")
async def list_memories(layer: str = "", memory_type: str = "",
                        sort_by: str = "updated_at", limit: int = 200, offset: int = 0):
    """获取记忆列表

    参数:
        layer:      层级筛选（identity/active/surfaced/archive）
        memory_type:类型筛选
        sort_by:    排序方式（updated_at/created_at/importance/importance_asc/access_count）
        limit:      单页条数（默认 200）
        offset:     偏移量（配合 limit 做分页）
    """
    try:
        conditions = []
        params = []

        if layer:
            conditions.append("layer = ?")
            params.append(layer)
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)

        order_by = _MEMORY_SORT_MAP.get(sort_by, "updated_at DESC")
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        limit = min(max(int(limit), 1), 500)
        offset = max(int(offset), 0)

        query = f"SELECT * FROM memories WHERE {where_clause} ORDER BY {order_by} LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)

        rows = execute_query(query, tuple(params))
        total_rows = execute_query(
            f"SELECT COUNT(*) AS cnt FROM memories WHERE {where_clause}",
            tuple(params[:-2])
        )
        total = total_rows[0].get("cnt", 0) if total_rows else 0
        return {"success": True, "data": rows, "total": total, "limit": limit, "offset": offset}
    except Exception as e:
        logger.error(f"获取记忆列表失败: {e}")
        return {"success": False, "data": [], "total": 0, "message": str(e)}


@router.post("/memory/search")
async def search_memories(request: SearchRequest):
    """搜索记忆"""
    if not request.query.strip():
        return await list_memories()

    try:
        query = "SELECT * FROM memories WHERE content LIKE ? ORDER BY importance DESC, updated_at DESC"
        params = (f"%{request.query}%",)
        rows = execute_query(query, params)
        return {"success": True, "data": rows}
    except Exception as e:
        logger.error(f"搜索记忆失败: {e}")
        return {"success": False, "data": [], "message": str(e)}


@router.post("/memory/add")
async def add_memory(request: AddMemoryRequest):
    """添加记忆"""
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="记忆内容不能为空")

    try:
        now = datetime.now().isoformat()
        query = '''INSERT INTO memories (content, memory_type, layer, importance, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)'''
        params = (request.content.strip(), request.memory_type, request.layer, request.importance, now, now)
        row_id = execute_insert(query, params)

        if row_id:
            return {"success": True, "message": "记忆添加成功", "id": row_id}
        else:
            return {"success": False, "message": "添加记忆失败"}
    except Exception as e:
        logger.error(f"添加记忆失败: {e}")
        return {"success": False, "message": f"添加记忆失败: {str(e)}"}


@router.delete("/memory/delete/{memory_id}")
async def delete_memory(memory_id: int):
    """删除记忆"""
    if memory_id <= 0:
        raise HTTPException(status_code=400, detail="无效的记忆ID")

    try:
        affected = execute_delete("DELETE FROM memories WHERE id = ?", (memory_id,))
        if affected > 0:
            return {"success": True, "message": "记忆已删除"}
        else:
            return {"success": False, "message": "记忆不存在"}
    except Exception as e:
        logger.error(f"删除记忆失败: {e}")
        return {"success": False, "message": f"删除失败: {str(e)}"}


@router.put("/memory/update/{memory_id}")
async def update_memory(memory_id: int, request: UpdateMemoryRequest):
    """更新记忆（内容/类型/层级/重要性）"""
    if memory_id <= 0:
        raise HTTPException(status_code=400, detail="无效的记忆ID")

    try:
        fields = []
        params = []
        if request.content is not None and request.content.strip():
            fields.append("content = ?")
            params.append(request.content.strip())
        if request.memory_type is not None:
            fields.append("memory_type = ?")
            params.append(request.memory_type)
        if request.layer is not None:
            fields.append("layer = ?")
            params.append(request.layer)
        if request.importance is not None:
            fields.append("importance = ?")
            params.append(min(1.0, max(0.0, float(request.importance))))

        if not fields:
            return {"success": False, "message": "没有需要更新的字段"}

        fields.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(memory_id)

        query = f"UPDATE memories SET {', '.join(fields)} WHERE id = ?"
        affected = execute_insert(query, tuple(params))
        # SQLite 的 execute 影响行数需单独获取，这里用查询判断
        rows = execute_query("SELECT id FROM memories WHERE id = ?", (memory_id,))
        if rows:
            return {"success": True, "message": "记忆已更新"}
        return {"success": False, "message": "记忆不存在"}
    except Exception as e:
        logger.error(f"更新记忆失败: {e}")
        return {"success": False, "message": f"更新失败: {str(e)}"}


@router.post("/memory/batch-delete")
async def batch_delete_memory(request: BatchDeleteRequest):
    """批量删除记忆"""
    ids = [i for i in request.ids if isinstance(i, int) and i > 0]
    if not ids:
        return {"success": False, "message": "没有有效的记忆ID"}

    try:
        placeholders = ",".join("?" * len(ids))
        query = f"DELETE FROM memories WHERE id IN ({placeholders})"
        execute_delete(query, tuple(ids))
        return {"success": True, "message": f"已删除 {len(ids)} 条记忆"}
    except Exception as e:
        logger.error(f"批量删除记忆失败: {e}")
        return {"success": False, "message": f"删除失败: {str(e)}"}


@router.post("/memory/clear")
async def clear_all_memories():
    """清空全部记忆（危险操作，需前端二次确认）"""
    try:
        rows = execute_query("SELECT COUNT(*) AS cnt FROM memories", ())
        count = rows[0].get("cnt", 0) if rows else 0
        execute_delete("DELETE FROM memories", ())
        return {"success": True, "message": f"已清空 {count} 条记忆"}
    except Exception as e:
        logger.error(f"清空记忆失败: {e}")
        return {"success": False, "message": f"清空失败: {str(e)}"}


@router.get("/memory/export")
async def export_memories():
    """导出全部记忆为 JSON（备份用）"""
    try:
        rows = execute_query("SELECT * FROM memories ORDER BY created_at ASC", ())
        return {
            "success": True,
            "data": rows,
            "exported_at": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"导出记忆失败: {e}")
        return {"success": False, "message": f"导出失败: {str(e)}"}


@router.post("/memory/import")
async def import_memories(request: ImportMemoryRequest):
    """导入记忆 JSON

    参数:
        items: 记忆项列表（content/memory_type/layer/importance/created_at/updated_at 可选）
        mode:  append=追加合并（去重 content），replace=清空后导入
    """
    if not request.items:
        return {"success": False, "message": "没有可导入的记忆"}

    try:
        now = datetime.now().isoformat()
        if request.mode == "replace":
            execute_delete("DELETE FROM memories", ())

        # 去重：追加模式下跳过已存在相同 content 的记忆
        existing = set()
        if request.mode == "append":
            for row in execute_query("SELECT content FROM memories", ()):
                existing.add((row.get("content") or "").strip())

        inserted = 0
        for item in request.items:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content or content in existing:
                continue
            memory_type = str(item.get("memory_type", "fact")) or "fact"
            layer = str(item.get("layer", "active")) or "active"
            importance = min(1.0, max(0.0, float(item.get("importance", 0.5) or 0.5)))
            created_at = str(item.get("created_at", "") or now)
            updated_at = str(item.get("updated_at", "") or created_at)

            execute_insert(
                '''INSERT INTO memories (content, memory_type, layer, importance, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (content, memory_type, layer, importance, created_at, updated_at)
            )
            existing.add(content)
            inserted += 1

        return {"success": True, "message": f"已导入 {inserted} 条记忆"}
    except Exception as e:
        logger.error(f"导入记忆失败: {e}")
        return {"success": False, "message": f"导入失败: {str(e)}"}


@router.get("/memory/stats")
async def memory_stats():
    """获取记忆统计信息"""
    try:
        rows = execute_query("SELECT * FROM memories", ())
        total = len(rows)
        by_type = {}
        by_layer = {}
        avg_importance = 0.0
        for r in rows:
            mt = r.get("memory_type", "fact")
            by_type[mt] = by_type.get(mt, 0) + 1
            l = r.get("layer", "active")
            by_layer[l] = by_layer.get(l, 0) + 1
            avg_importance += float(r.get("importance", 0))
        if total > 0:
            avg_importance = round(avg_importance / total, 2)

        # 最近更新时间
        recent = execute_query("SELECT MAX(updated_at) AS recent FROM memories", ())
        recent_time = recent[0].get("recent") if recent else None

        return {
            "success": True,
            "data": {
                "total": total,
                "by_type": by_type,
                "by_layer": by_layer,
                "avg_importance": avg_importance,
                "recent_time": recent_time,
            }
        }
    except Exception as e:
        logger.error(f"获取记忆统计失败: {e}")
        return {"success": False, "data": None, "message": str(e)}


# ============= 对话历史管理（新增） =============

@router.get("/conversations/list")
async def get_conversations():
    """获取所有保存的对话列表"""
    try:
        conversations = list_conversations()
        return {"success": True, "data": conversations}
    except Exception as e:
        logger.error(f"获取对话列表失败: {e}")
        return {"success": False, "data": [], "message": str(e)}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """获取指定对话的完整内容"""
    try:
        messages = load_conversation(conversation_id)
        return {"success": True, "data": messages}
    except Exception as e:
        logger.error(f"获取对话失败: {e}")
        return {"success": False, "data": [], "message": str(e)}


@router.delete("/conversations/{conversation_id}")
async def remove_conversation(conversation_id: str):
    """删除指定对话"""
    try:
        result = delete_conversation(conversation_id)
        return {"success": result, "message": "对话已删除" if result else "对话不存在"}
    except Exception as e:
        logger.error(f"删除对话失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/conversations/search")
async def search_conversation_history(request: SearchRequest):
    """搜索对话历史"""
    try:
        results = search_conversations(request.query)
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"搜索对话历史失败: {e}")
        return {"success": False, "data": [], "message": str(e)}


@router.get("/conversations/summary/{conversation_id}")
async def get_conversation_summary_endpoint(conversation_id: str):
    """获取对话摘要"""
    try:
        summary = get_conversation_summary(conversation_id)
        return {"success": True, "summary": summary}
    except Exception as e:
        logger.error(f"获取对话摘要失败: {e}")
        return {"success": False, "summary": "", "message": str(e)}


@router.post("/conversations/save")
async def save_current_conversation(request: dict):
    """
    手动保存当前对话

    参数:
        conversation_id: 对话 ID
        messages: 消息列表
    """
    try:
        conversation_id = request.get("conversation_id", "default")
        messages = request.get("messages", [])

        if not messages:
            return {"success": False, "message": "没有消息需要保存"}

        result = save_conversation(conversation_id, messages)

        # 保存后更新用户画像
        if result:
            update_profile_from_messages(messages)

        return {"success": result, "message": "对话已保存" if result else "保存失败"}
    except Exception as e:
        logger.error(f"保存对话失败: {e}")
        return {"success": False, "message": str(e)}


# ============= 用户画像管理（新增） =============

@router.get("/profile/context")
async def get_profile_context_endpoint():
    """获取用户画像的格式化上下文（用于 LLM 系统提示词）"""
    try:
        context = get_profile_context()
        return {"success": True, "context": context}
    except Exception as e:
        logger.error(f"获取用户画像上下文失败: {e}")
        return {"success": False, "context": "", "message": str(e)}


@router.get("/profile")
async def get_profile_endpoint():
    """获取完整用户画像"""
    try:
        profile = get_profile()
        return {"success": True, "data": profile}
    except Exception as e:
        logger.error(f"获取用户画像失败: {e}")
        return {"success": False, "data": None, "message": str(e)}


@router.post("/profile/reset")
async def reset_profile_endpoint():
    """重置用户画像"""
    try:
        reset_profile()
        return {"success": True, "message": "用户画像已重置"}
    except Exception as e:
        logger.error(f"重置用户画像失败: {e}")
        return {"success": False, "message": str(e)}


# ============= 向量记忆管理 =============

@router.post("/vector-memory/add")
async def add_vector_memory(request: AddMemoryRequest):
    """添加向量记忆"""
    try:
        vmem = get_vector_memory()
        result = vmem.add(
            content=request.content,
            memory_type=request.memory_type,
            importance=request.importance,
            source="api",
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"添加向量记忆失败: {e}")
        return {"success": False, "error": str(e)}


@router.post("/vector-memory/search")
async def search_vector_memory(request: SearchRequest):
    """语义搜索向量记忆"""
    try:
        vmem = get_vector_memory()
        results = vmem.search(request.query, top_k=10)
        return {"success": True, "data": results}
    except Exception as e:
        logger.error(f"搜索向量记忆失败: {e}")
        return {"success": False, "data": [], "error": str(e)}


@router.get("/vector-memory/types")
async def get_vector_memory_by_type(
    memory_type: str = "fact",
    limit: int = 20,
):
    """按类型获取向量记忆"""
    try:
        vmem = get_vector_memory()
        results = vmem.get_by_type(memory_type, limit)
        return {"success": True, "data": results}
    except Exception as e:
        return {"success": False, "data": [], "error": str(e)}


@router.get("/vector-memory/important")
async def get_important_vector_memory(threshold: float = 0.7, limit: int = 20):
    """获取重要记忆"""
    try:
        vmem = get_vector_memory()
        results = vmem.get_important(threshold, limit)
        return {"success": True, "data": results}
    except Exception as e:
        return {"success": False, "data": [], "error": str(e)}


@router.get("/vector-memory/recent")
async def get_recent_vector_memory(hours: int = 24, limit: int = 20):
    """获取最近记忆"""
    try:
        vmem = get_vector_memory()
        results = vmem.get_recent(hours, limit)
        return {"success": True, "data": results}
    except Exception as e:
        return {"success": False, "data": [], "error": str(e)}


@router.get("/vector-memory/stats")
async def get_vector_memory_stats():
    """获取向量记忆统计"""
    try:
        vmem = get_vector_memory()
        stats = vmem.get_stats()
        return {"success": True, "data": stats}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}


@router.delete("/vector-memory/delete/{memory_id}")
async def delete_vector_memory(memory_id: str):
    """删除向量记忆"""
    try:
        vmem = get_vector_memory()
        success = vmem.delete(memory_id)
        return {"success": success, "message": "已删除" if success else "未找到"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/vector-memory/clear")
async def clear_vector_memory():
    """清空所有向量记忆"""
    try:
        vmem = get_vector_memory()
        vmem.clear()
        return {"success": True, "message": "向量记忆已清空"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============= 自动记忆写入 =============

@router.get("/auto-memory/config")
async def get_auto_memory_config():
    """获取自动记忆配置"""
    try:
        from zhiyan_web.memory.auto_memory_writer import get_auto_memory_writer
        writer = get_auto_memory_writer()
        return {"success": True, "data": writer.get_config()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/auto-memory/config")
async def set_auto_memory_config(request: AutoMemoryConfigRequest):
    """更新自动记忆配置"""
    try:
        from zhiyan_web.memory.auto_memory_writer import get_auto_memory_writer
        writer = get_auto_memory_writer()
        result = writer.set_config(
            enabled=request.enabled,
            min_interval_seconds=request.min_interval_seconds,
            max_daily_writes=request.max_daily_writes,
            summary_trigger_count=request.summary_trigger_count,
            summary_min_interval_hours=request.summary_min_interval_hours,
        )
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/auto-memory/stats")
async def get_auto_memory_stats():
    """获取自动记忆写入统计"""
    try:
        from zhiyan_web.memory.auto_memory_writer import get_auto_memory_writer
        writer = get_auto_memory_writer()
        return {"success": True, "data": writer.get_stats()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/profile/update")
async def update_profile_endpoint(request: ProfileUpdateRequest):
    """更新用户画像（偏好/个性字段）"""
    try:
        profile = get_profile()
        if not isinstance(profile, dict):
            profile = {}
        if request.preferences is not None and isinstance(request.preferences, dict):
            profile.setdefault("preferences", {}).update(request.preferences)
        if request.personality is not None and isinstance(request.personality, dict):
            profile.setdefault("personality", {}).update(request.personality)
        # 调用 user_profile 内部的写入函数保存
        from zhiyan_web.memory import user_profile
        user_profile._write_profile(profile)
        return {"success": True, "message": "用户画像已更新", "data": get_profile()}
    except Exception as e:
        logger.error(f"更新用户画像失败: {e}")
        return {"success": False, "error": str(e)}


@router.post("/auto-memory/summarize")
async def trigger_memory_summary():
    """手动触发记忆总结"""
    try:
        from zhiyan_web.memory.auto_memory_writer import get_auto_memory_writer
        writer = get_auto_memory_writer()
        result = writer.summarize_memories()
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/auto-memory/review")
async def review_unsummarized_memories():
    """查看待总结的记忆"""
    try:
        from zhiyan_web.memory.auto_memory_writer import get_auto_memory_writer
        writer = get_auto_memory_writer()
        return writer.review_unsummarized()
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/auto-memory/summaries")
async def get_memory_summaries():
    """获取历史记忆总结列表"""
    try:
        from zhiyan_web.memory.auto_memory_writer import get_auto_memory_writer
        writer = get_auto_memory_writer()
        return {"success": True, "data": writer.get_summaries()}
    except Exception as e:
        return {"success": False, "data": [], "error": str(e)}