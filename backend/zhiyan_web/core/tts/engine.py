"""
TTS 引擎模块
提供语音合成功能，支持 GPT-SoVITS、Edge TTS 和 pyttsx3 三种引擎

核心功能:
- 自动选择可用的 TTS 引擎
- 支持情绪检测和合成
- 异步合成接口
- 流式合成支持
- 完整的参数配置支持
- 缓存机制优化性能
- 文本预处理和清洗
"""
from __future__ import annotations

import os
import re
import asyncio
import threading
import logging
import time
import hashlib
import tempfile

from zhiyan_web.config.settings import TTS_DIR

logger = logging.getLogger("ZhiYanWeb")


class TTSEngine:
    """
    TTS 语音合成引擎
    
    支持多种 TTS 引擎：
    - GPT-SoVITS: 基于 AI 的语音合成引擎（高质量）
    - Edge TTS: Microsoft Edge 提供的免费 TTS 服务（免费）
    - pyttsx3: 系统内置 TTS（离线）
    """
    
    def __init__(self, engine_type: str = "auto", voice: str = "zh-CN-XiaoxiaoNeural", 
                 rate: int = 200, volume: float = 1.0, config: dict = None):
        """
        初始化 TTS 引擎
        
        参数:
            engine_type: 引擎类型（auto, gpt_sovits, edge, pyttsx3, none）
            voice: 语音名称
            rate: 语速（每分钟词数）
            volume: 音量（0.0-1.0）
            config: 详细配置字典
        """
        self.engine_type = engine_type.lower().replace("-", "_")
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.pitch = 0
        self.language = "zh"
        self._output_dir = TTS_DIR
        self._last_synthesize_time = 0
        self._min_interval = 0.1
        self._auto_clean = True
        self._max_length = 500
        self._cache_enabled = True
        self._cache = {}
        self._cache_max_size = 100
        
        # 加载配置
        if config and isinstance(config, dict):
            self._load_config(config)
        else:
            self._init_default_config()
        
        # 检查 Edge TTS 是否可用
        self._has_edge_tts = False
        try:
            import edge_tts
            self._has_edge_tts = True
        except Exception as e:
            logger.debug(f"Edge TTS 不可用: {e}")
        
        # 检查 pyttsx3 是否可用
        self._has_pyttsx3 = False
        try:
            import pyttsx3
            self._has_pyttsx3 = True
        except Exception as e:
            logger.debug(f"pyttsx3 不可用: {e}")
        
        # 自动选择引擎
        if engine_type == "auto":
            if self._check_gpt_sovits_available():
                self.engine_type = "gpt_sovits"
            elif self._has_edge_tts:
                self.engine_type = "edge"
            elif self._has_pyttsx3:
                self.engine_type = "pyttsx3"
            else:
                self.engine_type = "none"
        
        logger.info(f"TTS 引擎已选择: {self.engine_type}")
    
    def _init_default_config(self):
        """初始化默认配置（兼容 zhiyan 软件配置格式）"""
        # GPT-SoVITS 连接配置
        self.gpt_sovits_host = "127.0.0.1"
        self.gpt_sovits_port = 9880
        self.gpt_sovits_path = ""
        
        # 参考音频配置
        self.gpt_sovits_refer_wav = ""
        self.gpt_sovits_prompt_text = ""
        self.gpt_sovits_prompt_language = "ja"
        self.gpt_sovits_text_language = "zh"
        self.gpt_sovits_aux_ref_audio_paths = []
        
        # 模型权重路径
        self.gpt_sovits_gpt_weights_path = ""
        self.gpt_sovits_sovits_weights_path = ""
        
        # 合成参数（优化默认值以提高文本跟随准确性）
        self.gpt_sovits_speed = 1.0
        self.gpt_sovits_temperature = 0.5
        self.gpt_sovits_top_k = 10
        self.gpt_sovits_top_p = 0.8
        self.gpt_sovits_seed = -1
        self.gpt_sovits_sample_steps = 20
        self.gpt_sovits_super_sampling = False
        self.gpt_sovits_batch_size = 1
        self.gpt_sovits_text_split_method = "cut5"
        self.gpt_sovits_batch_threshold = 0.75
        self.gpt_sovits_split_bucket = True
        self.gpt_sovits_fragment_interval = 0.3
        self.gpt_sovits_repetition_penalty = 1.15
        self.gpt_sovits_parallel_infer = True
        
        # 高级参数
        self.gpt_sovits_text_language_detection = True
        self.gpt_sovits_auto_tuning = False
        self.gpt_sovits_auto_tuning_alpha = 0.3
        self.gpt_sovits_noise_scale = 0.667
        self.gpt_sovits_length_scale = 1.0
        
        # API v2 输出格式
        self.gpt_sovits_media_type = "wav"
        self.gpt_sovits_streaming_mode = False
        
        # 情绪检测
        self.gpt_sovits_emotion_detection_enabled = False
        self.gpt_sovits_default_emotion = "default"
        
        # 情绪预设（完整兼容 zhiyan 软件配置）
        self.gpt_sovits_emotions = {
            "gentle": {
                "name": "温柔",
                "ref_audio_path": "",
                "prompt_text": "不要害怕，也不要哭了",
                "prompt_lang": "zh",
                "speed_factor": 1.0,
                "fragment_interval": 0.3,
                "keywords": ["温柔", "深情", "喜欢", "爱", "抱抱", "安慰"]
            },
            "happy": {
                "name": "开心",
                "ref_audio_path": "",
                "prompt_text": "今天天气真好，我们出去玩吧！",
                "prompt_lang": "zh",
                "speed_factor": 1.1,
                "fragment_interval": 0.2,
                "keywords": ["开心", "高兴", "哈哈", "太好了", "棒", "哇", "耶"]
            },
            "angry": {
                "name": "生气",
                "ref_audio_path": "",
                "prompt_text": "你怎么能这样做！太让我失望了！",
                "prompt_lang": "zh",
                "speed_factor": 1.2,
                "fragment_interval": 0.4,
                "keywords": ["生气", "哼", "尼玛", "妈", "操", "艹", "逼", "爹", "滚", "老子", "屎", "死"]
            },
            "surprise": {
                "name": "惊讶",
                "ref_audio_path": "",
                "prompt_text": "天啊！这怎么可能发生？",
                "prompt_lang": "zh",
                "speed_factor": 1.0,
                "fragment_interval": 0.3,
                "keywords": ["惊讶", "啊", "什么", "怎么", "真的", "不会吧"]
            },
            "default": {
                "name": "默认",
                "ref_audio_path": "",
                "prompt_text": "",
                "prompt_lang": "zh",
                "speed_factor": 1.0,
                "fragment_interval": 0.3,
                "keywords": []
            }
        }
    
    def _load_config(self, config: dict):
        """从配置字典加载参数（完整兼容 zhiyan 软件配置格式）"""
        # 基础 TTS 配置
        self.pitch = config.get("tts_pitch", 0)
        self.language = config.get("tts_language", "zh")
        self._auto_clean = config.get("tts_auto_clean", True)
        self._max_length = config.get("tts_max_length", 500)
        self._cache_enabled = config.get("tts_cache_enabled", True)
        
        # GPT-SoVITS 配置
        sovits_config = config.get("gpt_sovits", {})
        self.gpt_sovits_host = sovits_config.get("host", "127.0.0.1")
        self.gpt_sovits_port = sovits_config.get("port", 9880)
        self.gpt_sovits_path = sovits_config.get("path", "")
        self.gpt_sovits_refer_wav = sovits_config.get("refer_wav", "")
        self.gpt_sovits_prompt_text = sovits_config.get("prompt_text", "")
        self.gpt_sovits_prompt_language = sovits_config.get("prompt_language", "ja")
        self.gpt_sovits_text_language = sovits_config.get("text_language", "zh")
        self.gpt_sovits_aux_ref_audio_paths = sovits_config.get("aux_ref_audio_paths", [])
        self.gpt_sovits_gpt_weights_path = sovits_config.get("gpt_weights_path", "")
        self.gpt_sovits_sovits_weights_path = sovits_config.get("sovits_weights_path", "")
        self.gpt_sovits_speed = sovits_config.get("speed", 1.0)
        self.gpt_sovits_temperature = sovits_config.get("temperature", 0.5)
        self.gpt_sovits_top_k = sovits_config.get("top_k", 10)
        self.gpt_sovits_top_p = sovits_config.get("top_p", 0.8)
        self.gpt_sovits_seed = sovits_config.get("seed", -1)
        self.gpt_sovits_sample_steps = sovits_config.get("sample_steps", 20)
        self.gpt_sovits_super_sampling = sovits_config.get("super_sampling", False)
        self.gpt_sovits_batch_size = sovits_config.get("batch_size", 1)
        self.gpt_sovits_text_split_method = sovits_config.get("text_split_method", "cut5")
        self.gpt_sovits_batch_threshold = sovits_config.get("batch_threshold", 0.75)
        self.gpt_sovits_split_bucket = sovits_config.get("split_bucket", True)
        self.gpt_sovits_fragment_interval = sovits_config.get("fragment_interval", 0.3)
        self.gpt_sovits_repetition_penalty = sovits_config.get("repetition_penalty", 1.15)
        self.gpt_sovits_parallel_infer = sovits_config.get("parallel_infer", True)
        self.gpt_sovits_text_language_detection = sovits_config.get("text_language_detection", True)
        self.gpt_sovits_auto_tuning = sovits_config.get("auto_tuning", False)
        self.gpt_sovits_auto_tuning_alpha = sovits_config.get("auto_tuning_alpha", 0.3)
        self.gpt_sovits_noise_scale = sovits_config.get("noise_scale", 0.667)
        self.gpt_sovits_length_scale = sovits_config.get("length_scale", 1.0)
        self.gpt_sovits_media_type = sovits_config.get("media_type", "wav")
        self.gpt_sovits_streaming_mode = sovits_config.get("streaming_mode", False)
        self.gpt_sovits_emotion_detection_enabled = sovits_config.get("emotion_detection", sovits_config.get("emotion_detection_enabled", False))
        self.gpt_sovits_default_emotion = sovits_config.get("default_emotion", "default")
        
        # 情绪预设（从配置加载，完整兼容 zhiyan 格式）
        emotions_config = sovits_config.get("emotions", {})
        if emotions_config:
            self.gpt_sovits_emotions = {}
            for key, emo in emotions_config.items():
                self.gpt_sovits_emotions[key] = {
                    "name": emo.get("name", key),
                    "ref_audio_path": emo.get("ref_audio_path", ""),
                    "prompt_text": emo.get("prompt_text", ""),
                    "prompt_lang": emo.get("prompt_lang", "zh"),
                    "speed_factor": emo.get("speed_factor", 1.0),
                    "fragment_interval": emo.get("fragment_interval", 0.3),
                    "keywords": emo.get("keywords", []),
                }
        else:
            # 默认情绪预设（完整兼容 zhiyan 软件配置）
            self.gpt_sovits_emotions = {
                "gentle": {
                    "name": "温柔",
                    "ref_audio_path": "",
                    "prompt_text": "不要害怕，也不要哭了",
                    "prompt_lang": "zh",
                    "speed_factor": 1.0,
                    "fragment_interval": 0.3,
                    "keywords": ["温柔", "深情", "喜欢", "爱", "抱抱", "安慰"]
                },
                "happy": {
                    "name": "开心",
                    "ref_audio_path": "",
                    "prompt_text": "今天天气真好，我们出去玩吧！",
                    "prompt_lang": "zh",
                    "speed_factor": 1.1,
                    "fragment_interval": 0.2,
                    "keywords": ["开心", "高兴", "哈哈", "太好了", "棒", "哇", "耶"]
                },
                "angry": {
                    "name": "生气",
                    "ref_audio_path": "",
                    "prompt_text": "你怎么能这样做！太让我失望了！",
                    "prompt_lang": "zh",
                    "speed_factor": 1.2,
                    "fragment_interval": 0.4,
                    "keywords": ["生气", "哼", "尼玛", "妈", "操", "艹", "逼", "爹", "滚", "老子", "屎", "死"]
                },
                "surprise": {
                    "name": "惊讶",
                    "ref_audio_path": "",
                    "prompt_text": "天啊！这怎么可能发生？",
                    "prompt_lang": "zh",
                    "speed_factor": 1.0,
                    "fragment_interval": 0.3,
                    "keywords": ["惊讶", "啊", "什么", "怎么", "真的", "不会吧"]
                },
                "default": {
                    "name": "默认",
                    "ref_audio_path": "",
                    "prompt_text": "",
                    "prompt_lang": "zh",
                    "speed_factor": 1.0,
                    "fragment_interval": 0.3,
                    "keywords": []
                }
            }
    
    def _check_gpt_sovits_available(self) -> bool:
        """
        检查 GPT-SoVITS 服务是否可用
        
        返回:
            bool: 可用返回 True，否则返回 False
        """
        try:
            import requests
            url = f"http://{self.gpt_sovits_host}:{self.gpt_sovits_port}/docs"
            response = requests.get(url, timeout=3)
            return response.status_code == 200
        except (ImportError, requests.RequestException):
            return False
    
    def synthesize_with_emotion(self, text: str, emotion: str = None, 
                                on_complete=None, on_error=None) -> None:
        """
        带情绪的语音合成
        
        参数:
            text: 要合成的文本
            emotion: 情绪类型（gentle, happy, angry, surprise, sad, excited, default）
            on_complete: 合成完成回调函数
            on_error: 合成失败回调函数
        """
        self.synthesize(text, on_complete, on_error, emotion=emotion)
    
    def detect_emotion(self, text: str) -> str:
        """
        检测文本情绪
        
        参数:
            text: 输入文本
        
        返回:
            str: 检测到的情绪类型
        """
        if not self.gpt_sovits_emotion_detection_enabled:
            return self.gpt_sovits_default_emotion
        
        for emotion_key, emotion_config in self.gpt_sovits_emotions.items():
            if emotion_key == "default":
                continue
            keywords = emotion_config.get("keywords", [])
            for keyword in keywords:
                if keyword in text:
                    return emotion_key
        
        return self.gpt_sovits_default_emotion
    
    def _detect_api_version(self) -> str:
        """
        自动检测 GPT-SoVITS API 版本
        
        返回:
            str: "v2" 或 "v1"
        """
        if hasattr(self, '_api_version'):
            return self._api_version
        
        try:
            import requests
            base = f"http://{self.gpt_sovits_host}:{self.gpt_sovits_port}"
            
            # 尝试 v2: POST /tts 用最小参数
            try:
                resp = requests.post(f"{base}/tts", json={
                    "text": "test",
                    "text_lang": "zh",
                }, timeout=5)
                # 200 = 成功, 400 = 参数错误但端点存在（v2）
                if resp.status_code in (200, 400, 422):
                    self._api_version = "v2"
                    logger.info(f"GPT-SoVITS API 版本: v2 (端点 /tts)")
                    return "v2"
            except requests.RequestException:
                pass
            
            # 尝试 v1: POST / 用最小参数
            try:
                resp = requests.post(f"{base}/", json={
                    "text": "test",
                    "text_language": "zh",
                }, timeout=5)
                if resp.status_code in (200, 400):
                    self._api_version = "v1"
                    logger.info(f"GPT-SoVITS API 版本: v1 (端点 /)")
                    return "v1"
            except requests.RequestException:
                pass
            
            # 默认 v2
            self._api_version = "v2"
            logger.warning(f"GPT-SoVITS API 版本检测失败，默认使用 v2")
            return "v2"
            
        except Exception as e:
            logger.warning(f"API 版本检测异常: {e}，默认使用 v2")
            self._api_version = "v2"
            return "v2"
    
    def _synthesize_gpt_sovits(self, text: str, on_complete=None, on_error=None, params: dict = None) -> None:
        """
        使用 GPT-SoVITS 合成语音（自动适配 API v1/v2）
        
        参数:
            text: 要合成的文本
            on_complete: 合成完成回调函数
            on_error: 合成失败回调函数
            params: 额外参数（包含 emotion 等）
        """
        try:
            import requests
            
            clean_text = self._clean_text_for_tts(text)
            
            # 确定输出文件扩展名
            ext = ".wav" if self.gpt_sovits_media_type == "wav" else ".mp3"
            hash_str = hashlib.md5(f"{text}_{time.time()}".encode()).hexdigest()[:8]
            output_path = os.path.join(self._output_dir, f"ZhiYan_tts_gpt_sovits_{hash_str}{ext}")
            
            # 自动检测 API 版本
            api_version = self._detect_api_version()
            base = f"http://{self.gpt_sovits_host}:{self.gpt_sovits_port}"
            
            # 处理情绪参数
            emotion = None
            if params:
                emotion = params.get("emotion")
            
            # 确定参考音频配置
            ref_audio_path = self.gpt_sovits_refer_wav
            prompt_text = self.gpt_sovits_prompt_text
            prompt_lang = self.gpt_sovits_prompt_language
            
            # 如果指定了情绪且有对应的参考音频配置，切换情绪参考音频
            speed_factor = self.gpt_sovits_speed
            fragment_interval = self.gpt_sovits_fragment_interval
            if emotion and emotion != "default":
                emotion_config = self.gpt_sovits_emotions.get(emotion)
                if emotion_config:
                    emo_ref = emotion_config.get("ref_audio_path", "")
                    if emo_ref and os.path.exists(emo_ref):
                        ref_audio_path = emo_ref
                    emo_prompt = emotion_config.get("prompt_text", "")
                    if emo_prompt:
                        prompt_text = emo_prompt
                    emo_lang = emotion_config.get("prompt_lang", "")
                    if emo_lang:
                        prompt_lang = emo_lang
                    emo_speed = emotion_config.get("speed_factor")
                    if emo_speed:
                        speed_factor = self.gpt_sovits_speed * emo_speed
                    emo_fragment = emotion_config.get("fragment_interval")
                    if emo_fragment:
                        fragment_interval = emo_fragment
            
            if api_version == "v2":
                # ====== API v2: POST /tts ======
                url = f"{base}/tts"
                request_params = {
                    "text": clean_text,
                    "text_lang": self.gpt_sovits_text_language,
                    "speed_factor": speed_factor,
                    "top_k": self.gpt_sovits_top_k,
                    "top_p": self.gpt_sovits_top_p,
                    "temperature": self.gpt_sovits_temperature,
                    "seed": self.gpt_sovits_seed,
                    "sample_steps": self.gpt_sovits_sample_steps,
                    "super_sampling": self.gpt_sovits_super_sampling,
                    "batch_size": self.gpt_sovits_batch_size,
                    "text_split_method": self.gpt_sovits_text_split_method,
                    "batch_threshold": self.gpt_sovits_batch_threshold,
                    "split_bucket": self.gpt_sovits_split_bucket,
                    "fragment_interval": fragment_interval,
                    "repetition_penalty": self.gpt_sovits_repetition_penalty,
                    "parallel_infer": self.gpt_sovits_parallel_infer,
                    "media_type": self.gpt_sovits_media_type,
                    "streaming_mode": self.gpt_sovits_streaming_mode,
                }
                
                # 添加参考音频配置
                if ref_audio_path and os.path.exists(ref_audio_path):
                    request_params["ref_audio_path"] = ref_audio_path
                    request_params["prompt_text"] = prompt_text
                    request_params["prompt_lang"] = prompt_lang
                
                # 添加额外参考音频路径
                if self.gpt_sovits_aux_ref_audio_paths:
                    filtered_paths = [p for p in self.gpt_sovits_aux_ref_audio_paths if os.path.exists(p)]
                    if filtered_paths:
                        request_params["aux_ref_audio_paths"] = filtered_paths
            else:
                # ====== API v1: POST / ======
                url = f"{base}/"
                request_params = {
                    "text": clean_text,
                    "text_language": self.gpt_sovits_text_language,
                    "speed": speed_factor,
                    "top_k": self.gpt_sovits_top_k,
                    "top_p": self.gpt_sovits_top_p,
                    "temperature": self.gpt_sovits_temperature,
                }
                
                # 添加参考音频配置（v1 参数名不同）
                if ref_audio_path and os.path.exists(ref_audio_path):
                    request_params["refer_wav_path"] = ref_audio_path
                    request_params["prompt_text"] = prompt_text
                    request_params["prompt_language"] = prompt_lang
            
            logger.info(f"GPT-SoVITS 请求 (API {api_version}): {url} | 文本长度: {len(clean_text)}")
            logger.debug(f"GPT-SoVITS 请求参数: {request_params}")
            
            # 发送请求，设置较长超时
            response = requests.post(url, json=request_params, timeout=180)
            
            if response.status_code == 200:
                with open(output_path, 'wb') as f:
                    f.write(response.content)
                
                if os.path.exists(output_path) and on_complete:
                    on_complete(output_path)
                elif on_error:
                    on_error("合成失败：输出文件未生成")
            else:
                error_msg = f"HTTP {response.status_code}"
                try:
                    error_info = response.json()
                    error_msg = error_info.get('message', error_info.get('detail', str(error_info)))
                except (json.JSONDecodeError, ValueError):
                    try:
                        error_msg = response.text[:200]
                    except (LookupError, Exception):
                        pass
                logger.error(f"GPT-SoVITS 返回错误: {response.status_code} | {error_msg}")
                if on_error:
                    on_error(f"GPT-SoVITS 错误: {error_msg}")
        except Exception as e:
            logger.error(f"GPT-SoVITS 错误: {e}")
            if on_error:
                on_error(f"TTS 错误: {str(e)}")
    
    def _synthesize_edge(self, text: str, on_complete=None, on_error=None, params: dict = None) -> None:
        """
        使用 Edge TTS 合成语音
        
        参数:
            text: 要合成的文本
            on_complete: 合成完成回调函数
            on_error: 合成失败回调函数
            params: 额外参数
        """
        try:
            import edge_tts
            
            # 使用更稳定的文件名生成方式
            hash_str = hashlib.md5(f"{text}_{time.time()}".encode()).hexdigest()[:8]
            output_path = os.path.join(self._output_dir, f"ZhiYan_tts_edge_{hash_str}.mp3")
            clean_text = self._clean_text_for_tts(text)
            
            # 计算语速
            rate_offset = self.rate - 200
            rate_str = f"+{rate_offset}%" if rate_offset >= 0 else f"{rate_offset}%"
            
            communicate = edge_tts.Communicate(
                clean_text, 
                self.voice, 
                rate=rate_str,
                volume=f"+{(self.volume - 1) * 100}%"
            )
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(communicate.save(output_path))
            loop.close()
            
            if os.path.exists(output_path) and on_complete:
                on_complete(output_path)
            elif on_error:
                on_error("合成失败：输出文件未生成")
        except Exception as e:
            logger.error(f"Edge TTS 错误: {e}")
            if on_error:
                on_error(f"TTS 错误: {str(e)}")
    
    def _synthesize_pyttsx3(self, text: str, on_complete=None, on_error=None, params: dict = None) -> None:
        """
        使用 pyttsx3 合成语音（系统 TTS）
        
        参数:
            text: 要合成的文本
            on_complete: 合成完成回调函数
            on_error: 合成失败回调函数
            params: 额外参数
        """
        try:
            import pyttsx3
            
            # 使用更稳定的文件名生成方式
            hash_str = hashlib.md5(f"{text}_{time.time()}".encode()).hexdigest()[:8]
            output_path = os.path.join(self._output_dir, f"ZhiYan_tts_pyttsx3_{hash_str}.wav")
            clean_text = self._clean_text_for_tts(text)
            
            engine = pyttsx3.init()
            
            # 设置语速 (words per minute)
            engine.setProperty('rate', self.rate)
            
            # 设置音量 (0.0-1.0)
            engine.setProperty('volume', self.volume)
            
            # 设置语音
            voices = engine.getProperty('voices')
            for voice in voices:
                if self.voice.lower() in voice.name.lower():
                    engine.setProperty('voice', voice.id)
                    break
            
            # 保存到文件
            engine.save_to_file(clean_text, output_path)
            engine.runAndWait()
            
            if os.path.exists(output_path) and on_complete:
                on_complete(output_path)
            elif on_error:
                on_error("合成失败：输出文件未生成")
                
            engine.stop()
        except Exception as e:
            logger.error(f"pyttsx3 错误: {e}")
            if on_error:
                on_error(f"TTS 错误: {str(e)}")
    
    def _clean_text_for_tts(self, text: str) -> str:
        """
        清理文本，去除不适合语音合成的内容
        
        参数:
            text: 原始文本
        
        返回:
            str: 清理后的文本
        """
        if not self._auto_clean:
            return text.strip()
            
        # 移除代码块
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'`[^`]+`', '', text)
        
        # 移除图片链接
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
        
        # 将链接转换为纯文本
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        # 移除标题
        text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
        
        # 移除强调符号
        text = re.sub(r'[*_]{1,3}([^*_]+)[*_]{1,3}', r'\1', text)
        
        # 移除Markdown表格
        text = re.sub(r'\|.*?\|', '', text)
        
        # 移除HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        
        # 合并空行
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # 移除多余空格
        text = re.sub(r' {2,}', ' ', text)
        
        # 限制长度
        if len(text) > self._max_length:
            text = text[:self._max_length] + '...'
        
        return text.strip()
    
    def _get_cache_key(self, text: str, emotion: str) -> str:
        """
        生成缓存键
        
        参数:
            text: 文本内容
            emotion: 情绪类型
        
        返回:
            str: 缓存键
        """
        return hashlib.md5(f"{text}_{emotion}_{self.engine_type}_{self.rate}_{self.voice}".encode()).hexdigest()
    
    def _check_cache(self, text: str, emotion: str) -> str:
        """
        检查缓存
        
        参数:
            text: 文本内容
            emotion: 情绪类型
        
        返回:
            str: 缓存文件路径，如果没有缓存返回 None
        """
        if not self._cache_enabled:
            return None
            
        cache_key = self._get_cache_key(text, emotion)
        if cache_key in self._cache:
            cached_path = self._cache[cache_key]
            if os.path.exists(cached_path):
                return cached_path
            else:
                del self._cache[cache_key]
        
        return None
    
    def _update_cache(self, text: str, emotion: str, file_path: str) -> None:
        """
        更新缓存
        
        参数:
            text: 文本内容
            emotion: 情绪类型
            file_path: 音频文件路径
        """
        if not self._cache_enabled:
            return
            
        cache_key = self._get_cache_key(text, emotion)
        
        # 移除旧的缓存
        if cache_key in self._cache:
            old_path = self._cache[cache_key]
            if old_path != file_path and os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
        
        # 添加新缓存
        self._cache[cache_key] = file_path
        
        # 限制缓存大小
        if len(self._cache) > self._cache_max_size:
            oldest_key = next(iter(self._cache))
            old_path = self._cache.pop(oldest_key)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except OSError:
                    pass
    
    def synthesize(self, text: str, on_complete=None, on_error=None, 
                   emotion: str = None, params: dict = None) -> None:
        """
        合成语音（优化版）
        
        参数:
            text: 要合成的文本
            on_complete: 合成完成回调函数，接收音频文件路径
            on_error: 合成失败回调函数，接收错误信息
            emotion: 情绪类型
            params: 额外参数字典
        """
        # 频率限制
        now = time.time()
        if now - self._last_synthesize_time < self._min_interval:
            time.sleep(self._min_interval - (now - self._last_synthesize_time))
        self._last_synthesize_time = time.time()
        
        # 检测情绪
        if emotion is None:
            emotion = self.detect_emotion(text)
        
        # 清理文本
        clean_text = self._clean_text_for_tts(text)
        if not clean_text:
            if on_error:
                on_error("文本内容为空")
            return
        
        # 检查缓存
        cached_path = self._check_cache(clean_text, emotion)
        if cached_path:
            if on_complete:
                on_complete(cached_path)
            return
        
        # 构建参数
        synth_params = {
            "emotion": emotion,
        }
        if params:
            synth_params.update(params)
        
        if self.engine_type == "gpt_sovits":
            thread = threading.Thread(
                target=self._synthesize_gpt_sovits, 
                args=(clean_text, on_complete, on_error, synth_params), 
                daemon=True
            )
            thread.start()
        elif self.engine_type == "edge" and self._has_edge_tts:
            thread = threading.Thread(
                target=self._synthesize_edge, 
                args=(clean_text, on_complete, on_error, synth_params), 
                daemon=True
            )
            thread.start()
        elif self.engine_type == "pyttsx3" and self._has_pyttsx3:
            thread = threading.Thread(
                target=self._synthesize_pyttsx3, 
                args=(clean_text, on_complete, on_error, synth_params), 
                daemon=True
            )
            thread.start()
        else:
            if on_error:
                on_error("没有可用的 TTS 引擎")
    
    async def synthesize_stream(self, text: str, chunk_callback=None, emotion: str = None):
        """
        流式合成语音（异步版本）
        
        参数:
            text: 要合成的文本
            chunk_callback: 音频块回调函数，接收二进制音频数据
            emotion: 情绪类型
        
        返回:
            str: 音频文件路径
        """
        import asyncio
        
        clean_text = self._clean_text_for_tts(text)
        if not clean_text:
            raise ValueError("文本内容为空")
        
        if emotion is None:
            emotion = self.detect_emotion(text)
        
        # 检查缓存
        cached_path = self._check_cache(clean_text, emotion)
        if cached_path:
            with open(cached_path, 'rb') as f:
                data = f.read()
                if chunk_callback:
                    chunk_callback(data)
            return cached_path
        
        # 使用事件等待合成完成
        result = {"success": False, "error": None, "file_path": None}
        event = asyncio.Event()
        
        def on_complete(file_path):
            nonlocal result
            result["success"] = True
            result["file_path"] = file_path
            event.set()
        
        def on_error(error):
            nonlocal result
            result["error"] = error
            event.set()
        
        self.synthesize(clean_text, on_complete, on_error, emotion=emotion)
        
        try:
            await asyncio.wait_for(event.wait(), timeout=120)
            
            if result["success"] and result["file_path"]:
                # 更新缓存
                self._update_cache(clean_text, emotion, result["file_path"])
                
                # 如果有回调，读取并返回音频数据
                if chunk_callback:
                    with open(result["file_path"], 'rb') as f:
                        chunk_callback(f.read())
                
                return result["file_path"]
            else:
                raise ValueError(result["error"] or "合成失败")
                
        except asyncio.TimeoutError:
            raise TimeoutError("TTS 合成超时")
    
    def get_engine_info(self) -> dict:
        """
        获取引擎信息（增强版）
        
        返回:
            dict: 引擎信息
        """
        return {
            "engine_type": self.engine_type,
            "available_engines": {
                "gpt_sovits": self._check_gpt_sovits_available(),
                "edge": self._has_edge_tts,
                "pyttsx3": self._has_pyttsx3,
            },
            "voice": self.voice,
            "rate": self.rate,
            "volume": self.volume,
            "pitch": self.pitch,
            "language": self.language,
            "emotions": [
                {"key": k, "name": v["name"], "keywords": v["keywords"]}
                for k, v in self.gpt_sovits_emotions.items()
            ],
            "config": {
                "auto_clean": self._auto_clean,
                "max_length": self._max_length,
                "cache_enabled": self._cache_enabled,
                "cache_size": len(self._cache),
            },
        }
    
    def update_config(self, config: dict) -> None:
        """
        更新配置
        
        参数:
            config: 新配置字典
        """
        if "tts_pitch" in config:
            self.pitch = config["tts_pitch"]
        if "tts_language" in config:
            self.language = config["tts_language"]
        if "tts_auto_clean" in config:
            self._auto_clean = config["tts_auto_clean"]
        if "tts_max_length" in config:
            self._max_length = config["tts_max_length"]
        if "tts_cache_enabled" in config:
            self._cache_enabled = config["tts_cache_enabled"]
        
        sovits_config = config.get("gpt_sovits", {})
        if sovits_config:
            if "host" in sovits_config:
                self.gpt_sovits_host = sovits_config["host"]
            if "port" in sovits_config:
                self.gpt_sovits_port = sovits_config["port"]
            if "path" in sovits_config:
                self.gpt_sovits_path = sovits_config["path"]
            if "refer_wav" in sovits_config:
                self.gpt_sovits_refer_wav = sovits_config["refer_wav"]
            if "prompt_text" in sovits_config:
                self.gpt_sovits_prompt_text = sovits_config["prompt_text"]
            if "prompt_language" in sovits_config:
                self.gpt_sovits_prompt_language = sovits_config["prompt_language"]
            if "text_language" in sovits_config:
                self.gpt_sovits_text_language = sovits_config["text_language"]
            if "speed" in sovits_config:
                self.gpt_sovits_speed = sovits_config["speed"]
            if "temperature" in sovits_config:
                self.gpt_sovits_temperature = sovits_config["temperature"]
            if "top_k" in sovits_config:
                self.gpt_sovits_top_k = sovits_config["top_k"]
            if "top_p" in sovits_config:
                self.gpt_sovits_top_p = sovits_config["top_p"]
            if "seed" in sovits_config:
                self.gpt_sovits_seed = sovits_config["seed"]
            if "sample_steps" in sovits_config:
                self.gpt_sovits_sample_steps = sovits_config["sample_steps"]
            if "super_sampling" in sovits_config:
                self.gpt_sovits_super_sampling = sovits_config["super_sampling"]
            if "batch_size" in sovits_config:
                self.gpt_sovits_batch_size = sovits_config["batch_size"]
            if "text_split_method" in sovits_config:
                self.gpt_sovits_text_split_method = sovits_config["text_split_method"]
            if "batch_threshold" in sovits_config:
                self.gpt_sovits_batch_threshold = sovits_config["batch_threshold"]
            if "split_bucket" in sovits_config:
                self.gpt_sovits_split_bucket = sovits_config["split_bucket"]
            if "fragment_interval" in sovits_config:
                self.gpt_sovits_fragment_interval = sovits_config["fragment_interval"]
            if "repetition_penalty" in sovits_config:
                self.gpt_sovits_repetition_penalty = sovits_config["repetition_penalty"]
            if "parallel_infer" in sovits_config:
                self.gpt_sovits_parallel_infer = sovits_config["parallel_infer"]
            if "media_type" in sovits_config:
                self.gpt_sovits_media_type = sovits_config["media_type"]
            if "streaming_mode" in sovits_config:
                self.gpt_sovits_streaming_mode = sovits_config["streaming_mode"]
            if "emotion_detection" in sovits_config:
                self.gpt_sovits_emotion_detection_enabled = sovits_config["emotion_detection"]
            if "emotion_detection_enabled" in sovits_config:
                self.gpt_sovits_emotion_detection_enabled = sovits_config["emotion_detection_enabled"]
            if "default_emotion" in sovits_config:
                self.gpt_sovits_default_emotion = sovits_config["default_emotion"]
            if "emotions" in sovits_config:
                emotions_config = sovits_config["emotions"]
                for key, emo in emotions_config.items():
                    if key in self.gpt_sovits_emotions:
                        self.gpt_sovits_emotions[key].update(emo)
                    else:
                        self.gpt_sovits_emotions[key] = {
                            "name": emo.get("name", key),
                            "ref_audio_path": emo.get("ref_audio_path", ""),
                            "prompt_text": emo.get("prompt_text", ""),
                            "prompt_lang": emo.get("prompt_lang", "zh"),
                            "speed_factor": emo.get("speed_factor", 1.0),
                            "fragment_interval": emo.get("fragment_interval", 0.3),
                            "keywords": emo.get("keywords", []),
                        }
        
        logger.info("TTS 配置已更新")
