"""AstrBot EmbeddingProvider 兼容基类（加载级）。

插件（如 knowledge_base）会 `from astrbot.core.provider.provider import
EmbeddingProvider` 并继承它。这里提供一个可导入的基类，未指派真实嵌入模型时
调用 get_embedding / get_embeddings 会抛出 NotImplementedError，让依赖方向下
降级处理，而不是在导入插件时崩溃。
"""
from __future__ import annotations

import abc


class EmbeddingProvider(abc.ABC):
    """嵌入提供器抽象基类（兼容 AstrBot）。"""

    @abc.abstractmethod
    async def get_embedding(self, text: str) -> list[float]:
        """将单条文本转换为向量。"""
        raise NotImplementedError("EmbeddingProvider.get_embedding 未实现（兼容层仅加载级）")

    @abc.abstractmethod
    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """批量将文本转换为向量。"""
        raise NotImplementedError("EmbeddingProvider.get_embeddings 未实现（兼容层仅加载级）")

    def get_dim(self) -> int:
        """返回向量的维度。"""
        return 0


__all__ = ["EmbeddingProvider"]