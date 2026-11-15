"""
AstrBot dashboard 兼容模块 - 作为 from astrbot.dashboard import ... 的目标

提供空的 dashboard 类，避免插件导入失败。
"""


class Dashboard:
    """仪表盘类（兼容 AstrBot）"""
    def __init__(self, *args, **kwargs) -> None:
        pass


__all__ = ["Dashboard"]