"""
AstrBot StarTools 兼容模块
"""
from __future__ import annotations

import os
from pathlib import Path


class StarTools:
    """AstrBot 的 StarTools 兼容实现"""

    @staticmethod
    def get_data_dir(plugin_name: str | None = None) -> Path:
        """获取插件数据存储目录
        
        Args:
            plugin_name: 插件名称（可选，不提供时返回默认数据目录）
            
        Returns:
            插件数据目录的 Path 对象
        """
        # 数据目录在 data/plugins_data/{plugin_name}/
        try:
            from zhiyan_web.config import DATA_DIR
            data_dir = Path(DATA_DIR) / "plugins_data"
        except Exception:
            # 回退：向上找到 backend 的父目录
            current_dir = os.path.dirname(os.path.abspath(__file__))
            for _ in range(6):
                if os.path.basename(current_dir) == "backend":
                    break
                current_dir = os.path.dirname(current_dir)
            project_root = os.path.dirname(current_dir)  # backend 的父目录
            data_dir = Path(project_root) / "data" / "plugins_data"
        if plugin_name:
            data_dir = data_dir / plugin_name
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir

    @staticmethod
    def get_plugin_data_dir(plugin_name: str) -> Path:
        """获取插件数据目录（兼容 AstrBot 新版接口）"""
        return StarTools.get_data_dir(plugin_name)


__all__ = ["StarTools"]