"""
管道上下文容器

PipelineContext 聚合所有系统管理器的引用，在管道执行期间供各阶段使用。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zhiyan_web.core.llm.chat_manager import ChatManager
    from zhiyan_web.core.llm.providers import LLMProvider
    from zhiyan_web.core.tts.engine import TTSEngine
    from zhiyan_web.emotion.engine import EmotionEngine
    from zhiyan_web.emotion.bond import BondEngine
    from zhiyan_web.proactive.engine import ProactiveEngine
    from zhiyan_web.proactive.scheduler import SchedulerEngine
    from zhiyan_web.star import PluginManager  # AstrBot 兼容插件管理器
    from zhiyan_web.star.context import Context as StarContext  # AstrBot 兼容插件上下文
    from zhiyan_web.star.star_handler import StarHandlerRegistry  # AstrBot 兼容事件处理器注册中心
    from zhiyan_web.plugins.manager import PluginManager as LegacyPluginManager
    from zhiyan_web.plugins.context import PluginContext
    from zhiyan_web.plugins.event_dispatcher import EventHandlerRegistry
    from zhiyan_web.provider.func_tool_manager import FunctionToolManager
    from zhiyan_web.provider.manager import ProviderManager
    from zhiyan_web.agent.context.manager import ContextManager as AgentContextManager

logger = logging.getLogger("ZhiYanWeb.Pipeline")


class PipelineContext:
    """管道上下文，聚合所有管理器的引用

    各管理器通过属性注入，在 app 启动时完成初始化。
    """

    def __init__(self):
        # ── 核心管理器 ──
        self.chat_manager: ChatManager | None = None
        """聊天管理器（对话历史管理）"""
        self.provider_manager: ProviderManager | None = None
        """提供商管理器（LLM/STT/TTS/Embedding 生命周期）"""
        self.func_tool_manager: FunctionToolManager | None = None
        """函数工具管理器（工具注册 + MCP 客户端）"""
        self.agent_context_manager: AgentContextManager | None = None
        """智能体上下文管理器（Token 计数 + 压缩）"""

        # ── 现有模块 ──
        self.tts_engine: TTSEngine | None = None
        """TTS 引擎"""
        self.emotion_engine: EmotionEngine | None = None
        """情感引擎"""
        self.bond_engine: BondEngine | None = None
        """羁绊引擎"""
        self.proactive_engine: ProactiveEngine | None = None
        """主动交互引擎"""
        self.scheduler_engine: SchedulerEngine | None = None
        """定时任务调度器"""

        # ── 插件系统（AstrBot 兼容） ──
        self.plugin_manager: PluginManager | None = None
        """Star 插件管理器（AstrBot 兼容）"""
        self.plugin_context: PluginContext | None = None
        """旧版插件上下文（向后兼容）"""
        self.star_context: StarContext | None = None
        """Star 插件上下文 API（AstrBot 兼容）"""
        self.event_handler_registry: EventHandlerRegistry | None = None
        """旧版事件处理器注册中心（向后兼容）"""
        self.star_handler_registry: StarHandlerRegistry | None = None
        """Star 事件处理器注册中心（AstrBot 兼容，引用 star_handlers_registry）"""

        # ── 运行时状态 ──
        self._extra: dict[str, Any] = {}
        """额外运行时数据"""

    def set(self, key: str, value: Any) -> None:
        """设置额外运行时数据"""
        self._extra[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """获取额外运行时数据"""
        return self._extra.get(key, default)

    @property
    def llm_provider(self):
        """获取当前 LLM 提供商（兼容旧接口）"""
        if self.provider_manager:
            return self.provider_manager.get_using_provider("chat_completion")
        if self.chat_manager:
            return self.chat_manager.get_provider()
        return None

    def __repr__(self) -> str:
        attrs = []
        for k, v in self.__dict__.items():
            if v is not None:
                attrs.append(f"{k}={type(v).__name__}")
        return f"PipelineContext({', '.join(attrs)})"