"""
AstrBot 配置兼容模块
"""

from __future__ import annotations

import json
import os
import logging

logger = logging.getLogger("ZhiYanWeb.star")


class AstrBotConfig(dict):
    """AstrBot 配置类 - 兼容 AstrBot 的配置对象
    
    支持 dict 风格的访问和 save_config() 方法。
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._config_file: str | None = None

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            return self.__class__()

    def __missing__(self, key):
        """缺失键返回空 AstrBotConfig，兼容插件中 AstrBotConfig()['dashboard'] 的访问模式"""
        return self.__class__()

    def load(self, config_file: str | None = None) -> None:
        """从文件加载配置"""
        if config_file and os.path.exists(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.update(data)
                self._config_file = config_file
            except Exception as e:
                logger.error(f"加载配置失败 {config_file}: {e}")

    def save_config(self) -> None:
        """保存配置到文件（兼容 AstrBot 接口）"""
        if self._config_file:
            try:
                os.makedirs(os.path.dirname(self._config_file), exist_ok=True)
                with open(self._config_file, "w", encoding="utf-8") as f:
                    json.dump(dict(self), f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error(f"保存配置失败 {self._config_file}: {e}")


__all__ = ["AstrBotConfig"]