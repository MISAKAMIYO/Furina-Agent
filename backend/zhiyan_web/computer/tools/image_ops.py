"""
图片展示工具 - 让 AI 向用户展示本地图片

用于部分多模态大模型（它自己拥有"看见"图片的能力可跳过）以及
不支持多模态的模型：当 AI 需要向用户展示一张指定的本地图片时，
调用本工具将图片路径规范化为可通过 HTTP 访问的地址，并在聊天界面呈现。

能力：
- 按指定本地路径展示图片（支持常见图片格式）
- 返回可访问的 URL、文件大小、宽高信息
- 多参数别名兼容（path / image_path / file）
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional, Any

logger = logging.getLogger("ZhiYanWeb.Computer.ImageOps")

# 允许展示的图片扩展名
_ALLOWED_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
    ".svg", ".ico", ".tiff", ".tif", ".heic", ".avif",
}

# 各扩展名对应的 MIME 类型
_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".heic": "image/heic",
    ".avif": "image/avif",
}


def _is_valid_image_path(path: str) -> bool:
    """校验是否为存在的图片文件"""
    if not path or not isinstance(path, str):
        return False
    if not os.path.isfile(path):
        return False
    ext = os.path.splitext(path)[1].lower()
    return ext in _ALLOWED_EXTS


def _get_image_size(path: str) -> Dict[str, int]:
    """尝试读取图片实际像素尺寸（无 PIL 时返回空）"""
    try:
        from PIL import Image
        with Image.open(path) as img:
            return {"width": img.width, "height": img.height}
    except Exception:
        return {}


def show_image(
    path: str = "",
    image_path: str = "",
    file_path: str = "",
    caption: str = "",
) -> Dict[str, Any]:
    """向用户展示指定路径下的本地图片。

    Args:
        path: 图片文件的本地绝对路径（主参数）
        image_path: 图片路径（别名参数）
        file_path: 图片路径（别名参数）
        caption: 可选的图片说明文字

    Returns:
        Dict: {success, path, url, name, size, width, height, ...}
    """
    target = path or image_path or file_path or ""
    target = (target or "").strip().strip('"').strip()

    if not target:
        return {"success": False, "error": "未提供图片路径（可传 path / image_path / file_path）"}

    # 展开 ~ 与相对路径
    try:
        abspath = os.path.abspath(os.path.expanduser(target))
    except Exception as e:
        return {"success": False, "error": f"路径解析失败: {e}"}

    if not _is_valid_image_path(abspath):
        return {
            "success": False,
            "error": (
                f"图片不存在或不是受支持的图片格式: {target}\n"
                f"支持的格式: {', '.join(sorted(e for e in _ALLOWED_EXTS))}"
            ),
        }

    try:
        size_bytes = os.path.getsize(abspath)
    except Exception:
        size_bytes = 0

    dim = _get_image_size(abspath)
    ext = os.path.splitext(abspath)[1].lower()

    # 生成供前端 <img> 加载的访问地址
    import urllib.parse
    encoded = urllib.parse.quote(abspath)
    url = f"/api/computer/image/view?path={encoded}"

    return {
        "success": True,
        "path": abspath,
        "name": os.path.basename(abspath),
        "url": url,
        "size": size_bytes,
        "mime": _MIME_MAP.get(ext, "image/png"),
        "caption": caption,
        **dim,
        "message": f"图片已展示: {os.path.basename(abspath)}",
    }


def _resolve_view_path(raw_path: str) -> Optional[str]:
    """安全解析 HTTP 视图请求中的图片路径（含路径遍历防护）"""
    if not raw_path or not isinstance(raw_path, str):
        return None
    try:
        abspath = os.path.realpath(os.path.abspath(os.path.expanduser(raw_path)))
    except Exception:
        return None
    if not _is_valid_image_path(abspath):
        return None
    return abspath