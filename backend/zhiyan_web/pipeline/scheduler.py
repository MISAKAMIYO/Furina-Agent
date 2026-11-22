"""
管道调度器

实现洋葱模型的事件处理管线。
调度器按注册顺序执行所有阶段，支持：
- 前置/后置处理（洋葱模型）
- 事件停止传播
- 阶段异常隔离
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator
from typing import Any

from .stage import Stage, get_registered_stages
from .context import PipelineContext

logger = logging.getLogger("ZhiYanWeb.Pipeline")


class PipelineEvent:
    """管道事件基类

    在管道中传递的事件对象，支持停止传播和结果设置。
    """

    def __init__(self, data: dict[str, Any] | None = None):
        self._data: dict[str, Any] = data or {}
        self._stopped: bool = False
        self._result: Any = None

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    @data.setter
    def data(self, value: dict[str, Any]) -> None:
        self._data = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def stop_event(self) -> None:
        """停止事件在管道中的传播"""
        self._stopped = True

    def is_stopped(self) -> bool:
        return self._stopped

    def set_result(self, result: Any) -> None:
        self._result = result

    def get_result(self) -> Any:
        return self._result


class PipelineScheduler:
    """管道调度器

    管理所有阶段的执行顺序，实现洋葱模型。
    """

    def __init__(self, context: PipelineContext | None = None):
        self.context = context or PipelineContext()
        self.stages: list[Stage] = []
        self._initialized = False

    def build(self) -> None:
        """从注册表构建阶段实例列表"""
        registered = get_registered_stages()
        self.stages = []
        for order, stage_cls in registered:
            stage = stage_cls(self.context)
            self.stages.append(stage)
        logger.info(
            f"管道已构建: {len(self.stages)} 个阶段"
            f" ({', '.join(s.__class__.__name__ for s in self.stages)})"
        )
        self._initialized = True

    async def initialize(self) -> None:
        """初始化所有阶段"""
        if not self._initialized:
            self.build()
        for stage in self.stages:
            try:
                await stage.initialize()
            except Exception as e:
                logger.error(f"阶段初始化失败 {stage.__class__.__name__}: {e}")
        logger.info("所有阶段初始化完成")

    async def execute(self, event: PipelineEvent) -> PipelineEvent:
        """执行管道处理

        Args:
            event: 要处理的事件

        Returns:
            PipelineEvent: 处理完成后的事件（包含结果）
        """
        if not self._initialized:
            await self.initialize()

        await self._process_stages(event, 0)
        return event

    async def _process_stages(
        self, event: PipelineEvent, from_stage: int
    ) -> None:
        """递归处理阶段（洋葱模型核心）

        遍历 from_stage 之后的所有阶段：
        - 如果阶段是 AsyncGenerator → 执行前置处理，yield 暂停，递归执行后续阶段，
          后续阶段全部完成后回到 yield 处执行后置处理
        - 如果阶段是普通协程 → 直接执行
        - 如果事件被停止 → 跳过后续阶段
        """
        for i in range(from_stage, len(self.stages)):
            if event.is_stopped():
                logger.debug(f"事件已停止，跳过后续阶段 [from={from_stage}, current={i}]")
                return

            stage = self.stages[i]
            stage_name = stage.__class__.__name__

            try:
                coroutine = stage.process(event)

                if isinstance(coroutine, AsyncGenerator):
                    # 洋葱模型：前置处理 → 递归后续阶段 → 后置处理
                    async for _ in coroutine:
                        if event.is_stopped():
                            break
                        # 递归执行所有后续阶段
                        await self._process_stages(event, i + 1)
                else:
                    # 普通协程，直接等待完成
                    if coroutine is not None:
                        await coroutine
                
                # 关键修复：每个阶段执行后都检查事件是否被停止
                # 之前只在 AsyncGenerator 分支检查，导致返回 None 的阶段
                # 即使调用了 stop_event() 也不会被后续阶段感知
                if event.is_stopped():
                    logger.debug(f"事件已停止（阶段 {stage_name} 后），跳过后续阶段")
                    return

            except Exception as e:
                logger.error(
                    f"阶段执行异常 {stage_name}: {e}", exc_info=True
                )
                # 阶段异常 → 停止事件传播
                event.stop_event()
                event.set_result({"error": str(e)})
                return

        logger.debug("管道处理完成")