"""
管道系统
实现洋葱模型的事件处理管线

模块结构:
- stage.py        - Stage 基类 + 注册装饰器
- context.py      - PipelineContext 上下文容器
- scheduler.py    - PipelineScheduler 调度器
- stages/         - 内置阶段实现
"""
from __future__ import annotations

import logging
_logger = logging.getLogger("ZhiYanWeb.Pipeline")

# 导入内置阶段以触发 register_stage 装饰器
from . import stages
from .stage import get_registered_stages
_logger.info(f"pipeline 包已加载, 已注册阶段: {get_registered_stages()}")