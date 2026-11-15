"""
提供商管理器

管理 LLM 提供商的实例化、切换和会话隔离。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from zhiyan_web.core.llm.providers import LLMProvider

logger = logging.getLogger("ZhiYanWeb.Provider")


class ProviderType:
    """提供商类型"""
    CHAT_COMPLETION = "chat_completion"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"
    EMBEDDING = "embedding"


class ProviderManager:
    """提供商管理器

    管理多个 LLM 提供商实例，支持：
    - 多实例管理（按 ID 索引）
    - 当前提供商切换
    - 会话隔离（不同会话使用不同提供商）
    - 切换钩子回调
    """

    def __init__(self):
        # 提供商实例映射: provider_id → instance
        self.inst_map: dict[str, "LLMProvider"] = {}

        # 当前提供商实例
        self.curr_provider_inst: "LLMProvider" | None = None

        # 提供商变更回调
        self._provider_change_callback: Callable[[str, str, str | None], None] | None = None
        self._provider_change_hooks: list[Callable[[str, str, str | None], None]] = []

    def register_provider(self, provider_id: str, provider: "LLMProvider") -> None:
        """注册提供商

        Args:
            provider_id: 提供商唯一标识
            provider: 提供商实例
        """
        self.inst_map[provider_id] = provider
        logger.info(f"提供商已注册: {provider_id} ({provider.name})")

    def unregister_provider(self, provider_id: str) -> None:
        """注销提供商"""
        if provider_id in self.inst_map:
            del self.inst_map[provider_id]
            logger.info(f"提供商已注销: {provider_id}")

    def set_curr_provider(self, provider_id: str) -> None:
        """设置当前提供商

        Args:
            provider_id: 提供商 ID
        """
        if provider_id not in self.inst_map:
            raise ValueError(f"提供商 {provider_id} 不存在")

        self.curr_provider_inst = self.inst_map[provider_id]
        self._notify_provider_changed(provider_id, ProviderType.CHAT_COMPLETION, None)
        logger.info(f"当前提供商已切换: {provider_id}")

    def get_using_provider(
        self,
        provider_type: str = "chat_completion",
        umo: str | None = None,
    ) -> "LLMProvider" | None:
        """获取当前使用的提供商

        Args:
            provider_type: 提供商类型
            umo: 用户会话 ID（用于会话隔离）

        Returns:
            LLMProvider: 提供商实例，或 None
        """
        # 会话隔离：如果指定了会话 ID，优先使用该会话的提供商
        if umo:
            provider_id = self._get_session_provider(umo, provider_type)
            if provider_id and provider_id in self.inst_map:
                return self.inst_map[provider_id]

        # 默认使用当前提供商
        return self.curr_provider_inst

    def get_provider_by_id(self, provider_id: str) -> "LLMProvider" | None:
        """根据 ID 获取提供商"""
        return self.inst_map.get(provider_id)

    def get_all_providers(self) -> list["LLMProvider"]:
        """获取所有注册的提供商"""
        return list(self.inst_map.values())

    def set_provider_change_callback(
        self,
        callback: Callable[[str, str, str | None], None] | None,
    ) -> None:
        """设置提供商变更回调"""
        self._provider_change_callback = callback

    def register_provider_change_hook(
        self,
        hook: Callable[[str, str, str | None], None],
    ) -> None:
        """注册提供商变更钩子"""
        if hook not in self._provider_change_hooks:
            self._provider_change_hooks.append(hook)

    def _notify_provider_changed(
        self,
        provider_id: str,
        provider_type: str,
        umo: str | None,
    ) -> None:
        """通知提供商变更"""
        if self._provider_change_callback:
            try:
                self._provider_change_callback(provider_id, provider_type, umo)
            except Exception as e:
                logger.warning(f"提供商变更回调失败: {e}")

        for hook in self._provider_change_hooks:
            try:
                hook(provider_id, provider_type, umo)
            except Exception as e:
                logger.warning(f"提供商变更钩子失败: {e}")

    def _get_session_provider(self, umo: str, provider_type: str) -> str | None:
        """获取会话的提供商 ID"""
        # 简化实现：从会话存储获取
        # 完整实现需要持久化会话-提供商映射
        return None

    def count(self) -> int:
        """获取提供商数量"""
        return len(self.inst_map)