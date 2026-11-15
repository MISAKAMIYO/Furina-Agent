"""
情感系统 API 路由
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("ZhiYanWeb.emotion")

emotion_router = APIRouter(prefix="/api/emotion", tags=["emotion"])

# 全局实例引用
_emotion_engine = None
_bond_engine = None
_drift_detector = None


def set_emotion_engine(engine):
    global _emotion_engine
    _emotion_engine = engine


def set_bond_engine(engine):
    global _bond_engine
    _bond_engine = engine


def set_drift_detector(detector):
    global _drift_detector
    _drift_detector = detector


class EmotionUpdate(BaseModel):
    event_type: Optional[str] = None
    value: float = 0.0
    text: Optional[str] = None


class BondUpdate(BaseModel):
    user_message: str = ""
    assistant_reply: str = ""


# ===== 情绪状态 API =====

@emotion_router.get("/status")
async def get_emotion_status():
    """获取当前情绪状态"""
    if not _emotion_engine:
        return {"error": "情感引擎未初始化"}
    return _emotion_engine.get_emotion()


@emotion_router.get("/statistics")
async def get_emotion_statistics():
    """获取情绪统计数据"""
    if not _emotion_engine:
        return {"error": "情感引擎未初始化"}
    return _emotion_engine.get_statistics()


@emotion_router.get("/history")
async def get_emotion_history(limit: int = 12):
    """获取情绪历史"""
    if not _emotion_engine:
        return {"history": []}
    return {"history": _emotion_engine.get_history(limit=limit)}


@emotion_router.post("/update")
async def update_emotion(update: EmotionUpdate):
    """更新情绪状态"""
    if not _emotion_engine:
        raise HTTPException(status_code=500, detail="情感引擎未初始化")
    result = _emotion_engine.update(
        event_type=update.event_type,
        value=update.value,
        text=update.text,
    )
    return {"status": "success", "emotion": result}


@emotion_router.post("/reset")
async def reset_emotion():
    """重置情绪状态"""
    if not _emotion_engine:
        raise HTTPException(status_code=500, detail="情感引擎未初始化")
    _emotion_engine.reset()
    return {"status": "success", "message": "情绪状态已重置"}


# ===== 羁绊系统 API =====

@emotion_router.get("/bond")
async def get_bond_status():
    """获取羁绊状态"""
    if not _bond_engine:
        return {"error": "羁绊引擎未初始化"}
    return _bond_engine.get_status()


@emotion_router.get("/bond/history")
async def get_bond_history(limit: int = 12):
    """获取羁绊历史"""
    if not _bond_engine:
        return {"history": []}
    return {"history": _bond_engine.get_history(limit=limit)}


@emotion_router.post("/bond/update")
async def update_bond(update: BondUpdate):
    """更新羁绊值"""
    if not _bond_engine:
        raise HTTPException(status_code=500, detail="羁绊引擎未初始化")
    result = _bond_engine.update(
        user_message=update.user_message,
        assistant_reply=update.assistant_reply,
    )
    return {"status": "success", "bond": result}


@emotion_router.post("/bond/reset")
async def reset_bond():
    """重置羁绊状态"""
    if not _bond_engine:
        raise HTTPException(status_code=500, detail="羁绊引擎未初始化")
    _bond_engine.reset()
    return {"status": "success", "message": "羁绊状态已重置"}


# ===== 角色漂移检测 API =====

@emotion_router.post("/drift/analyze")
async def analyze_drift(data: dict):
    """分析角色漂移"""
    if not _drift_detector:
        return {"drift": False, "error": "漂移检测器未初始化"}
    text = data.get("text", "")
    if not text:
        return {"drift": False, "error": "缺少文本内容"}
    return _drift_detector.analyze(text)


@emotion_router.get("/drift/status")
async def get_drift_status():
    """获取漂移检测状态"""
    if not _drift_detector:
        return {"error": "漂移检测器未初始化"}
    return {
        "smooth_score": _drift_detector.smooth_score,
        "consecutive_low": _drift_detector.consecutive_low,
        "anchor_text": _drift_detector.anchor_text[:50] + "...",
    }