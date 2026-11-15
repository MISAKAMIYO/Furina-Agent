"""
插件管理器模块 - 兼容层
PluginManager 提供向后兼容，所有功能委托给 star 的 PluginManager
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
from types import ModuleType

from zhiyan_web.star import PluginManager as StarPluginManager
from zhiyan_web.star import Context
from zhiyan_web.star.star import star_map, star_registry
from zhiyan_web.star.star_handler import star_handlers_registry
from zhiyan_web.config import DATA_DIR, PROJECT_ROOT, BUILTIN_PLUGINS_DIR

logger = logging.getLogger("ZhiYanWeb.plugins")


class PluginManager:
    """插件管理器 - 兼容层
    提供向后兼容的接口，底层使用 star 的 PluginManager
    """

    PLUGIN_METADATA_FILENAMES = ["metadata.json", "plugin.json", "metadata.yaml", "metadata.yml"]

    def __init__(self, context, config: dict = None) -> None:
        self.context = context
        self.config = config or {}

        # 获取或创建 StarContext
        star_ctx = None
        try:
            from zhiyan_web.core import star_context
            if star_context:
                star_ctx = star_context
        except (ImportError, AttributeError):
            pass

        if not star_ctx:
            star_ctx = Context()
            star_ctx.set_config(self.config)

        # 创建 StarPluginManager 实例
        self._star_manager = StarPluginManager(context=star_ctx, config=self.config)
        self.plugin_store_path = os.path.join(DATA_DIR, "plugins")
        self.plugin_config_path = os.path.join(DATA_DIR, "plugin_configs")
        self.reserved_plugin_path = BUILTIN_PLUGINS_DIR
        self.failed_plugin_dict: dict[str, dict] = {}

    # 委托方法
    async def load(self, specified_dir_name: str = None):
        return await self._star_manager.load(specified_dir_name)

    async def unload(self, plugin_name: str):
        return await self._star_manager.unload(plugin_name)

    async def reload(self, plugin_name: str = None):
        return await self._star_manager.reload(plugin_name)

    async def enable(self, plugin_name: str) -> bool:
        return await self._star_manager.enable(plugin_name)

    async def disable(self, plugin_name: str) -> bool:
        return await self._star_manager.disable(plugin_name)

    async def get_plugin_config(self, plugin_name: str) -> dict | None:
        return await self._star_manager.get_plugin_config(plugin_name)

    async def set_plugin_config(self, plugin_name: str, config: dict) -> bool:
        return await self._star_manager.set_plugin_config(plugin_name, config)

    def get_plugin_status(self) -> list[dict]:
        return self._star_manager.get_plugin_status()