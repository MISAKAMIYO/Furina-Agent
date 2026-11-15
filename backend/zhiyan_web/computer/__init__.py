"""
电脑操控模块 - 提供 Agent 操控电脑的能力

功能:
- 文件系统操作（读写编辑删除搜索）
- 进程管理（启动终止列出）
- 浏览器自动化（导航搜索截图）
- 系统命令执行（带超时和安全限制）
- 桌面自动化（键盘鼠标模拟）
- 代码编写与项目管理

该模块为 Agent 提供"双手"，使其能够像人类一样操作电脑。
"""
from __future__ import annotations

from .services.executor import ComputerExecutor
from .services.audit import AuditLogger

__all__ = ["ComputerExecutor", "AuditLogger"]