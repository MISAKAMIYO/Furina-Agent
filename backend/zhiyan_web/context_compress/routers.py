"""
上下文压缩 API 路由

提供上下文压缩系统的 HTTP 接口：
- 配置查询与更新（压缩策略配置模块）
- 手动触发连续压缩 / 历史压缩
- 压缩效果评估（评估机制）
- 压缩统计与存档查询
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zhiyan_web.context_compress.compressor import SemanticCompressor
from zhiyan_web.context_compress.evaluator import CompressionEvaluator
from zhiyan_web.context_compress.manager import get_context_compress_manager

logger = logging.getLogger("ZhiYanWeb.ContextCompress")

router = APIRouter(prefix="/api/context-compress", tags=["context-compress"])


# ============= 请求模型 =============

class ConfigUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    strategy: Optional[str] = None
    max_context_messages: Optional[int] = None
    keep_recent: Optional[int] = None
    history_enabled: Optional[bool] = None
    history_interval_hours: Optional[float] = None
    history_min_messages: Optional[int] = None
    history_mode: Optional[str] = None
    history_keep_recent: Optional[int] = None
    max_output_chars: Optional[int] = None
    llm_instruction: Optional[str] = None


class CompressRequest(BaseModel):
    messages: List[Dict]
    strategy: Optional[str] = "auto"
    llm_instruction: Optional[str] = None


class EvaluateRequest(BaseModel):
    messages: List[Dict]
    compressed_text: str


# ============= 配置 =============

@router.get("/config")
async def get_config_api():
    """获取上下文压缩配置"""
    return get_context_compress_manager().get_config()


@router.post("/config")
async def update_config_api(data: ConfigUpdateRequest):
    """更新上下文压缩配置"""
    try:
        patch = {k: v for k, v in data.model_dump().items() if v is not None}
        if "strategy" in patch and patch["strategy"] not in ("auto", "llm", "extractive"):
            raise HTTPException(status_code=400, detail="strategy 必须为 auto/llm/extractive")
        if "history_mode" in patch and patch["history_mode"] not in ("compact", "archive"):
            raise HTTPException(status_code=400, detail="history_mode 必须为 compact/archive")
        config = get_context_compress_manager().update_config(patch)
        return {"status": "success", "config": config}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新上下文压缩配置失败: {e}")
        return {"status": "error", "message": str(e)}


# ============= 压缩操作 =============

@router.post("/compress")
async def compress_api(data: CompressRequest):
    """手动对一组消息执行语义压缩（返回摘要 + 评估）"""
    try:
        manager = get_context_compress_manager()
        compressor = SemanticCompressor(
            llm_instruction=data.llm_instruction,
            max_output_chars=manager.get_config().get("max_output_chars", 2500),
        )
        summary, key_info, used_strategy = await compressor.compress(
            data.messages,
            strategy=data.strategy,
            llm_instruction=data.llm_instruction,
        )
        evaluation = CompressionEvaluator().evaluate(data.messages, summary)
        return {
            "status": "success",
            "summary": summary,
            "key_info": key_info,
            "used_strategy": used_strategy,
            "evaluation": evaluation,
        }
    except Exception as e:
        logger.error(f"压缩失败: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/evaluate")
async def evaluate_api(data: EvaluateRequest):
    """评估一次压缩的效果"""
    try:
        evaluation = CompressionEvaluator().evaluate(data.messages, data.compressed_text)
        return {"status": "success", "evaluation": evaluation}
    except Exception as e:
        logger.error(f"评估失败: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/history/run")
async def run_history_compress_api():
    """手动触发一次历史对话定期压缩"""
    try:
        result = await get_context_compress_manager().run_history_compress_once()
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"历史压缩失败: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/history/{conversation_id}")
async def compress_conversation_api(conversation_id: str):
    """压缩指定的历史对话"""
    try:
        result = await get_context_compress_manager().compress_conversation_history(conversation_id)
        if not result.get("compressed"):
            return {"status": "skipped", "result": result}
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"压缩历史对话失败: {e}")
        return {"status": "error", "message": str(e)}


# ============= 统计与存档 =============

@router.get("/stats")
async def get_stats_api():
    """获取压缩统计"""
    return get_context_compress_manager().get_stats()


@router.get("/archives")
async def list_archives_api(limit: int = 50):
    """列出历史压缩存档"""
    return get_context_compress_manager().list_archives(limit=limit)


@router.get("/archives/{archive_id}")
async def get_archive_api(archive_id: str):
    """获取单个压缩存档详情"""
    archive = get_context_compress_manager().get_archive(archive_id)
    if archive is None:
        raise HTTPException(status_code=404, detail="存档不存在")
    return archive
