"""
唤醒检查阶段

检查消息是否被 @ 或唤醒前缀触发，设置事件中的唤醒标记。
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from ..stage import Stage, register_stage
from ..scheduler import PipelineEvent

logger = logging.getLogger("ZhiYanWeb.Pipeline")


@register_stage(order=1)
class WakingCheckStage(Stage):
    """唤醒检查阶段

    检查消息是否匹配唤醒前缀，设置 is_at_or_wake_command 标记。
    """

    async def process(self, event: PipelineEvent) -> AsyncGenerator[None, None] | None:
        message = event.get("message", "")
        is_at = event.get("is_at", False)
        wake_prefixes = event.get("wake_prefixes", ["/", "!"])
        is_wake = False

        if message:
            for prefix in wake_prefixes:
                if message.strip().startswith(prefix):
                    is_wake = True
                    break

        event.set("is_at_or_wake_command", is_at or is_wake)
        logger.debug(f"唤醒检查: message={message[:30]!r}..., is_wake={is_wake}")

        yield
        # 后置处理（无操作）