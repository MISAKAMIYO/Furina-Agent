"""
截屏与录屏 API 路由
==================
提供 REST 接口 + WebSocket 实时推送 + 截图/录屏文件访问。
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import service

logger = logging.getLogger("ZhiYanWeb.Screencap.Router")

router = APIRouter(prefix="/api/screencap", tags=["screencap"])


# ── 请求模型 ──

class CaptureRequest(BaseModel):
    mode: str = "full"                      # full / window / region
    region: Optional[Dict[str, int]] = None
    window_title: Optional[str] = None


class CropRequest(BaseModel):
    source: str                             # 源截图路径
    region: Dict[str, int]                  # {left, top, width, height}


class RecordingStartRequest(BaseModel):
    fps: Optional[int] = None
    audio: Optional[bool] = None
    quality: Optional[str] = None


class PathRequest(BaseModel):
    path: str


class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, Any]
    rebind: bool = False                    # 是否立即重绑全局热键


# ── WebSocket 实时事件推送 ──

@router.websocket("/ws")
async def screencap_ws(websocket: WebSocket):
    await websocket.accept()
    service.set_loop(asyncio.get_running_loop())
    service.add_client(websocket)
    try:
        while True:
            try:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text('{"type":"pong"}')
            except WebSocketDisconnect:
                break
            except Exception:
                break
    finally:
        service.remove_client(websocket)


# ── 截图 ──

@router.post("/capture")
async def capture(req: CaptureRequest):
    """截取屏幕"""
    try:
        return service.take_screenshot(
            mode=req.mode,
            region=req.region,
            window_title=req.window_title,
        )
    except Exception as e:
        logger.error(f"截图失败: {e}")
        return {"success": False, "error": str(e)}


@router.post("/crop")
async def crop(req: CropRequest):
    """区域裁剪（区域截图）"""
    try:
        return service.crop_capture(req.source, req.region)
    except Exception as e:
        logger.error(f"裁剪失败: {e}")
        return {"success": False, "error": str(e)}


# ── 录屏 ──

@router.post("/recording/start")
async def recording_start(req: RecordingStartRequest):
    """开始录屏"""
    return service.start_recording(fps=req.fps, audio=req.audio, quality=req.quality)


@router.post("/recording/stop")
async def recording_stop():
    """停止录屏"""
    return service.stop_recording()


@router.get("/recording/status")
async def recording_status():
    """获取录屏状态"""
    return service.get_recording_status()


@router.get("/ffmpeg/status")
async def ffmpeg_status():
    """检查 FFmpeg 可用性"""
    path = service._get_ffmpeg_path()
    return {
        "success": True,
        "available": service.ffmpeg_available(),
        "ffmpeg_path": path,
    }


# ── 历史记录 ──

@router.get("/history")
async def history(
    kind: Optional[str] = Query(None, description="screenshot/video，留空全部"),
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=200),
):
    """列出截图/录屏历史"""
    return service.list_history(kind=kind, page=page, limit=limit)


@router.post("/delete")
async def delete(req: PathRequest):
    """删除截图/录屏文件"""
    return service.delete_capture(req.path)


@router.post("/open")
async def open_file(req: PathRequest):
    """打开文件所在文件夹"""
    return service.open_capture(req.path)


@router.post("/open-direct")
async def open_direct(req: PathRequest):
    """直接打开文件"""
    return service.open_capture_direct(req.path)


@router.post("/clipboard")
async def to_clipboard(req: PathRequest):
    """将截图复制到剪贴板"""
    return service.copy_to_clipboard(req.path)


@router.get("/screen-size")
async def screen_size():
    """获取屏幕分辨率"""
    return {"success": True, **service.get_screen_size()}


# ── 配置 ──

@router.get("/settings")
async def get_settings():
    """获取截屏录屏配置"""
    return {"success": True, "settings": service.get_screencap_settings()}


@router.post("/settings")
async def update_settings(req: SettingsUpdateRequest):
    """更新截屏录屏配置（rebind=true 时即时重绑热键）"""
    try:
        new = service.update_screencap_settings(req.settings, rebind=req.rebind)
        return {"success": True, "settings": new, "message": "配置已保存"}
    except Exception as e:
        logger.error(f"保存截屏录屏配置失败: {e}")
        return {"success": False, "error": str(e)}


# ── 文件访问（带目录穿越防护） ──

_ALLOWED_SUBS = {"screenshots", "videos"}
_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
}


@router.get("/file/{sub}/{filename}")
async def serve_file(sub: str, filename: str):
    """访问截图/录屏文件"""
    if sub not in _ALLOWED_SUBS:
        raise HTTPException(status_code=404, detail="路径无效")
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        raise HTTPException(status_code=404, detail="路径无效")

    directory = os.path.join(service.get_capture_root(), sub)
    full = os.path.normpath(os.path.join(directory, filename))
    if not full.startswith(os.path.normpath(directory) + os.sep):
        raise HTTPException(status_code=404, detail="路径无效")
    if not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="文件不存在")

    ext = os.path.splitext(filename)[1].lower()
    media_type = _MIME.get(ext, "application/octet-stream")
    return FileResponse(full, media_type=media_type)
