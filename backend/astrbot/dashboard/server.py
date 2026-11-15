"""
AstrBot dashboard.server 兼容模块

用于 `from astrbot.dashboard.server import ...` 的导入方式。
"""

from starlette.responses import Response


class AstrBotDashboardServer:
    """AstrBot 仪表盘服务器（兼容存根）"""
    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self, *args, **kwargs) -> None:
        pass

    def stop(self, *args, **kwargs) -> None:
        pass


__all__ = ["AstrBotDashboardServer", "Response"]