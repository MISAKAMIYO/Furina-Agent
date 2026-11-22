"""
管道阶段基类

提供:
- Stage 抽象基类（洋葱模型接口）
- register_stage 装饰器（全局阶段注册）
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator
from typing import Any

logger = logging.getLogger("ZhiYanWeb.Pipeline")

# 全局阶段注册表：(order, stage_class)
_stage_registry: list[tuple[int, type["Stage"]]] = []


def register_stage(order: int):
    """注册阶段到全局调度器

    Args:
        order: 执行顺序（数字越小越先执行）
    """

    def decorator(cls: type["Stage"]):
        _stage_registry.append((order, cls))
        # 按 order 排序
        _stage_registry.sort(key=lambda x: x[0])
        logger.debug(f"阶段已注册: [{order}] {cls.__name__}")
        return cls

    return decorator


def get_registered_stages() -> list[tuple[int, type["Stage"]]]:
    """获取所有已注册的阶段"""
    return list(_stage_registry)


class Stage(ABC):
    """管道阶段基类

    每个阶段实现 process() 方法。
    如果该方法是一个 AsyncGenerator（包含 yield），则实现洋葱模型：
    - yield 之前：前置处理
    - yield 之后（递归返回时）：后置处理
    - 通过 yield 暂停当前阶段，让调度器执行后续阶段
    """

    def __init__(self, context: "PipelineContext"):
        self.context = context

    @abstractmethod
    async def process(self, event: Any) -> AsyncGenerator[None, None] | None:
        """处理事件

        Args:
            event: 当前处理的事件对象

        Returns:
            AsyncGenerator 或 None:
            - AsyncGenerator: 实现洋葱模型（前置 → yield → 后置）
            - None: 普通处理，不参与洋葱模型
        """
        ...

    async def initialize(self) -> None:
        """阶段初始化（可选重写）"""
        pass