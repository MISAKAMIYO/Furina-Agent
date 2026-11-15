"""
智能体系统

提供智能体运行时的核心实现。

模块结构:
- tool.py          - 函数工具定义 (FunctionTool, ToolSet)
- hooks.py         - 智能体生命周期钩子
- run_context.py   - 智能体运行上下文
- runners/         - 智能体运行器实现
- context/         - 上下文管理（Token 计数、压缩）
"""
from __future__ import annotations