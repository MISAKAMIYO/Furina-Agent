"""
LLM 核心模块
提供大语言模型的抽象接口和多种提供商实现

模块结构:
- __init__.py      - 公共接口导出
- providers.py     - LLM提供商实现
- chat_manager.py  - 聊天管理器
"""
from __future__ import annotations

from .providers import LLMProvider, MockProvider, OpenAIProvider, create_provider
from .chat_manager import ChatManager, LLMMessage, LLMResponse

__all__ = [
    "LLMProvider",
    "MockProvider",
    "OpenAIProvider",
    "create_provider",
    "ChatManager",
    "LLMMessage",
    "LLMResponse",
]
