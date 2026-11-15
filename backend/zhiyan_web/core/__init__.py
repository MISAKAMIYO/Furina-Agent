"""
核心模块
提供系统核心功能组件

导出:
- LLM 模块
- TTS 模块
- WebSocket 模块
- Pipeline 系统
- 共享实例（chat_manager, tts_engine, pipeline_scheduler）
"""
from __future__ import annotations

from .llm import LLMProvider, MockProvider, OpenAIProvider, create_provider, ChatManager, LLMMessage, LLMResponse
from .tts import TTSEngine
from .websocket import ConnectionManager

# 共享实例（在 app.py 中初始化）
chat_manager = None
tts_engine = None
pipeline_scheduler = None
pipeline_context = None
provider_manager = None
func_tool_manager = None
star_context = None
"""Star 插件上下文（AstrBot 兼容，在 app.py 中初始化）"""


def init_core():
    """
    初始化核心模块，加载完整配置
    
    初始化顺序:
    1. 加载配置
    2. 创建聊天管理器
    3. 创建 TTS 引擎
    4. 创建 Provider 管理器
    5. 创建函数工具管理器
    6. 创建管道调度器
    """
    global chat_manager, tts_engine, pipeline_scheduler, pipeline_context
    global provider_manager, func_tool_manager
    
    from zhiyan_web.config import get_settings
    settings = get_settings()
    
    # 1. 聊天管理器
    chat_manager = ChatManager()
    
    # 2. TTS 引擎
    tts_engine = TTSEngine(
        engine_type=settings.get("tts_engine", "auto"),
        voice=settings.get("tts_voice", "zh-CN-XiaoxiaoNeural"),
        rate=settings.get("tts_rate", 200),
        volume=settings.get("tts_volume", 1.0),
        config=settings
    )
    
    # 3. Provider 管理器
    from zhiyan_web.provider.manager import ProviderManager
    provider_manager = ProviderManager()
    
    # 4. 函数工具管理器
    from zhiyan_web.provider.func_tool_manager import FunctionToolManager
    func_tool_manager = FunctionToolManager()
    
    # 5. 管道调度器（洋葱模型）
    # 必须先导入 pipeline 包以触发 register_stage 装饰器
    import zhiyan_web.pipeline
    from zhiyan_web.pipeline.scheduler import PipelineScheduler
    from zhiyan_web.pipeline.context import PipelineContext
    pipeline_context = PipelineContext()
    pipeline_scheduler = PipelineScheduler(context=pipeline_context)
    pipeline_scheduler.build()  # 立即构建阶段列表
    
    # 注入管理器到管道上下文
    pipeline_context.chat_manager = chat_manager
    pipeline_context.tts_engine = tts_engine
    pipeline_context.provider_manager = provider_manager
    pipeline_context.func_tool_manager = func_tool_manager
    
    # 注册管道到聊天管理器
    chat_manager.register_pipeline(pipeline_scheduler)
    
    logger = logging.getLogger("ZhiYanWeb")
    logger.info("核心模块初始化完成（含管道系统）")


def init_pipeline_modules():
    """
    初始化管道关联模块
    
    在情感引擎、插件系统等模块初始化完成后调用，
    将这些模块注入管道上下文。
    """
    global pipeline_context
    
    if pipeline_context is None:
        return
    
    import logging
    logger = logging.getLogger("ZhiYanWeb")
    
    # 注入情感引擎
    try:
        from zhiyan_web.core import emotion_engine, bond_engine
        if emotion_engine:
            pipeline_context.emotion_engine = emotion_engine
        if bond_engine is not None:
            from zhiyan_web.emotion.bond import BondEngine
            pipeline_context.bond_engine = bond_engine
    except (ImportError, AttributeError):
        pass
    
    # 注入主动交互引擎
    try:
        from zhiyan_web.core import proactive_engine, scheduler_engine
        if proactive_engine is not None:
            pipeline_context.proactive_engine = proactive_engine
        if scheduler_engine is not None:
            pipeline_context.scheduler_engine = scheduler_engine
    except (ImportError, AttributeError):
        pass
    
    # 注入插件系统（AstrBot 兼容 + 旧版兼容）
    try:
        from zhiyan_web.core import plugin_manager, plugin_context, event_handler_registry
        if plugin_manager is not None:
            pipeline_context.plugin_manager = plugin_manager
        if plugin_context is not None:
            pipeline_context.plugin_context = plugin_context
        if event_handler_registry is not None:
            pipeline_context.event_handler_registry = event_handler_registry
    except (ImportError, AttributeError):
        pass

    # 注入 Star 插件上下文和 Handler 注册中心
    try:
        from zhiyan_web.core import star_context
        if star_context is not None:
            pipeline_context.star_context = star_context
        from zhiyan_web.star.star_handler import star_handlers_registry
        pipeline_context.star_handler_registry = star_handlers_registry
    except (ImportError, AttributeError):
        pass
    
    # 注入智能体上下文管理器
    try:
        from zhiyan_web.agent.context.manager import ContextManager, ContextConfig
        from zhiyan_web.agent.context.token_counter import TokenCounter
        agent_context_manager = ContextManager(
            config=ContextConfig(),
            token_counter=TokenCounter(),
        )
        pipeline_context.agent_context_manager = agent_context_manager
    except Exception as e:
        logger.debug(f"智能体上下文管理器初始化失败: {e}")
    
    logger.info("管道模块初始化完成")


import logging

__all__ = [
    "LLMProvider",
    "MockProvider",
    "OpenAIProvider",
    "create_provider",
    "ChatManager",
    "LLMMessage",
    "LLMResponse",
    "TTSEngine",
    "ConnectionManager",
    "chat_manager",
    "tts_engine",
    "pipeline_scheduler",
    "pipeline_context",
    "provider_manager",
    "func_tool_manager",
    "star_context",
    "init_core",
    "init_pipeline_modules",
]
