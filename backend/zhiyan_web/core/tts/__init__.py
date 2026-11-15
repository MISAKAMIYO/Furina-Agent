"""
TTS 核心模块
提供语音合成功能，支持多种 TTS 引擎

模块结构:
- __init__.py      - 公共接口导出
- engine.py        - TTS 引擎实现
"""
from __future__ import annotations

from .engine import TTSEngine

__all__ = ["TTSEngine"]
