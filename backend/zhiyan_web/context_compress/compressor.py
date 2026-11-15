"""
语义压缩模型模块

将对话历史压缩为「保留关键信息、对话主题与上下文理解」的语义摘要。

支持两种压缩策略：
- LLM 语义压缩（strategy=llm）：调用配置的 LLM 生成结构化摘要，信息保留度最高
- 抽取式压缩（strategy=extractive）：基于关键信息识别算法，无 LLM 依赖、零成本、稳定

strategy=auto 时自动选择：LLM 可用则用 LLM，否则回退抽取式。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from zhiyan_web.context_compress.key_info import KeyInfoExtractor

logger = logging.getLogger("ZhiYanWeb.ContextCompress")

# LLM 压缩默认指令（可在设置中自定义）
DEFAULT_COMPRESS_INSTRUCTION = (
    "你是一位对话上下文压缩器。请将下面的对话历史压缩为一段语义摘要，用于后续对话保持连续性。\n"
    "必须保留以下信息，不要遗漏：\n"
    "1. 关键事实、用户偏好与个人信息（名字、喜好、习惯、重要事件等）\n"
    "2. 对话主题与当前话题进展\n"
    "3. 重要实体、数字、文件路径、代码引用\n"
    "4. 用户提出的问题、未解决的请求、需要继续处理的事项\n"
    "5. 说话人角色（用户/助手）各自的立场或结论\n"
    "输出格式：先用短标签分条列出「主题」「关键事实」「用户偏好」「待办/未解决问题」，"
    "最后用一段连贯的对话摘要收尾。语言简洁、信息密度高，使用中文。"
)


class SemanticCompressor:
    """语义压缩器

    Args:
        llm_instruction: LLM 压缩指令（覆盖默认值）
        max_output_chars: 压缩结果的最大字符数
    """

    def __init__(self, llm_instruction: Optional[str] = None, max_output_chars: int = 2500):
        self.llm_instruction = llm_instruction or DEFAULT_COMPRESS_INSTRUCTION
        self.max_output_chars = max_output_chars
        self.extractor = KeyInfoExtractor()

    # ---------------- 主入口 ----------------

    async def compress(
        self,
        messages: List[dict],
        target_tokens: int = 800,
        strategy: str = "auto",
        llm_instruction: Optional[str] = None,
    ) -> tuple:
        """压缩消息列表

        Args:
            messages: 消息列表（[{"role": ..., "content": ...}, ...]）
            target_tokens: 压缩结果的目标 token 数（近似）
            strategy: auto / llm / extractive
            llm_instruction: 自定义压缩指令（覆盖配置）

        Returns:
            (summary_text, key_info, used_strategy)
        """
        key_info = self.extractor.extract(messages)

        strategy = (strategy or "auto").lower()
        if strategy == "auto":
            strategy = "llm" if self._provider_available() else "extractive"

        if strategy == "llm":
            result = await self._compress_with_llm(
                messages, key_info, target_tokens, llm_instruction
            )
            if result:
                logger.info(f"上下文 LLM 语义压缩完成（{len(messages)} 条 → 摘要）")
                return result, key_info, "llm"

        # 降级 / 抽取式
        summary = key_info.get("summary_text") or self._fallback_summary(messages)
        logger.info(f"上下文抽取式压缩完成（{len(messages)} 条 → 摘要）")
        return summary, key_info, "extractive"

    # ---------------- LLM 语义压缩 ----------------

    def _provider_available(self) -> bool:
        """检查 LLM 提供商是否可用（排除 mock）"""
        try:
            from zhiyan_web.core import chat_manager
            provider = chat_manager.get_provider()
            if provider is None or not provider.is_available():
                return False
            name = getattr(provider, "name", "").lower()
            return "mock" not in str(name)
        except Exception:
            return False

    async def _compress_with_llm(
        self,
        messages: List[dict],
        key_info: Dict,
        target_tokens: int,
        llm_instruction: Optional[str],
    ) -> Optional[str]:
        """调用 LLM 生成语义摘要，失败返回 None（调用方降级）"""
        try:
            from zhiyan_web.core import chat_manager
            from zhiyan_web.core.llm.providers import LLMMessage

            provider = chat_manager.get_provider()
            if provider is None or not provider.is_available():
                return None
            name = getattr(provider, "name", "").lower()
            if "mock" in str(name):
                return None

            instruction = llm_instruction or self.llm_instruction
            history_text = self._format_history(messages)

            prompt = f"{instruction}\n\n=== 对话历史 ===\n{history_text}"

            max_tokens = max(200, min(int(target_tokens or 800), 2000))

            async def _call():
                resp = await provider.chat(
                    [LLMMessage(role="user", content=prompt)],
                    max_tokens=max_tokens,
                    temperature=0.3,
                )
                return resp

            try:
                resp = await asyncio.wait_for(_call(), timeout=60)
            except asyncio.TimeoutError:
                logger.warning("LLM 语义压缩超时，回退抽取式")
                return None

            content = (resp.content or "").strip()
            if not content or content.startswith("错误："):
                return None
            if len(content) > self.max_output_chars:
                content = content[: self.max_output_chars] + "……"
            return content
        except Exception as e:
            logger.warning(f"LLM 语义压缩失败: {e}，回退抽取式")
            return None

    def _format_history(self, messages: List[dict]) -> str:
        """将消息列表格式化为压缩输入文本"""
        lines = []
        for m in messages:
            role = m.get("role", "user")
            content = (m.get("content", "") or "").strip()
            if not content:
                continue
            if role == "tool":
                label = "工具结果"
                # 工具结果通常很长，截断避免超出输入上限
                content = content[:500]
            elif role == "system":
                label = "系统"
            elif role == "user":
                label = "用户"
            else:
                label = "助手"
            lines.append(f"{label}: {content}")
        return "\n".join(lines)

    # ---------------- 兜底摘要 ----------------

    def _fallback_summary(self, messages: List[dict]) -> str:
        """极简兜底摘要（抽取关键行）"""
        lines = []
        for m in messages[-10:]:
            role = m.get("role", "")
            content = (m.get("content", "") or "").strip()
            if not content or role in ("tool", "system"):
                continue
            label = "用户" if role == "user" else "助手"
            lines.append(f"{label}: {content[:80]}")
        return "对话要点:\n" + "\n".join("  - " + l for l in lines)
