"""
子代理（Sub Agent）

内置 agent 对话引擎的子代理能力，对齐 DSH 内核 tool-subagent 的设计并做了强化：

基础能力
- 独立上下文：子代理有自己的消息列表，探索过程不污染主对话历史；
- 最小权限：按子代理类型裁剪工具集（场景 + 分组白名单 + 允许/禁止清单）；
- 轮次预算：每个类型有自己的 max_iterations，防止失控；
- 可观测：运行过程通过 events 通道实时推给父 SSE 流，前端可视化。

强化能力
- 并发：同一条回复里发起的多个子代理会并行执行（并发度可配）；
- 受控嵌套：子代理可以再派子代理，深度与总次数由回合预算兜底；
- 后台运行：spawn_background_subagent 起长任务，不阻塞当前回复，完成后回灌；
- 取消：运行中可从界面中断（软取消，不穿透成整轮中断）；
- 计量：每次运行记录轮次 / 工具调用 / token / 耗时，可回看；
- 共享白板：并行子代理之间交换中间发现，避免重复劳动；
- 逐字回传：结论分片推给前端，卡片里能看到它在「写出来」；
- 类型管理：内置类型 + 界面自定义类型（落盘 data/），同名可覆盖内置。

模块分工
- registry      类型定义与内存注册表
- types_store   自定义类型的落盘 CRUD
- runner        单个子代理的独立上下文 ReAct 循环
- tools         对外的工具（派发 / 后台 / 白板）
- events        往父 SSE 流推送事件（ContextVar sink）
- runtime       回合预算、取消、运行记录、后台任务
- board         共享白板
- routers       /api/subagent/* 管理接口
"""
from __future__ import annotations

from .board import begin_board, current_board, end_board
from .events import emit, get_sink, reset_sink, set_sink
from .registry import (
    SubAgentType,
    default_type,
    describe_types,
    get_subagent_type,
    list_subagent_types,
    register_subagent_type,
    unregister_subagent_type,
)
from .runner import run_subagent, scope_tools
from .runtime import (
    begin_budget,
    end_budget,
    jobs_stats,
    list_jobs,
    list_runs,
    pending_jobs,
    request_cancel,
    runs_stats,
)
from .tools import register_subagent_tools, set_current_conv_id

__all__ = [
    "SubAgentType",
    "default_type",
    "describe_types",
    "get_subagent_type",
    "list_subagent_types",
    "register_subagent_type",
    "unregister_subagent_type",
    "run_subagent",
    "scope_tools",
    "register_subagent_tools",
    "set_current_conv_id",
    "emit",
    "get_sink",
    "set_sink",
    "reset_sink",
    "begin_budget",
    "end_budget",
    "request_cancel",
    "list_runs",
    "runs_stats",
    "list_jobs",
    "jobs_stats",
    "pending_jobs",
    "current_board",
    "begin_board",
    "end_board",
]
