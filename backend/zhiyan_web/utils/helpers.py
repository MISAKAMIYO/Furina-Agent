"""
通用辅助函数模块
提供项目中常用的工具函数

功能列表:
- format_size: 格式化文件大小
- format_time: 格式化时间
"""
from __future__ import annotations

def format_size(bytes: int) -> str:
    """
    格式化文件大小
    
    参数:
        bytes: 文件大小（字节）
    
    返回:
        str: 格式化后的文件大小字符串
    """
    if bytes < 1024:
        return f"{bytes} B"
    if bytes < 1024 * 1024:
        return f"{bytes / 1024:.1f} KB"
    if bytes < 1024 * 1024 * 1024:
        return f"{bytes / (1024 * 1024):.2f} MB"
    return f"{bytes / (1024 * 1024 * 1024):.2f} GB"


def format_time(seconds: float) -> str:
    """
    格式化时间（秒转分秒）
    
    参数:
        seconds: 时间（秒）
    
    返回:
        str: 格式化后的时间字符串（MM:SS）
    """
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}:{secs:02d}"
