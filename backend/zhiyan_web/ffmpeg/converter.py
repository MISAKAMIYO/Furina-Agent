"""
多媒体文件格式转换工具 - 使用 FFmpeg

功能：
- 音频格式转换（mp3, wav, aac, ogg, flac 等）
- 视频格式转换（mp4, webm, avi, mkv, mov 等）
- 查看多媒体文件信息
- 提取音频从视频文件

FFmpeg 路径读取自 settings.json，如果未配置默认使用 data/ffmpeg.exe
"""
from __future__ import annotations

import os
import json
import subprocess
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("ZhiYanWeb.FFmpeg")


def _get_ffmpeg_path() -> str:
    """获取 ffmpeg 可执行文件路径"""
    try:
        from zhiyan_web.config.settings import get_settings
        settings = get_settings()
        custom_path = settings.get("ffmpeg_path", "")
        if custom_path and os.path.exists(custom_path):
            return custom_path
    except Exception:
        pass
    
    # 默认路径
    from zhiyan_web.config import DATA_DIR
    default_path = os.path.join(DATA_DIR, "ffmpeg.exe")
    if os.path.exists(default_path):
        return default_path
    
    # 尝试系统 PATH
    return "ffmpeg"


def _ffmpeg_exists() -> bool:
    """检查 ffmpeg 是否可用"""
    ffmpeg_path = _get_ffmpeg_path()
    if os.path.isabs(ffmpeg_path) and not os.path.exists(ffmpeg_path):
        return False
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def _get_supported_formats() -> Dict:
    """获取支持的格式列表"""
    audio_formats = [
        {"ext": "mp3", "name": "MP3", "desc": "MPEG Audio Layer III"},
        {"ext": "wav", "name": "WAV", "desc": "Waveform Audio (无损)"},
        {"ext": "aac", "name": "AAC", "desc": "Advanced Audio Coding"},
        {"ext": "ogg", "name": "OGG", "desc": "Ogg Vorbis"},
        {"ext": "flac", "name": "FLAC", "desc": "Free Lossless Audio Codec (无损)"},
        {"ext": "m4a", "name": "M4A", "desc": "MPEG-4 Audio"},
        {"ext": "opus", "name": "Opus", "desc": "Opus 高效压缩"},
        {"ext": "alac", "name": "ALAC", "desc": "Apple Lossless 无损"},
    ]
    
    video_formats = [
        {"ext": "mp4", "name": "MP4", "desc": "MPEG-4 (通用)"},
        {"ext": "webm", "name": "WebM", "desc": "WebM 开源"},
        {"ext": "mkv", "name": "MKV", "desc": "Matroska (支持多轨)"},
        {"ext": "avi", "name": "AVI", "desc": "Audio Video Interleave"},
        {"ext": "mov", "name": "MOV", "desc": "QuickTime"},
        {"ext": "flv", "name": "FLV", "desc": "Flash Video"},
        {"ext": "wmv", "name": "WMV", "desc": "Windows Media Video"},
        {"ext": "gif", "name": "GIF", "desc": "Animated GIF"},
    ]
    
    return {
        "audio": audio_formats,
        "video": video_formats,
    }


def get_converter_status() -> Dict:
    """获取转换器状态"""
    ffmpeg_path = _get_ffmpeg_path()
    exists = _ffmpeg_exists()
    
    formats = _get_supported_formats()
    
    return {
        "success": True,
        "available": exists,
        "ffmpeg_path": ffmpeg_path,
        "audio_formats": formats["audio"],
        "video_formats": formats["video"],
        "message": f"FFmpeg {'可用' if exists else '不可用'}: {ffmpeg_path}",
    }


