"""
BetterGI 托管模块（框架骨架）

职责：
- 读取 bettergi 配置
- 监测 BetterGI 进程与运行状态
- 异常时按配置重启（重启次数超过上限则告警）
- 通过 QQ 平台推送事件（可选）

后续扩展点（当前仅保留接口占位）：
- BetterGI WebSocket / MCP 通讯
- 图像识别兜底
- 任务完成后自动关机
"""
from __future__ import annotations

from .service import BetterGiManager, get_bettergi_manager

__all__ = ["BetterGiManager", "get_bettergi_manager"]
