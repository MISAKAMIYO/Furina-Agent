"""
情感系统模块
基于神经化学模型的情绪引擎、羁绊系统和角色漂移检测
"""
from __future__ import annotations

from .engine import EmotionEngine
from .bond import BondEngine
from .drift import DriftDetector

__all__ = ["EmotionEngine", "BondEngine", "DriftDetector"]