def get_file_info(input_path: str) -> Dict:
    """获取多媒体文件信息"""
    ffmpeg_path = _get_ffmpeg_path()
    if not _ffmpeg_exists():
        return {"success": False, "error": "FFmpeg 不可用"}
    
    if not os.path.exists(input_path):
        return {"success": False, "error": f"文件不存在: {input_path}"}
    
    try:
        # 使用 ffprobe 获取信息
        ffprobe_path = _get_ffmpeg_path().replace("ffmpeg", "ffprobe")
        if not os.path.exists(ffprobe_path):
            ffprobe_path = "ffprobe"
        cmd = [
            ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            input_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {"success": False, "error": f"ffprobe 失败: {result.stderr}"}
        data = json.loads(result.stdout)
        return {
            "success": True,
            "path": input_path,
            "format": data.get("format", {}),
            "streams": data.get("streams", []),
            "duration": float(data.get("format", {}).get("duration", "0")),
            "size": int(data.get("format", {}).get("size", "0")),
        }
    except Exception as e:
        logger.error(f"获取文件信息失败: {e}")
        return {"success": False, "error": str(e)}


def convert_file(
    input_path: str,
    output_ext: str,
    audio_bitrate: Optional[int] = None,
    video_bitrate: Optional[int] = None,
) -> Dict:
    """转换文件格式

    Args:
        input_path: 输入文件路径（必须在 Agent 工作区内）
        output_ext: 输出格式扩展名 (mp3, wav, mp4 等)
        audio_bitrate: 音频比特率（kbps）
        video_bitrate: 视频比特率（kbps）

    Returns:
        转换结果字典
    """
    ffmpeg_path = _get_ffmpeg_path()
    if not _ffmpeg_exists():
        return {"success": False, "error": "FFmpeg 不可用，请检查配置"}
    
    if not os.path.exists(input_path):
        return {"success": False, "error": f"输入文件不存在: {input_path}"}
    
    # 输出路径 = 输入路径替换扩展名
    dirname = os.path.dirname(input_path)
    basename = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(dirname, f"{basename}.{output_ext}")
    
    # 构建命令
    cmd = [ffmpeg_path, "-y", "-i", input_path]
    
    # 添加比特率参数
    if audio_bitrate:
        cmd.extend(["-b:a", f"{audio_bitrate}k"])
    if video_bitrate:
        cmd.extend(["-b:v", f"{video_bitrate}k"])
    
    # 输出路径
    cmd.append(output_path)
    
    logger.info(f"开始转换: {input_path} -> {output_path}")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 分钟超时
        )
        
        if result.returncode != 0:
            logger.error(f"转换失败: {result.stderr}")
            return {
                "success": False,
                "error": "转换失败",
                "ffmpeg_output": result.stderr[-2000:],
            }
        
        # 获取输出文件信息
        output_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        input_info = get_file_info(input_path)
        
        return {
            "success": True,
            "input_path": input_path,
            "output_path": output_path,
            "output_ext": output_ext,
            "output_size": output_size,
            "input_info": input_info if input_info.get("success", False) else None,
            "message": f"转换完成: {os.path.basename(input_path)} -> {os.path.basename(output_path)}",
        }
    except subprocess.TimeoutExpired:
        logger.error("转换超时")
        return {"success": False, "error": "转换超时（超过 5 分钟）"}
    except Exception as e:
        logger.error(f"转换异常: {e}")
        return {"success": False, "error": str(e)}


def extract_audio(
    input_path: str,
    output_ext: str = "mp3",
    audio_bitrate: Optional[int] = 192,
) -> Dict:
    """从视频文件提取音频"""
    ffmpeg_path = _get_ffmpeg_path()
    if not _ffmpeg_exists():
        return {"success": False, "error": "FFmpeg 不可用，请检查配置"}
    
    if not os.path.exists(input_path):
        return {"success": False, "error": f"输入文件不存在: {input_path}"}
    
    # 输出路径
    dirname = os.path.dirname(input_path)
    basename = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(dirname, f"{basename}.{output_ext}")
    
    cmd = [
        ffmpeg_path, "-y", "-i", input_path,
        "-vn",  # 不保留视频
        "-b:a", f"{audio_bitrate}k",
        output_path,
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            return {
                "success": False,
                "error": "提取失败",
                "ffmpeg_output": result.stderr[-2000:],
            }
        
        output_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        return {
            "success": True,
            "input_path": input_path,
            "output_path": output_path,
            "output_ext": output_ext,
            "output_size": output_size,
            "message": f"音频提取完成: {os.path.basename(input_path)} -> {os.path.basename(output_path)}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_convert_history(workspace_path: str) -> List[Dict]:
    """列出工作区内所有可转换文件"""
    if not workspace_path or not os.path.exists(workspace_path):
        return []
    
    supported_exts = set()
    formats = _get_supported_formats()
    for f in formats["audio"]:
        supported_exts.add(f["ext"].lower())
    for f in formats["video"]:
        supported_exts.add(f["ext"].lower())
    
    result = []
    try:
        for root, dirs, files in os.walk(workspace_path):
            for filename in files:
                ext = os.path.splitext(filename)[1].lower().lstrip(".")
                if ext in supported_exts:
                    filepath = os.path.join(root, filename)
                    try:
                        stat = os.stat(filepath)
                        result.append({
                            "path": filepath,
                            "filename": filename,
                            "ext": ext,
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                        })
                    except Exception:
                        pass
            if len(result) >= 200:  # 最多列出 200 个
                break
    except Exception as e:
        logger.error(f"列出文件失败: {e}")
    
    result.sort(key=lambda x: x["modified"], reverse=True)
    return result
