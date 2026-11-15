"""
知识库引擎模块
提供知识条目的存储、检索、语义搜索和AI辅助管理功能

功能:
- 知识条目 CRUD
- 基于关键词的搜索
- 标签管理
- 自动关联（基于共同标签）
- 知识优先级管理
- AI 主动写入知识
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Any
from collections import Counter

from zhiyan_web.db.database import get_db, execute_query, execute_insert, execute_delete, execute_update

logger = logging.getLogger("ZhiYanWeb.Knowledge")


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
                source_ref TEXT DEFAULT '',
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


class KnowledgeEngine:
    """知识库引擎"""

    def __init__(self):
        init_knowledge_table()

    def add(self, content: str, tags: List[str] = None, priority: int = 5, source: str = "manual") -> Dict:
        """
        添加知识条目

        Args:
            content: 知识内容
            tags: 标签列表
            priority: 优先级 (1-10)
            source: 来源

        Returns:
            Dict: 添加结果
        """
        if not content or not content.strip():
            return {"success": False, "message": "知识内容不能为空"}

        try:
            now = datetime.now().isoformat()
            tags_json = json.dumps(tags or [], ensure_ascii=False)
            priority = max(1, min(10, priority))

            query = '''INSERT INTO knowledge (content, tags, priority, source, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)'''
            params = (content.strip(), tags_json, priority, source, now, now)
            row_id = execute_insert(query, params)

            if row_id:
                logger.info(f"知识添加成功: {content[:50]}... (来源: {source})")
                return {"success": True, "message": "知识添加成功", "id": row_id}
            else:
                return {"success": False, "message": "添加知识失败"}
        except Exception as e:
            logger.error(f"添加知识失败: {e}")
            return {"success": False, "message": f"添加知识失败: {str(e)}"}

    def search(self, query: str, tags: List[str] = None, limit: int = 20) -> List[Dict]:
        """
        搜索知识条目

        Args:
            query: 搜索关键词
            tags: 筛选标签
            limit: 返回数量

        Returns:
            List[Dict]: 匹配的知识条目
        """
        results = []
        try:
            if not query.strip():
                sql = "SELECT * FROM knowledge ORDER BY priority DESC, updated_at DESC LIMIT ?"
                params = (limit,)
            else:
                sql = "SELECT * FROM knowledge WHERE content LIKE ? ORDER BY priority DESC, updated_at DESC LIMIT ?"
                params = (f"%{query}%", limit)

            rows = execute_query(sql, params)

            for row in rows:
                try:
                    row_tags = json.loads(row.get("tags", "[]"))
                except (json.JSONDecodeError, TypeError):
                    row_tags = []

                # 如果指定了标签筛选，检查是否匹配
                if tags:
                    if not any(t in row_tags for t in tags):
                        continue

                results.append({
                    "id": row["id"],
                    "content": row["content"],
                    "tags": row_tags,
                    "priority": row["priority"],
                    "source": row.get("source", "manual"),
                    "source_ref": row.get("source_ref", ""),
                    "view_count": row.get("view_count", 0),
                    "created_at": row.get("created_at", ""),
                    "updated_at": row.get("updated_at", ""),
                    "relevance": self._calc_relevance(query, row["content"]),
                })

            return results
        except Exception as e:
            logger.error(f"搜索知识失败: {e}")
            return []

    def _calc_relevance(self, query: str, content: str) -> float:
        """计算相关度"""
        if not query:
            return 1.0
        query_lower = query.lower()
        content_lower = content.lower()
        # 关键词匹配数量
        keywords = set(re.findall(r'[\u4e00-\u9fff\w]+', query_lower))
        if not keywords:
            return 0.5
        matches = sum(1 for k in keywords if k in content_lower)
        return min(1.0, matches / len(keywords) + 0.3)

    def get_by_tags(self, tags: List[str], limit: int = 20) -> List[Dict]:
        """
        按标签获取知识

        Args:
            tags: 标签列表
            limit: 返回数量

        Returns:
            List[Dict]: 匹配的知识条目
        """
        return self.search("", tags=tags, limit=limit)

    def get_related(self, knowledge_id: int, limit: int = 5) -> List[Dict]:
        """
        获取相关知识（基于共同标签）

        Args:
            knowledge_id: 知识条目 ID
            limit: 返回数量

        Returns:
            List[Dict]: 相关知识条目
        """
        try:
            # 获取当前条目的标签
            rows = execute_query("SELECT tags FROM knowledge WHERE id = ?", (knowledge_id,))
            if not rows:
                return []
            try:
                tags = json.loads(rows[0].get("tags", "[]"))
            except (json.JSONDecodeError, TypeError):
                tags = []

            if not tags:
                return []

            # 查找有共同标签的其他条目
            related = []
            all_rows = execute_query(
                "SELECT * FROM knowledge WHERE id != ? ORDER BY priority DESC, updated_at DESC LIMIT ?",
                (knowledge_id, limit * 3),
            )
            for row in all_rows:
                try:
                    row_tags = json.loads(row.get("tags", "[]"))
                except (json.JSONDecodeError, TypeError):
                    row_tags = []
                common = set(tags) & set(row_tags)
                if common:
                    row_data = {
                        "id": row["id"],
                        "content": row["content"],
                        "tags": row_tags,
                        "priority": row["priority"],
                        "common_tags": list(common),
                        "view_count": row.get("view_count", 0),
                    }
                    related.append(row_data)

            # 按共同标签数量排序
            related.sort(key=lambda x: len(x["common_tags"]), reverse=True)
            return related[:limit]
        except Exception as e:
            logger.error(f"获取相关知识失败: {e}")
            return []

    def get_all_tags(self) -> List[Dict]:
        """获取所有标签及计数"""
        try:
            rows = execute_query("SELECT tags FROM knowledge")
            all_tags = []
            for row in rows:
                try:
                    row_tags = json.loads(row.get("tags", "[]"))
                    all_tags.extend(row_tags)
                except (json.JSONDecodeError, TypeError):
                    pass

            tag_counts = Counter(all_tags)
            return [{"name": name, "count": count} for name, count in tag_counts.most_common()]
        except Exception as e:
            logger.error(f"获取标签列表失败: {e}")
            return []

    def delete(self, knowledge_id: int) -> bool:
        """删除知识条目"""
        try:
            affected = execute_delete("DELETE FROM knowledge WHERE id = ?", (knowledge_id,))
            return affected > 0
        except Exception as e:
            logger.error(f"删除知识失败: {e}")
            return False

    def update_priority(self, knowledge_id: int, priority: int) -> bool:
        """更新优先级"""
        try:
            priority = max(1, min(10, priority))
            affected = execute_update(
                "UPDATE knowledge SET priority = ?, updated_at = ? WHERE id = ?",
                (priority, datetime.now().isoformat(), knowledge_id),
            )
            return affected > 0
        except Exception as e:
            logger.error(f"更新知识优先级失败: {e}")
            return False

    def increment_view(self, knowledge_id: int) -> bool:
        """增加查看次数"""
        try:
            affected = execute_update(
                "UPDATE knowledge SET view_count = view_count + 1 WHERE id = ?",
                (knowledge_id,),
            )
            return affected > 0
        except Exception as e:
            logger.error(f"增加查看次数失败: {e}")
            return False

    def get_stats(self) -> Dict:
        """获取知识库统计"""
        try:
            rows = execute_query("SELECT COUNT(*) as total, AVG(priority) as avg_priority FROM knowledge")
            stats = rows[0] if rows else {"total": 0, "avg_priority": 0}
            tags = self.get_all_tags()
            return {
                "total_entries": stats.get("total", 0),
                "avg_priority": round(float(stats.get("avg_priority", 0)), 1) if stats.get("avg_priority") else 0,
                "total_tags": len(tags),
                "top_tags": tags[:10],
            }
        except Exception as e:
            logger.error(f"获取知识库统计失败: {e}")
            return {"total_entries": 0, "total_tags": 0, "top_tags": []}


# 全局单例
_knowledge_engine: Optional[KnowledgeEngine] = None


def get_knowledge_engine() -> KnowledgeEngine:
    global _knowledge_engine
    if _knowledge_engine is None:
        _knowledge_engine = KnowledgeEngine()
    return _knowledge_engine