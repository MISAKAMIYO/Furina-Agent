"""
上下文管理器

管理 LLM 对话上下文的压缩和截断。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .token_counter import TokenCounter

if TYPE_CHECKING:
    from ..run_context import Message

logger = logging.getLogger("ZhiYanWeb.Agent")


@dataclass
class ContextConfig:
    """上下文配置

    Attributes:
        max_context_tokens: 最大 Token 数（<=0 不压缩）
        enforce_max_turns: 强制最大轮数（-1 不限）
        truncate_turns: 截断保留的轮数
        llm_compress: 是否使用 LLM 压缩
        llm_compress_instruction: LLM 压缩指令
        llm_compress_keep_recent: LLM 压缩保留的最近轮数
    """

    max_context_tokens: int = 4096
    enforce_max_turns: int = -1
    truncate_turns: int = 20
    llm_compress: bool = False
    llm_compress_instruction: str = "压缩以下对话历史，保留关键信息"
    llm_compress_keep_recent: int = 4


class ContextManager:
    """上下文管理器

    支持 Token 计数、截断和 LLM 压缩。
    """

    def __init__(
        self,
        config: ContextConfig | None = None,
        token_counter: TokenCounter | None = None,
        llm_provider=None,
    ):
        self.config = config or ContextConfig()
        self.token_counter = token_counter or TokenCounter()
        self.llm_provider = llm_provider

    async def process(self, messages: list[dict]) -> list[dict]:
        """处理上下文

        按配置进行压缩或截断。

        Args:
            messages: 消息列表

        Returns:
            list[dict]: 处理后的消息列表
        """
        if not messages:
            return messages

        # 1. 轮次强制限制
        if self.config.enforce_max_turns > 0:
            messages = self._enforce_turns(messages)

        # 2. Token 限制
        if self.config.max_context_tokens > 0:
            total_tokens = self.token_counter.count_messages_tokens(messages)
            if total_tokens > self.config.max_context_tokens:
                if self.config.llm_compress and self.llm_provider:
                    messages = await self._llm_compress(messages)
                else:
                    messages = self._truncate(messages)

        return messages

    def _truncate(self, messages: list[dict]) -> list[dict]:
        """截断上下文

        保留最近的 truncate_turns 轮对话。
        """
        system_prompts = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 按轮次截断（每轮 user + assistant）
        if len(non_system) > self.config.truncate_turns * 2:
            excess = len(non_system) - self.config.truncate_turns * 2
            removed = non_system[:excess]
            non_system = non_system[excess:]

            # 生成摘要
            summary_text = " ".join(
                m.get("content", "")[:50] for m in removed if m.get("content")
            )
            if summary_text:
                logger.info(
                    f"上下文已截断: 移除了 {len(removed)} 条消息, "
                    f"摘要: {summary_text[:100]}"
                )

        return system_prompts + non_system

    async def _llm_compress(self, messages: list[dict]) -> list[dict]:
        """使用 LLM 压缩上下文"""
        if not self.llm_provider:
            return self._truncate(messages)

        system_prompts = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # 保留最近 N 轮
        keep_count = self.config.llm_compress_keep_recent * 2
        to_compress = non_system[:-keep_count] if len(non_system) > keep_count else []
        keep = non_system[-keep_count:] if len(non_system) > keep_count else non_system

        if not to_compress:
            return system_prompts + keep

        # 构建压缩提示
        history_text = "\n".join(
            f"{m.get('role')}: {m.get('content', '')}"
            for m in to_compress
        )

        compress_prompt = f"{self.config.llm_compress_instruction}\n\n{history_text}"

        try:
            compressed = await self.llm_provider.chat(
                [{"role": "user", "content": compress_prompt}]
            )
            if compressed and compressed.content:
                summary = Message(
                    role="system",
                    content=f"[上下文压缩] {compressed.content}",
                )
                logger.info(f"上下文已 LLM 压缩: {len(to_compress)} → 1 条")
                return system_prompts + [summary] + keep
        except Exception as e:
            logger.warning(f"LLM 压缩失败: {e}, 回退到截断")

        return self._truncate(messages)

    def _enforce_turns(self, messages: list[dict]) -> list[dict]:
        """强制轮次限制"""
        system_prompts = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        max_messages = self.config.enforce_max_turns * 2
        if len(non_system) > max_messages:
            excess = len(non_system) - max_messages
            non_system = non_system[excess:]
            logger.info(
                f"强制轮次限制: {self.config.enforce_max_turns} 轮, "
                f"丢弃了 {excess} 条消息"
            )

        return system_prompts + non_system