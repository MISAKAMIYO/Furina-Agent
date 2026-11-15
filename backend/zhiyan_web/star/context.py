"""插件上下文 API - 提供插件可用的所有系统能力"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .star import star_map, star_registry
from .star_handler import EventType, star_handlers_registry

if TYPE_CHECKING:
    from . import PluginManager

logger = logging.getLogger("ZhiYanWeb")


class Context:
    """插件上下文，聚合所有系统能力提供给插件使用"""

    def __init__(self) -> None:
        self._chat_manager = None
        self._tts_engine = None
        self._provider_manager = None
        self._config = {}
        self._star_manager: PluginManager | None = None
        self._web_api_routes: list[tuple] = []
        self._llm_tools = []

    @property
    def provider_manager(self):
        """获取提供商管理器（兼容 AstrBot 插件）"""
        return self._provider_manager

    @provider_manager.setter
    def provider_manager(self, pm) -> None:
        self._provider_manager = pm

    def set_chat_manager(self, cm) -> None:
        self._chat_manager = cm

    def set_tts_engine(self, engine) -> None:
        self._tts_engine = engine

    def set_provider_manager(self, pm) -> None:
        self._provider_manager = pm

    def set_config(self, config: dict) -> None:
        self._config = config

    # ────────── LLM 接口 ──────────

    async def llm_generate(self, *, prompt: str, **kwargs) -> str:
        """调用 LLM 生成文本"""
        if not self._chat_manager:
            return "错误：聊天管理器未初始化"
        try:
            response = await self._chat_manager.chat(prompt, **kwargs)
            return response.content
        except Exception as e:
            logger.error(f"LLM 生成失败: {e}")
            return f"错误：{e}"

    # ────────── 提供商访问 ──────────

    def get_using_provider(self, umo=None):
        """获取当前使用的 LLM 提供商"""
        if self._provider_manager:
            return self._provider_manager.get_using_provider("chat_completion")
        if self._chat_manager:
            return self._chat_manager.get_provider()
        return None

    def get_using_tts_provider(self, umo=None):
        return self._tts_engine

    def get_all_providers(self):
        if self._provider_manager:
            return self._provider_manager.get_all_providers()
        return []

    # ────────── 插件管理 ──────────

    def get_registered_star(self, star_name: str):
        """通过插件名称获取插件元数据"""
        for metadata in star_registry:
            if metadata.name == star_name:
                return metadata
            if metadata.root_dir_name == star_name:
                return metadata
        return None

    def get_all_stars(self):
        """获取所有已注册的插件元数据"""
        return list(star_registry)

    # ────────── 配置与数据库 ──────────

    def get_config(self, umo=None) -> dict:
        """获取配置"""
        return self._config

    # ────────── Web API 注册 ──────────

    def register_web_api(self, route: str, view_handler, methods: list[str], desc: str):
        """插件注册 HTTP API"""
        self._web_api_routes.append((route, view_handler, methods, desc))
        logger.info(f"插件注册 Web API: {route} ({methods})")

    def get_web_api_routes(self):
        return list(self._web_api_routes)

    # ────────── 工具注册 ──────────

    def add_llm_tools(self, *tools):
        """注册 LLM 函数工具"""
        from zhiyan_web.provider.register import llm_tools
        for tool in tools:
            llm_tools.add(tool)
        self._llm_tools.extend(tools)