"""
AstrBot CommandGroupFilter 兼容模块
"""
from __future__ import annotations


class CommandGroupFilter:
    """命令组过滤器（兼容 AstrBot）"""
    def __init__(self, parent_command_names: list[str] | None = None) -> None:
        self.parent_command_names = parent_command_names if parent_command_names is not None else [""]
    
    def filter(self, event, cfg: dict) -> bool:
        return True


__all__ = ["CommandGroupFilter"]