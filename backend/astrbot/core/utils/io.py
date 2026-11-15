"""
AstrBot io 工具兼容模块
"""
from __future__ import annotations

import os
import aiohttp
import asyncio


async def download_file(url: str, dest_path: str | None = None) -> str:
    """下载文件
    
    Args:
        url: 文件 URL
        dest_path: 目标路径，如果不提供则使用临时目录
        
    Returns:
        下载后的文件路径
    """
    if dest_path is None:
        import tempfile
        dest_path = os.path.join(tempfile.gettempdir(), os.path.basename(url))
    
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                f.write(await resp.read())
    
    return dest_path


async def download_image_by_url(url: str, download_path: str | None = None, convert_to: str | None = None) -> str:
    """下载图片（兼容 AstrBot）
    
    Args:
        url: 图片 URL
        download_path: 下载路径，如果不提供则使用临时目录
        convert_to: 目标格式（如 "png"），兼容 AstrBot 插件调用
        
    Returns:
        下载后的文件路径
    """
    # 如果指定了 convert_to，替换文件扩展名
    if download_path is None:
        import tempfile
        base_name = os.path.basename(url)
        if convert_to and base_name:
            name, _ = os.path.splitext(base_name)
            base_name = f"{name}.{convert_to.lstrip('.')}"
        download_path = os.path.join(tempfile.gettempdir(), base_name or "downloaded_image")
    
    return await download_file(url, download_path)


__all__ = ["download_file", "download_image_by_url"]