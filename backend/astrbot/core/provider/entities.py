"""
AstrBot core.provider.entities 兼容模块

用于 `from astrbot.core.provider.entities import ProviderRequest, LLMResponse` 的导入方式。
"""

from astrbot.api.provider import ProviderRequest, LLMResponse

__all__ = ["ProviderRequest", "LLMResponse"]