"""
AstrBot sp 模块兼容 - 提供简单的持久化存储
"""
from __future__ import annotations

import json
import os


class SimplePersist:
    """简单的持久化存储，兼容 AstrBot 的 sp 模块"""
    
    def __init__(self) -> None:
        self._data: dict = {}
        self._file_path: str | None = None
    
    def load(self, file_path: str) -> dict:
        self._file_path = file_path
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        return self._data
    
    def save(self, file_path: str | None = None) -> None:
        path = file_path or self._file_path
        if path:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
    
    def get(self, key: str, default=None):
        return self._data.get(key, default)
    
    def put(self, key: str, value) -> None:
        self._data[key] = value
    
    def delete(self, key: str) -> None:
        self._data.pop(key, None)
    
    def __getitem__(self, key: str):
        return self._data[key]
    
    def __setitem__(self, key: str, value) -> None:
        self._data[key] = value


sp = SimplePersist()

__all__ = ["sp", "SimplePersist"]