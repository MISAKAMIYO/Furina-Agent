"""
AstrBot core.utils.astrbot_path 兼容模块

用于 `from astrbot.core.utils.astrbot_path import get_astrbot_data_path` 的导入方式。
"""

import os


def get_astrbot_data_path() -> str:
    """获取 AstrBot 数据目录路径（兼容存根）"""
    # 从 backend/astrbot/core/utils/astrbot_path.py 向上5层到项目根
    current = os.path.abspath(__file__)
    for _ in range(5):
        current = os.path.dirname(current)
    # current = 项目根目录 (d:\Python\NEW_start\ZHIYAN AGENT WEB)
    data_dir = os.path.join(current, "data")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def get_astrbot_plugin_path() -> str:
    """获取 AstrBot 插件目录路径（兼容存根，返回 data/plugins）。"""
    plugins_dir = os.path.join(get_astrbot_data_path(), "plugins")
    os.makedirs(plugins_dir, exist_ok=True)
    return plugins_dir


def get_astrbot_plugin_data_path() -> str:
    """获取 AstrBot 插件数据目录路径（兼容存根，返回 data/plugin_data）。"""
    plugin_data_dir = os.path.join(get_astrbot_data_path(), "plugin_data")
    os.makedirs(plugin_data_dir, exist_ok=True)
    return plugin_data_dir


def get_astrbot_temp_path() -> str:
    """获取 AstrBot 临时文件目录路径（兼容存根，返回 data/tmp）。"""
    temp_dir = os.path.join(get_astrbot_data_path(), "tmp")
    os.makedirs(temp_dir, exist_ok=True)
    return temp_dir


__all__ = [
    "get_astrbot_data_path",
    "get_astrbot_plugin_path",
    "get_astrbot_plugin_data_path",
    "get_astrbot_temp_path",
]