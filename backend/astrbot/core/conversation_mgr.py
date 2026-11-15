"""
AstrBot Conversation 兼容模块
"""

from __future__ import annotations

from typing import Any


class Conversation:
    """对话管理器（兼容 AstrBot）
    
    用于在插件中管理对话上下文。
    """
    def __init__(self, conversation_id: str = "", **kwargs) -> None:
        self.conversation_id = conversation_id
        self.persona_id: str = ""
        self.system_prompt: str = ""
        self.contexts: list = []
        self._data: dict = kwargs

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value


__all__ = ["Conversation"]