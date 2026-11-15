"""
向量记忆模块 - 基于嵌入向量的语义搜索

功能:
- 将记忆文本转换为向量嵌入
- 基于语义相似度的记忆检索
- 自动聚类和摘要
- 无需外部 API（使用轻量级 TF-IDF + 关键词匹配作为降级方案）

数据存储: data/vector_memory/ 目录
"""
from __future__ import annotations

import json
import os
import logging
import re
import math
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from zhiyan_web.config.settings import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.Memory.VectorMemory")

VECTOR_MEMORY_DIR = os.path.join(DATA_DIR, "vector_memory")
os.makedirs(VECTOR_MEMORY_DIR, exist_ok=True)

# 最大记忆条目数
MAX_MEMORY_ENTRIES = 1000


@dataclass
class MemoryEntry:
    """记忆条目"""
    id: str
    content: str
    memory_type: str = "fact"  # fact / preference / event / concept
    importance: float = 0.5
    timestamp: str = ""
    source: str = "conversation"  # conversation / manual / system
    tags: List[str] = field(default_factory=list)
    access_count: int = 0
    last_accessed: str = ""

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type,
            "importance": self.importance,
            "timestamp": self.timestamp,
            "source": self.source,
            "tags": self.tags,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
        }


class SimpleVectorizer:
    """
    简单的向量化器 - 基于 TF-IDF 和关键词提取
    不需要外部依赖，纯 Python 实现
    """

    def __init__(self):
        self._vocab: Dict[str, int] = {}
        self._idf: Dict[str, float] = {}
        self._doc_count = 0
        self._stop_words = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
            "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
            "它", "们", "那", "这个", "那个", "什么", "怎么", "为什么",
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "shall", "can",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after",
            "and", "or", "but", "not", "so", "if", "because", "than",
            "i", "you", "he", "she", "it", "we", "they", "me", "him",
            "her", "us", "them", "my", "your", "his", "its", "our",
            "their", "this", "that", "these", "those",
        }

    def _tokenize(self, text: str) -> List[str]:
        """分词"""
        # 中文分词：按字符切分并组合常见双字词
        text = text.lower()
        # 英文分词
        en_tokens = re.findall(r'\b[a-z]+\b', text)
        # 中文分词（简单二元组）
        cn_chars = re.findall(r'[\u4e00-\u9fff]', text)
        cn_tokens = []
        for i in range(len(cn_chars) - 1):
            cn_tokens.append(cn_chars[i] + cn_chars[i + 1])

        # 过滤停用词
        tokens = [t for t in en_tokens + cn_tokens if t not in self._stop_words and len(t) > 1]
        return tokens

    def _compute_tf(self, tokens: List[str]) -> Dict[str, float]:
        """计算 TF"""
        if not tokens:
            return {}
        counter = Counter(tokens)
        total = len(tokens)
        return {word: count / total for word, count in counter.items()}

    def fit(self, documents: List[str]):
        """训练向量化器"""
        self._doc_count = len(documents)
        doc_freq = Counter()

        for doc in documents:
            tokens = set(self._tokenize(doc))
            for token in tokens:
                doc_freq[token] += 1

        # 计算 IDF
        self._vocab = {word: idx for idx, word in enumerate(doc_freq.keys())}
        self._idf = {
            word: math.log((self._doc_count + 1) / (freq + 1)) + 1
            for word, freq in doc_freq.items()
        }

    def vectorize(self, text: str) -> Dict[str, float]:
        """将文本向量化为词袋（TF-IDF 加权）"""
        tokens = self._tokenize(text)
        tf = self._compute_tf(tokens)
        vector = {}
        for word, tf_val in tf.items():
            if word in self._idf:
                vector[word] = tf_val * self._idf[word]
        return vector

    def cosine_similarity(self, vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        """计算余弦相似度"""
        if not vec1 or not vec2:
            return 0.0

        dot_product = 0
        norm1 = 0
        norm2 = 0

        for word, val in vec1.items():
            norm1 += val * val
            if word in vec2:
                dot_product += val * vec2[word]

        for val in vec2.values():
            norm2 += val * val

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (math.sqrt(norm1) * math.sqrt(norm2))


class VectorMemory:
    """向量记忆系统"""

    def __init__(self):
        self._entries: List[MemoryEntry] = []
        self._vectorizer = SimpleVectorizer()
        self._loaded = False

    def _load(self):
        """加载记忆"""
        if self._loaded:
            return
        self._loaded = True

        entries_file = os.path.join(VECTOR_MEMORY_DIR, "entries.json")
        if os.path.exists(entries_file):
            try:
                with open(entries_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    self._entries.append(MemoryEntry(**item))
                logger.info(f"已加载 {len(self._entries)} 条向量记忆")
            except Exception as e:
                logger.error(f"加载向量记忆失败: {e}")

    def _save(self):
        """保存记忆"""
        entries_file = os.path.join(VECTOR_MEMORY_DIR, "entries.json")
        data = [e.to_dict() for e in self._entries]
        try:
            with open(entries_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存向量记忆失败: {e}")

    def add(
        self,
        content: str,
        memory_type: str = "fact",
        importance: float = 0.5,
        source: str = "conversation",
        tags: List[str] = None,
    ) -> Dict:
        """添加记忆

        Args:
            content: 记忆内容
            memory_type: 记忆类型 (fact / preference / event / concept)
            importance: 重要性 (0-1)
            source: 来源
            tags: 标签

        Returns:
            Dict: 添加的记忆
        """
        self._load()
        import uuid
        entry = MemoryEntry(
            id=uuid.uuid4().hex[:12],
            content=content,
            memory_type=memory_type,
            importance=max(0.0, min(1.0, importance)),
            timestamp=datetime.now().isoformat(),
            source=source,
            tags=tags or [],
        )
        self._entries.append(entry)

        # 限制条目数
        if len(self._entries) > MAX_MEMORY_ENTRIES:
            # 移除最不重要且访问最少的
            self._entries.sort(key=lambda e: (e.importance, e.access_count))
            self._entries = self._entries[-MAX_MEMORY_ENTRIES:]

        self._save()
        return entry.to_dict()

    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        """语义搜索记忆

        Args:
            query: 搜索查询
            top_k: 返回结果数

        Returns:
            List[Dict]: 匹配的记忆列表
        """
        self._load()
        if not self._entries:
            return []

        # 训练向量化器
        documents = [e.content for e in self._entries]
        self._vectorizer.fit(documents)

        # 向量化查询
        query_vec = self._vectorizer.vectorize(query)

        # 计算相似度
        scored = []
        for entry in self._entries:
            entry_vec = self._vectorizer.vectorize(entry.content)
            similarity = self._vectorizer.cosine_similarity(query_vec, entry_vec)
            # 考虑重要性加权
            score = similarity * (0.5 + 0.5 * entry.importance)
            scored.append((score, entry))

        # 排序
        scored.sort(key=lambda x: x[0], reverse=True)

        # 更新访问计数
        results = []
        for score, entry in scored[:top_k]:
            if score > 0.01:  # 相似度阈值
                entry.access_count += 1
                entry.last_accessed = datetime.now().isoformat()
                result = entry.to_dict()
                result["similarity"] = round(score, 4)
                results.append(result)

        self._save()
        return results

    def get_by_type(self, memory_type: str, limit: int = 20) -> List[Dict]:
        """按类型获取记忆"""
        self._load()
        entries = [e for e in self._entries if e.memory_type == memory_type]
        entries.sort(key=lambda e: e.importance, reverse=True)
        return [e.to_dict() for e in entries[:limit]]

    def get_important(self, threshold: float = 0.7, limit: int = 20) -> List[Dict]:
        """获取重要记忆"""
        self._load()
        entries = [e for e in self._entries if e.importance >= threshold]
        entries.sort(key=lambda e: e.importance, reverse=True)
        return [e.to_dict() for e in entries[:limit]]

    def get_recent(self, hours: int = 24, limit: int = 20) -> List[Dict]:
        """获取最近的记忆"""
        self._load()
        cutoff = datetime.now() - timedelta(hours=hours)
        entries = [
            e for e in self._entries
            if e.timestamp and datetime.fromisoformat(e.timestamp) >= cutoff
        ]
        entries.sort(key=lambda e: e.timestamp or "", reverse=True)
        return [e.to_dict() for e in entries[:limit]]

    def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        self._load()
        for i, entry in enumerate(self._entries):
            if entry.id == memory_id:
                self._entries.pop(i)
                self._save()
                return True
        return False

    def clear(self):
        """清空所有记忆"""
        self._entries = []
        self._save()

    def get_stats(self) -> Dict:
        """获取记忆统计"""
        self._load()
        types = Counter(e.memory_type for e in self._entries)
        return {
            "total": len(self._entries),
            "by_type": dict(types),
            "avg_importance": round(
                sum(e.importance for e in self._entries) / max(len(self._entries), 1), 3
            ),
        }

    def get_context(self, query: str, max_entries: int = 5) -> str:
        """获取相关记忆的上下文文本（用于注入 LLM 提示词）"""
        results = self.search(query, top_k=max_entries)
        if not results:
            return ""

        lines = []
        for r in results:
            lines.append(f"- [{r['memory_type']}] {r['content']} (重要度: {r['importance']})")
        return "\n".join(lines)


# 全局单例
_vector_memory: Optional[VectorMemory] = None


def get_vector_memory() -> VectorMemory:
    global _vector_memory
    if _vector_memory is None:
        _vector_memory = VectorMemory()
    return _vector_memory