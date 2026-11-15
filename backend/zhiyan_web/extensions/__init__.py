"""扩展能力聚合模块。

ZhiYan 目前存在多套彼此独立的扩展体系，各自有独立的执行管道：

  原生插件 (plugins)   -> star_handlers_registry，由 ProcessStage 的命令/正则 Handler 处理
  原生技能 (skills)    -> FunctionToolManager，作为工具供 Agent 工具循环调用
  MCP 服务 (mcp)       -> FunctionToolManager（工具名带 mcp__ 前缀隔离），独立事件循环线程
  DSH 插件 (dsh)       -> DSH 内核子进程内的 cordis 行，随内核重启生效
  DSH 技能 (dsh)       -> DSH 内核内的 skill 文件，同样随内核生效

本模块只做「管理面」的聚合：把五类扩展的数量与运行状态汇总成一份总览，
供统一管理页展示。执行面仍各走各的管道，互不影响。

除此之外还提供内置 agent 引擎自身的可扩展点管理面（/api/extensions/agent）：
子代理类型、工具分组与场景、生命周期钩子，以及声明式扩展清单的加载与热重载。
"""
from __future__ import annotations

__all__ = ["router", "load_manifests", "summary", "contributions"]

from .routers import router  # noqa: E402
from .registry import contributions, load_manifests, summary  # noqa: E402
