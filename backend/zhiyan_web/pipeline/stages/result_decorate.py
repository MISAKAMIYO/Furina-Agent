"""
结果装饰阶段

情感注入、TTS 触发、结果格式化。
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from ..stage import Stage, register_stage
from ..scheduler import PipelineEvent

logger = logging.getLogger("ZhiYanWeb.Pipeline")


@register_stage(order=4)
class ResultDecorateStage(Stage):
    """结果装饰阶段

    1. 情感注入（如果启用了情感引擎）
    2. TTS 触发（如果启用了 TTS）
    3. 结果格式化
    """

    async def process(self, event: PipelineEvent) -> AsyncGenerator[None, None] | None:
        result = event.get_result()
        if result is None:
            yield
            return

        # 情感注入
        if self.context.emotion_engine:
            try:
                emotion_data = self.context.emotion_engine.get_emotion()
                event.set("emotion", emotion_data.get("mood", "平静"))
            except Exception:
                pass

        yield
        # 后置处理：自动更新情感/羁绊/好感度 + TTS 触发
        message = event.get("message", "")
        result = event.get_result()
        response_text = result.get("response", "") if isinstance(result, dict) else ""

        # 1. 自动更新情感状态（基于用户消息内容）
        if self.context.emotion_engine and message:
            try:
                self.context.emotion_engine.update(text=message)
                logger.debug(f"情感引擎已自动更新 (msg: {message[:30]!r})")
            except Exception as e:
                logger.debug(f"情感引擎自动更新失败: {e}")

        # 2. 自动更新羁绊系统
        if self.context.bond_engine and message:
            try:
                self.context.bond_engine.update(
                    user_message=message,
                    assistant_reply=response_text,
                )
                logger.debug(f"羁绊系统已自动更新")
            except Exception as e:
                logger.debug(f"羁绊系统自动更新失败: {e}")

        # 3. 自动更新好感度经验（每轮对话 +1 EXP）
        if message and not event.is_stopped():
            try:
                # 懒导入，避免循环依赖
                from zhiyan_web.likability.routers.likability import add_exp
                add_exp(1, "chat")
            except Exception as e:
                logger.debug(f"好感度自动更新失败: {e}")

        # 4. 好感度+羁绊 → 影响情感状态（联动）
        if self.context.emotion_engine:
            try:
                bond_status = None
                likability_level = None
                if self.context.bond_engine:
                    bond_status = self.context.bond_engine.get_status()
                try:
                    from zhiyan_web.likability.routers.likability import get_likability_record
                    record = get_likability_record()
                    likability_level = record.get("level", 1)
                except Exception:
                    pass
                self.context.emotion_engine.apply_bond_likability_influence(
                    bond_status=bond_status,
                    likability_level=likability_level,
                )
            except Exception as e:
                logger.debug(f"情感联动更新失败: {e}")

        # TTS 触发
        tts_enabled = event.get("tts_enabled", False)
        if tts_enabled and result:
            text = result.get("response", "") if isinstance(result, dict) else str(result)
            if text and self.context.tts_engine:
                logger.debug(f"TTS 触发: text={text[:50]!r}...")