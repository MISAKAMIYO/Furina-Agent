"""
Furina Agent Web - 模块化后端框架
基于 FastAPI 构建，采用模块化架构设计

模块结构:
- config/     - 配置管理模块
- core/       - 核心组件模块（LLM、TTS、WebSocket）
- music/      - 音乐播放器模块
- chat/       - 聊天功能模块
- db/         - 数据库模块
- utils/      - 工具函数模块
"""
from __future__ import annotations

import asyncio
import contextvars
import functools

__version__ = "1.0.0"
__author__ = "ZhiYan Team"

# Python 3.8（Windows 7 可用的最后一个版本）没有 asyncio.to_thread（3.9+ 才有），
# 这里按官方实现补齐，使各模块可继续直接使用 asyncio.to_thread。
if not hasattr(asyncio, "to_thread"):

    async def _to_thread(func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        ctx = contextvars.copy_context()
        return await loop.run_in_executor(
            None, functools.partial(ctx.run, func, *args, **kwargs)
        )

    asyncio.to_thread = _to_thread
