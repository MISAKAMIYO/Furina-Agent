"""
插件下载文件浏览 / 下载路由

供前端在聊天界面直接访问插件保存的本地文件（如 JM 本子的打包 zip/pdf 与图片目录）。
Web 平台下插件无法直接下发本地文件（Comp.File 仅被字符串化），因此提供：

- GET /api/plugins/jm/list   - 浏览某目录下文件列表（JSON）
- GET /api/plugins/jm/file   - 下载 / 查看某个文件

安全：所有路径均解析到固定根目录（JM 下载目录）内，防御目录穿越。
"""
from __future__ import annotations

import os
import logging
from urllib.parse import unquote
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from zhiyan_web.config import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.PluginFiles")

router = APIRouter(prefix="/api/plugins/jm", tags=["plugin-files"])

# JM 插件下载根目录（download_dir 相对路径基于插件 data_dir 解析）
JM_DOWNLOAD_DIR = os.path.abspath(
    os.path.join(DATA_DIR, "plugins_data", "jm_cosmos2", "downloads")
)


def _safe_resolve(rel_path: str):
    """将相对路径解析到下载目录内，防止目录穿越"""
    rel = unquote(rel_path or "").lstrip("/\\")
    base = os.path.abspath(JM_DOWNLOAD_DIR)
    target = os.path.abspath(os.path.join(base, rel))
    common = os.path.commonpath([base, target])
    if common != base:
        raise HTTPException(status_code=400, detail="路径不合法")
    return target


def _size_str(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


@router.get("/list")
async def list_plugin_files(
    path: str = Query("", description="下载目录下的相对路径，空表示根目录"),
):
    """列出指定目录下的文件与子目录"""
    base = os.path.abspath(JM_DOWNLOAD_DIR)
    target = _safe_resolve(path)

    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="目录不存在")
    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail="目标不是目录")

    entries = []
    try:
        for name in sorted(os.listdir(target)):
            full = os.path.join(target, name)
            rel = os.path.relpath(full, base).replace("\\", "/")
            if os.path.isdir(full):
                try:
                    size = sum(
                        os.path.getsize(os.path.join(r, f))
                        for r, _d, files in os.walk(full)
                        for f in files
                    )
                except OSError:
                    size = 0
                entries.append(
                    {
                        "name": name,
                        "type": "dir",
                        "size": size,
                        "size_str": _size_str(size),
                        "rel": rel,
                    }
                )
            else:
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                entries.append(
                    {
                        "name": name,
                        "type": "file",
                        "size": size,
                        "size_str": _size_str(size),
                        "rel": rel,
                    }
                )
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取目录失败: {e}")

    return {"path": path, "entries": entries}


@router.get("/file")
async def get_plugin_file(
    path: str = Query("", description="下载目录下的相对路径"),
):
    """下载 / 查看指定文件"""
    target = _safe_resolve(path)
    if not os.path.exists(target) or not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="文件不存在")

    ext = os.path.splitext(target)[1].lower()
    # 图片媒体类型可直接内联预览；压缩包等强制下载
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
    }
    media_type = media_types.get(ext)
    if media_type:
        return FileResponse(target, media_type=media_type)
    # 其他（zip/7z 等）以附件形式下载
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=os.path.basename(target),
    )