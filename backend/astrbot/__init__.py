"""
AstrBot 兼容层 - 将 AstrBot 插件 API 映射到现有 Star 系统
"""

import logging

logger = logging.getLogger("ZhiYanWeb.star")

# 应用兼容补丁（为 AstrMessageEvent 添加缺失方法）
from astrbot import _patch

__all__ = ["logger"]