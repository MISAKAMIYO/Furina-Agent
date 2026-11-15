"""
本地文件快捷查看/打开 API 路由
==============================
前端在 AI 回复中检测到本地文件引用时，点击文件卡片弹出详情并支持快捷打开。

接口:
- POST /api/files/info   - 文件/目录详情（含文本预览、图片预览地址、目录子项）
- POST /api/files/open   - 快捷打开：open=默认程序打开；explorer=打开所在文件夹
- GET  /api/files/raw    - 返回文件原始内容（供图片/音频/视频预览）
"""
from __future__ import annotations

import datetime
import mimetypes
import os
import subprocess
from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/files", tags=["files"])

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".log", ".json", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".html", ".htm", ".css", ".c", ".cpp", ".h", ".hpp", ".cs", ".java", ".go", ".rs",
    ".swift", ".kt", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".csv",
    ".sql", ".sh", ".bat", ".cmd", ".ps1", ".lua", ".rb", ".php", ".env", ".gitignore",
    ".vue", ".svelte", ".astro", ".json5", ".ipynb",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".avif", ".tiff"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".amr"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".ts", ".m4v"}
PDF_EXTS = {".pdf"}
PREVIEW_TEXT_LIMIT = 3000


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


def _resolve(path: str) -> str:
    """规整并校验路径：必须是绝对路径且已存在。"""
    if not path:
        raise HTTPException(status_code=400, detail="缺少路径")
    p = os.path.abspath(os.path.expanduser(str(path).strip().strip('"').strip("'")))
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    return p


def _kind_of(ext: str, p: str) -> str:
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in TEXT_EXTS:
        return "text"
    return "binary"


def _read_text_preview(p: str) -> str:
    """尽量以 UTF-8 读取文件开头作为文本预览；非文本/过大则回退为空。"""
    try:
        if os.path.getsize(p) > 5 * 1024 * 1024:
            return ""
        with open(p, "r", encoding="utf-8", errors="strict") as f:
            return f.read(PREVIEW_TEXT_LIMIT)
    except Exception:
        try:
            with open(p, "r", encoding="gb18030", errors="strict") as f:
                return f.read(PREVIEW_TEXT_LIMIT)
        except Exception:
            return ""


@router.post("/info")
async def file_info(body: dict = Body(...)):
    """获取文件/目录详情（未找到时返回 ok=False，供前端友好提示）。"""
    raw = str(body.get("path") or "").strip()
    try:
        p = _resolve(raw)
    except HTTPException as e:
        return {"ok": False, "message": e.detail}
    is_dir = os.path.isdir(p)
    name = os.path.basename(p.rstrip("/\\")) or p
    ext = os.path.splitext(name)[1].lower() if not is_dir else ""
    try:
        st = os.stat(p)
        mtime = st.st_mtime
        size = st.st_size
    except OSError as e:
        return {"ok": False, "message": f"读取文件信息失败: {e}"}
    kind = "dir" if is_dir else _kind_of(ext, p)
    info = {
        "ok": True,
        "exists": True,
        "is_dir": is_dir,
        "name": name,
        "ext": ext,
        "kind": kind,
        "path": p,
        "size": size,
        "size_human": _human_size(size) if not is_dir else "",
        "mtime": mtime,
        "mtime_human": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "preview_url": (f"/api/files/raw?path={quote(p)}"
                        if (kind in ("image", "audio", "video", "pdf") and not is_dir) else ""),
    }
    if is_dir:
        try:
            entries = sorted(os.listdir(p))
        except OSError:
            entries = []
        info["children"] = entries[:200]
        info["children_count"] = len(entries)
    elif kind == "text":
        info["preview_text"] = _read_text_preview(p)
    return info


@router.get("/raw")
async def file_raw(path: str = Query(..., description="绝对文件路径")):
    """返回文件原始内容（图片/音频/视频预览）。"""
    try:
        p = _resolve(path)
    except HTTPException as e:
        raise e
    if os.path.isdir(p):
        raise HTTPException(status_code=400, detail="目录不能作为文件内容预览")
    mime = mimetypes.guess_type(p)[0] or "application/octet-stream"
    return FileResponse(p, media_type=mime)


@router.post("/open")
async def file_open(body: dict = Body(...)):
    """快捷打开：mode=open 默认程序打开；mode=explorer 打开所在文件夹。"""
    raw = str(body.get("path") or "").strip()
    mode = str(body.get("mode") or "open").strip().lower()
    try:
        p = _resolve(raw)
    except HTTPException as e:
        return {"ok": False, "message": e.detail}
    try:
        if os.name == "nt":
            if mode == "explorer":
                if os.path.isdir(p):
                    subprocess.Popen(["explorer.exe", os.path.normpath(p)])
                else:
                    subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(p)])
            else:
                os.startfile(p)  # 文件与目录均可
        else:
            if mode == "explorer":
                target = os.path.dirname(p) if os.path.isfile(p) else p
            else:
                target = p
            subprocess.Popen(["xdg-open", target])
        verb = "已打开所在文件夹: " if mode == "explorer" else "已打开: "
        return {"ok": True, "message": verb + p}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "message": f"打开失败: {exc}"}
