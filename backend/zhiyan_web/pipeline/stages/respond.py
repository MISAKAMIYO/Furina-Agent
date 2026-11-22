"""
响应发送阶段

将处理结果发送给客户端。
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from ..stage import Stage, register_stage
from ..scheduler import PipelineEvent

logger = logging.getLogger("ZhiYanWeb.Pipeline")


@register_stage(order=5)
class RespondStage(Stage):
    """响应发送阶段

    将结果发送给客户端。
    - 如果是 WebSocket 连接，通过 ConnectionManager 广播
    - 如果是 HTTP 请求，结果通过 event.get_result() 获取
    """

    async def process(self, event: PipelineEvent) -> AsyncGenerator[None, None] | None:
        result = event.get_result()
        if result is None:
            logger.debug("无结果需要发送")
            return

        # 检查事件是否被停止（插件已处理）
        if event.is_stopped():
            logger.debug(f"事件已停止，发送插件响应: {str(result)[:100]!r}")
        else:
            logger.debug(f"响应发送: result={str(result)[:100]!r}...")

        # 后置处理（无操作）