"""
预处理阶段

消息格式化、历史记录、插件事件拦截。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from ..stage import Stage, register_stage
from ..scheduler import PipelineEvent

logger = logging.getLogger("ZhiYanWeb.Pipeline")


@register_stage(order=2)
class PreProcessStage(Stage):
    """预处理阶段

    1. 消息格式化
    2. 记录用户消息到历史
    3. 触发 OnLLMRequestEvent 插件事件
    """

    async def process(self, event: PipelineEvent) -> AsyncGenerator[None, None] | None:
        message = event.get("message", "")
        if not message:
            event.stop_event()
            event.set_result({"error": "消息内容为空"})
            yield
            return

        # 消息格式化：去除首尾空白
        message = message.strip()
        event.set("message", message)

        logger.debug(f"预处理: message={message[:50]!r}...")

        yield
        # 后置处理