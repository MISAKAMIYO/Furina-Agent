"""
AstrBot core 兼容模块
"""

import logging

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.sp import sp

logger = logging.getLogger("ZhiYanWeb.star")

__all__ = ["AstrBotConfig", "sp", "logger"]