"""
封面搜索服务
负责根据歌曲标题查找专辑封面图片

核心功能:
- 优先读取配置/playlist中保存的封面路径
- 根据歌曲标题匹配封面图片
- 从音频文件元数据提取封面
- 支持多种图片格式（JPG, JPEG, PNG, WebP）
"""
from __future__ import annotations

import os
import re
import logging
from typing import Optional

from zhiyan_web.config.settings import COVERS_DIR, DATA_DIR

logger = logging.getLogger("ZhiYanWeb")


class CoverFinder:
    """
    封面查找器
    
    多级查找策略:
    1. 从播放列表配置中读取已保存的封面路径
    2. 根据歌曲标题模糊匹配封面文件
    3. 从音频文件元数据提取封面
    """
    
    @staticmethod
    def find_cover(song_title: str, song_filename: str = "") -> Optional[str]:
        """
        根据歌曲标题查找封面图片（多级策略）
        
        参数:
            song_title: 歌曲标题
            song_filename: 歌曲文件名（用于元数据提取和文件名匹配）
        
        返回:
            str or None: 封面图片的 API 路径，未找到返回 None
        """
        # 策略1: 从播放列表配置中读取封面路径
        cover_path = CoverFinder._find_from_config(song_title)
        if cover_path:
            return cover_path
        
        # 策略2: 按歌曲标题模糊匹配封面文件
        cover_path = CoverFinder._find_by_title(song_title)
        if cover_path:
            return cover_path
        
        # 策略3: 按歌曲文件名匹配封面文件（新增）
        if song_filename:
            cover_path = CoverFinder._find_by_filename(song_filename)
            if cover_path:
                return cover_path
        
        # 策略4: 从音频文件元数据提取
        if song_filename:
            cover_path = CoverFinder._extract_from_metadata(song_filename)
            if cover_path:
                return cover_path
        
        return None
    
    @staticmethod
    def _find_from_config(song_title: str) -> Optional[str]:
        """
        从播放列表配置中查找封面路径
        
        扫描 playlist.json 中是否有该歌曲的封面路径配置
        """
        import json
        playlist_path = os.path.join(DATA_DIR, 'playlist.json')
        if not os.path.exists(playlist_path):
            return None
        
        try:
            with open(playlist_path, 'r', encoding='utf-8') as f:
                playlist_data = json.load(f)
            
            playlist = playlist_data.get('playlist', [])
            for item in playlist:
                if isinstance(item, dict):
                    title = item.get('title', '')
                    cover = item.get('cover', '')
                    if title and cover and title.lower() == song_title.lower():
                        # 如果封面是本地文件路径，转为 API 路径
                        if cover.startswith('/api/music/cover/'):
                            return cover
                        if os.path.exists(cover):
                            filename = os.path.basename(cover)
                            dest = os.path.join(COVERS_DIR, filename)
                            if not os.path.exists(dest):
                                import shutil
                                try:
                                    shutil.copy2(cover, dest)
                                except Exception:
                                    pass
                            return f"/api/music/cover/{filename}"
        except Exception as e:
            logger.debug(f"从配置读取封面失败: {e}")
        
        return None
    
    @staticmethod
    def _find_by_title(song_title: str) -> Optional[str]:
        """
        根据歌曲标题模糊匹配封面文件
        
        支持:
        - 精确匹配: 标题.jpg
        - 包含匹配: 标题_cover.jpg
        - 正则匹配: 移除特殊字符后匹配
        """
        if not os.path.exists(COVERS_DIR):
            return None
        
        song_clean = CoverFinder._clean_title(song_title)
        
        for filename in os.listdir(COVERS_DIR):
            if not filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                continue
            
            name_clean = CoverFinder._clean_title(os.path.splitext(filename)[0])
            
            # 精确匹配
            if song_clean == name_clean:
                logger.info(f"封面精确匹配: {filename}")
                return f"/api/music/cover/{filename}"
            
            # 包含匹配（标题包含封面名或封面名包含标题）
            if (song_clean in name_clean or name_clean in song_clean) and len(song_clean) > 2:
                logger.info(f"封面包含匹配: {filename}")
                return f"/api/music/cover/{filename}"
        
        # 正则匹配：尝试多种模式
        for filename in os.listdir(COVERS_DIR):
            if not filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                continue
            
            name_clean = CoverFinder._clean_title(os.path.splitext(filename)[0])
            
            # 移除常见后缀再匹配
            no_suffix = re.sub(r'[-_]\s*(cover|album|art|封面|专辑|单曲)$', '', name_clean, flags=re.IGNORECASE).strip()
            if no_suffix and (song_clean in no_suffix or no_suffix in song_clean):
                logger.info(f"封面正则匹配: {filename}")
                return f"/api/music/cover/{filename}"
        
        return None
    
    @staticmethod
    def _find_by_filename(song_filename: str) -> Optional[str]:
        """
        根据歌曲文件名匹配封面文件（新增策略）
        
        将音频文件名（不含扩展名）与封面文件名进行匹配，
        覆盖更多歌曲名与封面文件不一致的情况。
        """
        if not song_filename or not os.path.exists(COVERS_DIR):
            return None
        
        # 用音频文件名的主体（不含扩展名）来匹配
        base_name = os.path.splitext(song_filename)[0]
        file_clean = CoverFinder._clean_title(base_name)
        
        if not file_clean:
            return None
        
        for filename in os.listdir(COVERS_DIR):
            if not filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                continue
            
            name_clean = CoverFinder._clean_title(os.path.splitext(filename)[0])
            
            # 精确匹配
            if file_clean == name_clean:
                logger.info(f"封面文件名精确匹配: {filename} <- {song_filename}")
                return f"/api/music/cover/{filename}"
            
            # 包含匹配
            if (file_clean in name_clean or name_clean in file_clean) and len(file_clean) > 2:
                logger.info(f"封面文件名包含匹配: {filename} <- {song_filename}")
                return f"/api/music/cover/{filename}"
        
        return None
    
    @staticmethod
    def _extract_from_metadata(song_filename: str) -> Optional[str]:
        """
        从音频文件元数据提取封面
        
        使用 mutagen 库读取音频文件内嵌的封面图片
        """
        try:
            import mutagen
            from mutagen.mp3 import MP3
            from mutagen.flac import FLAC
            from mutagen.mp4 import MP4
            from mutagen.id3 import ID3, APIC
        except ImportError:
            logger.debug("mutagen 未安装，跳过元数据封面提取")
            return None
        
        from zhiyan_web.config.settings import SONGS_DIR
        
        # 在所有可能的歌曲目录中查找文件
        old_songs_dir = os.path.join(os.path.dirname(DATA_DIR), 'ZhiYan agent', 'data', 'Songs')
        search_paths = [
            SONGS_DIR,
            os.path.join(DATA_DIR, 'plugins_data', 'astrbot_plugin_music', 'songs'),
            os.path.join(DATA_DIR, 'Songs'),
            old_songs_dir,
        ]
        
        filepath = ""
        for path in search_paths:
            if os.path.exists(path):
                candidate = os.path.join(path, song_filename)
                if os.path.exists(candidate):
                    filepath = candidate
                    break
        
        if not filepath or not os.path.exists(filepath):
            return None
        
        try:
            ext = os.path.splitext(filepath)[1].lower()
            image_data = None
            
            if ext == '.mp3':
                audio = MP3(filepath)
                if audio.tags:
                    for tag in audio.tags.values():
                        if isinstance(tag, APIC):
                            image_data = tag.data
                            break
            elif ext == '.flac':
                audio = FLAC(filepath)
                if audio.pictures:
                    image_data = audio.pictures[0].data
            elif ext == '.m4a':
                audio = MP4(filepath)
                if 'covr' in audio:
                    image_data = audio['covr'][0]
            
            if image_data:
                # 保存封面到 COVERS_DIR
                os.makedirs(COVERS_DIR, exist_ok=True)
                cover_filename = os.path.splitext(song_filename)[0] + '.jpg'
                cover_path = os.path.join(COVERS_DIR, cover_filename)
                
                with open(cover_path, 'wb') as f:
                    f.write(image_data)
                
                logger.info(f"从元数据提取封面: {cover_filename}")
                return f"/api/music/cover/{cover_filename}"
        
        except Exception as e:
            logger.debug(f"元数据封面提取失败: {e}")
        
        return None
    
    @staticmethod
    def _clean_title(title: str) -> str:
        """清理标题，用于匹配"""
        title = title.lower().strip()
        # 移除特殊字符
        title = re.sub(r'[^\w\u4e00-\u9fff]', '', title)
        # 移除常见无意义后缀
        title = re.sub(r'(cover|karaoke|instrumental|offvocal|remix|伴奏|翻唱|纯音乐)$', '', title)
        return title.strip()