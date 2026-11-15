"""
AstrBot core.provider.entites 兼容模块

用于 `from astrbot.core.provider.entites import LLMResponse` 的导入方式。
"""

from astrbot.api.provider import LLMResponse, ProviderRequest

__all__ = ["LLMResponse", "ProviderRequest"]