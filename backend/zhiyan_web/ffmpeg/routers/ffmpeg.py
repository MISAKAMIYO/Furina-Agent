"""
FFmpeg 文件格式转换 - FastAPI 路由
"""
from __future__ import annotations

import os
import logging
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from zhiyan_web.config import DATA_DIR
from zhiyan_web.ffmpeg.converter import (
    get_converter_status,
    get_file_info,
    convert_file,
    extract_audio,
    list_convert_history,
    _get_supported_formats,
)

logger = logging.getLogger("ZhiYanWeb.FFmpegRouter")

router = APIRouter(prefix="/api/ffmpeg", tags=["ffmpeg"])


class ConvertRequest(BaseModel):
    input_path: str
    output_ext: str
    audio_bitrate: Optional[int] = None
    video_bitrate: Optional[int] = None


class ExtractAudioRequest(BaseModel):
    input_path: str
    output_ext: str = "mp3"
    audio_bitrate: int = 192


@router.get("/status")
async def api_get_status():
    """获取 FFmpeg 转换器状态"""
    return get_converter_status()


@router.get("/formats")
async def api_get_formats():
    """获取支持的格式列表"""
    formats = _get_supported_formats()
    return {"success": True, "formats": formats}


@router.get("/file-info")
async def api_get_file_info(
    path: str = Query(..., description="文件路径"),
):
    """获取多媒体文件信息"""
    if not path:
        raise HTTPException(status_code=400, detail="文件路径不能为空")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
    return get_file_info(path)


@router.post("/convert")
async def api_convert_file(req: ConvertRequest):
    """转换文件格式"""
    if not req.input_path:
        raise HTTPException(status_code=400, detail="输入文件路径不能为空")
    if not os.path.exists(req.input_path):
        raise HTTPException(status_code=404, detail=f"输入文件不存在: {req.input_path}")
    result = convert_file(
        input_path=req.input_path,
        output_ext=req.output_ext,
        audio_bitrate=req.audio_bitrate,
        video_bitrate=req.video_bitrate,
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "转换失败"))
    return result


@router.post("/extract-audio")
async def api_extract_audio(req: ExtractAudioRequest):
    """从视频提取音频"""
    if not req.input_path:
        raise HTTPException(status_code=400, detail="输入文件路径不能为空")
    if not os.path.exists(req.input_path):
        raise HTTPException(status_code=404, detail=f"输入文件不存在: {req.input_path}")
    result = extract_audio(
        input_path=req.input_path,
        output_ext=req.output_ext,
        audio_bitrate=req.audio_bitrate,
    )
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "提取失败"))
    return result


@router.get("/list-files")
async def api_list_files(
    path: str = Query("", description="工作区路径"),
):
    """列出可转换的文件"""
    if not path or not os.path.exists(path):
        return {"success": True, "files": []}
    files = list_convert_history(path)
    return {"success": True, "files": files}


@router.post("/upload")
async def api_upload_file(file: UploadFile = File(...)):
    """上传多媒体文件"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="未选择文件")

    # 保存到临时目录
    upload_dir = os.path.join(DATA_DIR, "uploads", "converter")
    os.makedirs(upload_dir, exist_ok=True)

    ext = os.path.splitext(file.filename)[1] or ""
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(upload_dir, unique_name)

    try:
        content = await file.read()
        with open(save_path, "wb") as f:
            f.write(content)
        logger.info(f"文件已上传: {save_path} ({len(content)} bytes)")
        return {"success": True, "path": save_path, "filename": file.filename}
    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)}")