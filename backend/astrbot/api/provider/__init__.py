"""
AstrBot Provider 兼容模块
"""

from __future__ import annotations

from typing import Any


class ProviderRequest:
    """LLM 提供商请求对象"""
    def __init__(self) -> None:
        self.system_prompt: str = ""
        self.prompt: str = ""
        self.contexts: list = []
        self.text_chat: Any = None


class LLMResponse:
    """LLM 响应对象（兼容 AstrBot）"""
    def __init__(self) -> None:
        self.completion_text: str = ""
        self.text: str = ""
        self.role: str = "assistant"
        self.finish_reason: str = "stop"


class Personality:
    """LLM 人格设置（兼容 AstrBot）"""
    def __init__(self, **kwargs) -> None:
        self.name = kwargs.get("name", "")
        self.prompt = kwargs.get("prompt", "")
        for k, v in kwargs.items():
            setattr(self, k, v)


__all__ = ["ProviderRequest", "LLMResponse", "Personality"]