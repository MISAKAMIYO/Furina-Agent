"""
音频元数据提取器

使用 mutagen 库提取音频文件的元数据（标题、艺术家、专辑、时长、格式等）。
"""

from __future__ import annotations

import os
import logging
from typing import Any

logger = logging.getLogger("ZhiYanWeb")


class MetadataExtractor:
    """音频元数据提取器"""

    @staticmethod
    def extract(filepath: str) -> dict[str, Any]:
        """
        提取音频文件的元数据

        参数:
            filepath: 音频文件完整路径

        返回:
            dict: 包含 title, artist, album, duration, format, bitrate, sample_rate 等字段
        """
        if not os.path.exists(filepath):
            logger.warning(f"文件不存在: {filepath}")
            return {"title": "", "artist": "", "album": "", "duration": 0, "format": ""}

        try:
            from mutagen import File as MutagenFile
            from mutagen.mp3 import MPEGInfo
            from mutagen.flac import FLAC
            from mutagen.oggvorbis import OggVorbis
            from mutagen.mp4 import MP4

            audio = MutagenFile(filepath, easy=True)
            if audio is None:
                logger.warning(f"无法读取音频文件: {filepath}")
                return MetadataExtractor._fallback_info(filepath)

            metadata: dict[str, Any] = {
                "title": "",
                "artist": "",
                "album": "",
                "duration": 0,
                "format": os.path.splitext(filepath)[1].lstrip(".").upper(),
                "bitrate": 0,
                "sample_rate": 0,
                "channels": 0,
            }

            # 提取标签信息
            if hasattr(audio, "tags") and audio.tags:
                tags = audio.tags

                # 标题
                title = MetadataExtractor._get_tag(tags, "title")
                if title:
                    metadata["title"] = title

                # 艺术家
                artist = MetadataExtractor._get_tag(tags, "artist")
                if artist:
                    metadata["artist"] = artist

                # 专辑
                album = MetadataExtractor._get_tag(tags, "album")
                if album:
                    metadata["album"] = album

            # 提取技术信息
            duration = audio.info.length if hasattr(audio, "info") and audio.info else 0
            metadata["duration"] = int(duration)

            if hasattr(audio, "info") and audio.info:
                info = audio.info
                if hasattr(info, "bitrate"):
                    metadata["bitrate"] = info.bitrate
                if hasattr(info, "sample_rate"):
                    metadata["sample_rate"] = info.sample_rate
                if hasattr(info, "channels"):
                    metadata["channels"] = info.channels

            logger.debug(
                f"元数据提取成功: {metadata.get('title', '')} - "
                f"{metadata.get('artist', '')} "
                f"({MetadataExtractor.format_duration(metadata['duration'])})"
            )
            return metadata

        except Exception as e:
            logger.error(f"提取元数据失败 {filepath}: {e}")
            return MetadataExtractor._fallback_info(filepath)

    @staticmethod
    def _get_tag(tags, key: str) -> str | None:
        """从标签中安全获取文本值"""
        try:
            values = tags.get(key, [])
            if values and len(values) > 0:
                return str(values[0]).strip()
        except Exception:
            pass
        return None

    @staticmethod
    def _fallback_info(filepath: str) -> dict[str, Any]:
        """当元数据提取失败时，返回基于文件名的基本信息"""
        filename = os.path.basename(filepath)
        name_no_ext = os.path.splitext(filename)[0]
        ext = os.path.splitext(filepath)[1].lstrip(".").upper()

        # 尝试从文件名猜测标题和艺术家（"艺术家 - 标题.mp3" 格式）
        title = name_no_ext
        artist = ""
        if " - " in name_no_ext:
            parts = name_no_ext.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()

        return {
            "title": title,
            "artist": artist,
            "album": "",
            "duration": 0,
            "format": ext,
            "bitrate": 0,
            "sample_rate": 0,
            "channels": 0,
        }

    @staticmethod
    def format_duration(seconds: int) -> str:
        """格式化时长（秒 -> MM:SS）"""
        if seconds <= 0:
            return "0:00"
        mins = seconds // 60
        secs = seconds % 60
        return f"{mins}:{secs:02d}"

    @staticmethod
    def format_size(size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / 1024 / 1024:.1f} MB"
        else:
            return f"{size_bytes / 1024 / 1024 / 1024:.2f} GB"

    @staticmethod
    def is_supported_format(filepath: str) -> bool:
        """检查文件是否为支持的音频格式"""
        ext = os.path.splitext(filepath)[1].lower()
        supported = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma"}
        return ext in supported