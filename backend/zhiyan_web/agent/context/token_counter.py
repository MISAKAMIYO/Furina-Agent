"""
Token 计数器

提供精确的 Token 计数，优先使用 tiktoken，回退到字符估算。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("ZhiYanWeb.Agent")


class TokenCounter:
    """Token 计数器"""

    # 中文 Token 估算比例
    CHINESE_CHAR_RATIO = 1.8
    # 英文 Token 估算比例
    ENGLISH_CHAR_RATIO = 4.0

    def __init__(self, encoding: str = "cl100k_base"):
        self._encoding = encoding
        self._tiktoken_available = False
        self._enc = None
        self._init_tiktoken()

    def _init_tiktoken(self) -> None:
        """尝试初始化 tiktoken"""
        try:
            import tiktoken
            self._enc = tiktoken.get_encoding(self._encoding)
            self._tiktoken_available = True
            logger.debug(f"tiktoken 已加载: {self._encoding}")
        except ImportError:
            logger.debug("tiktoken 不可用，使用字符估算")
        except Exception as e:
            logger.debug(f"tiktoken 初始化失败: {e}")

    def count_tokens(self, text: str) -> int:
        """计算文本的 Token 数量

        Args:
            text: 输入文本

        Returns:
            int: Token 数量
        """
        if not text:
            return 0

        if self._tiktoken_available and self._enc:
            try:
                return len(self._enc.encode(text))
            except Exception:
                pass

        return self._estimate_tokens(text)

    def _estimate_tokens(self, text: str) -> int:
        """基于字符的 Token 估算"""
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))
        other_chars = len(text) - chinese_chars

        tokens = chinese_chars / self.CHINESE_CHAR_RATIO + other_chars / self.ENGLISH_CHAR_RATIO
        return int(tokens) + 1

    def count_messages_tokens(self, messages: list[dict]) -> int:
        """计算消息列表的总 Token 数

        Args:
            messages: 消息列表（每项含 role 和 content）

        Returns:
            int: 总 Token 数
        """
        total = 0
        for msg in messages:
            total += self.count_tokens(msg.get("content", ""))
            total += self.count_tokens(msg.get("role", ""))
            # 消息格式开销
            total += 4  # 每条消息约 4 个 token 的格式开销
        return total + 3  # 对话格式开销