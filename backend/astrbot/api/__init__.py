"""
AstrBot API 兼容层
"""

import logging

from astrbot.core.config.astrbot_config import AstrBotConfig

logger = logging.getLogger("ZhiYanWeb.star")

__all__ = ["logger", "AstrBotConfig"]