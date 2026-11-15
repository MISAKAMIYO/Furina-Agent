"""AstrBot FaissVecDB 兼容实现（加载级）。

用于让依赖 `astrbot.core.db.vec_db.faiss_impl.FaissVecDB` 的插件（如
knowledge_base）能够正常导入并注册命令。由于 ZhiYan 兼容层未内置 FAISS 向量库，
本实现采用轻量 JSON 文件存储文档，向量检索（retrieve）降级返回空列表；
仅保证插件可加载、命令可注册，不保证真实语义检索。

接口签名对齐 AstrBot 的 FaissVecDB：构造参数 doc_store_path / index_store_path /
embedding_provider，并提供 initialize / insert / retrieve / count_documents /
close 及 embedding_storage.save_index()。
"""
from __future__ import annotations

import json
import logging
import os
import threading

logger = logging.getLogger("ZhiYanWeb.star")


class _EmbeddingStorageLite:
    """极简的索引存储占位：save_index 无操作，避免调用方崩溃。"""

    def __init__(self, path: str) -> None:
        self.path = path

    def save_index(self) -> None:
        return None


class FaissVecDB:
    """加载兼容的 FAISS 向量库替身。

    文档以 JSON 落盘于 doc_store_path，检索降级为空列表。这样依赖方
    （知识库上传/查询）不会因类或方法缺失而 ImportError / AttributeError。
    """

    # 允许一个进程内安全读写
    _lock = threading.Lock()

    def __init__(self, doc_store_path: str, index_store_path: str = "",
                 embedding_provider=None, **kwargs) -> None:
        self.doc_store_path = doc_store_path
        self.index_store_path = index_store_path or (doc_store_path or "")[:-3] + ".index"
        self.embedding_provider = embedding_provider
        self.embedding_storage = _EmbeddingStorageLite(self.index_store_path)
        self._docs: list[dict] = []
        self._seq = 0

    async def initialize(self) -> None:
        """加载已有文档；无真实向量则忽略索引文件。"""
        if self.doc_store_path and os.path.exists(self.doc_store_path):
            try:
                with FaissVecDB._lock, open(self.doc_store_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._docs = data
                    self._seq = len(data)
            except Exception as e:  # 加载失败仅告警，不影响启动
                logger.warning("加载 Faiss 文档存储 %s 失败: %s", self.doc_store_path, e)

    async def insert(self, content: str, metadata: dict | None = None, **kwargs) -> str:
        """写入一条文档，返回 doc_id（降级实现只存文本与元数据）。"""
        self._seq += 1
        doc_id = str(self._seq)
        self._docs.append({
            "doc_id": doc_id,
            "text": content or "",
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        })
        await self._persist()
        return doc_id

    async def retrieve(self, query: str, k: int = 5, **kwargs) -> list:
        """语义检索。未内置向量库，降级返回空结果。"""
        del query, k
        return []

    async def count_documents(self) -> int:
        return len(self._docs)

    async def close(self) -> None:
        self._docs = []

    async def _persist(self) -> None:
        if not self.doc_store_path:
            return
        try:
            os.makedirs(os.path.dirname(self.doc_store_path) or ".", exist_ok=True)
            with FaissVecDB._lock, open(self.doc_store_path, "w", encoding="utf-8") as f:
                json.dump(self._docs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("写入 Faiss 文档存储失败: %s", e)


__all__ = ["FaissVecDB"]