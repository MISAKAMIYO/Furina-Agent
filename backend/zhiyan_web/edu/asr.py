"""
语音识别（ASR）三引擎

- local   : faster-whisper 本地离线识别（未安装自动降级）
- cloud   : 阿里 DashScope 云端识别（qwen-audio-asr，走 OpenAI 兼容 /chat/completions 的 input_audio）
- browser : 前端浏览器 Web Speech API（由前端处理，后端只做标记）

对外接口:
- transcribe(audio_path, audio_base64, engine, language) -> dict
- available_engines() -> dict
"""
from __future__ import annotations

import base64
import logging
import os
import time
from typing import Any, Dict, Optional

from .llm_utils import _fallback_api_key, _vision_cfg
from .models import EDU_VOICE_DIR, teaching_settings

logger = logging.getLogger("ZhiYanWeb.Edu")


def _local_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _cloud_available() -> bool:
    cfg = teaching_settings("vision")
    return bool((cfg.get("api_key") or _fallback_api_key()) and cfg.get("api_url"))


def available_engines() -> Dict[str, bool]:
    return {"local": _local_available(), "cloud": _cloud_available(), "browser": True}


# ============= 音频载入 =============

def _save_audio(audio_base64: str, suffix: str = ".wav") -> str:
    data = audio_base64 or ""
    if data.strip().startswith("data:") and "," in data[:128]:
        header, data = data.split(",", 1)
        if "mp3" in header or "mpeg" in header:
            suffix = ".mp3"
        elif "webm" in header:
            suffix = ".webm"
    try:
        raw = base64.b64decode(data)
    except Exception as e:
        raise ValueError(f"音频 base64 解析失败: {e}")
    os.makedirs(EDU_VOICE_DIR, exist_ok=True)
    path = os.path.join(EDU_VOICE_DIR, f"asr_{int(time.time() * 1000)}{suffix}")
    with open(path, "wb") as f:
        f.write(raw)
    return path


def _resolve_audio(audio_path: str, audio_base64: str) -> str:
    if audio_path and os.path.exists(audio_path):
        return audio_path
    if audio_base64:
        return _save_audio(audio_base64)
    return ""


# ============= 本地（faster-whisper） =============

_LOCAL_MODELS: Dict[str, Any] = {}


def _local_transcribe(path: str, language: str, model_size: str) -> Dict[str, Any]:
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        return {"success": False, "engine": "local", "text": "", "message": f"未安装 faster-whisper: {e}"}
    try:
        model = _LOCAL_MODELS.get(model_size)
        if model is None:
            # CPU + int8，兼容性最好；首次会下载模型
            model = WhisperModel(model_size or "small", device="cpu", compute_type="int8")
            _LOCAL_MODELS[model_size] = model
        segments, _info = model.transcribe(path, language=None if language in ("auto", "") else language, beam_size=5)
        text = "".join(seg.text for seg in segments).strip()
        if not text:
            return {"success": False, "engine": "local", "text": "", "message": "本地识别无结果"}
        return {"success": True, "engine": "local", "text": text, "message": "本地识别"}
    except Exception as e:
        logger.warning(f"本地 ASR 失败: {e}")
        return {"success": False, "engine": "local", "text": "", "message": f"本地识别失败: {e}"}


# ============= 云端（DashScope qwen-audio-asr） =============

async def _cloud_transcribe(path: str, language: str, model: str) -> Dict[str, Any]:
    cfg = _vision_cfg()
    api_url = str(cfg.get("api_url") or "").rstrip("/")
    api_key = str(cfg.get("api_key") or _fallback_api_key())
    if not api_url or not api_key:
        return {"success": False, "engine": "cloud", "text": "", "message": "未配置云端 ASR（缺少 Key）"}
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "wav"
    fmt = {"mpeg": "mp3"}.get(ext, ext)
    try:
        with open(path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        return {"success": False, "engine": "cloud", "text": "", "message": f"读取音频失败: {e}"}

    payload = {
        "model": model or "qwen-audio-asr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": audio_b64, "format": fmt}},
                    {"type": "text", "text": "请把这段语音准确转写成文字，只输出转写结果，不要解释。"},
                ],
            }
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(f"{api_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return {"success": False, "engine": "cloud", "text": "", "message": "云端识别返回为空"}
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, list):
            content = "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        text = str(content or "").strip()
        if not text:
            return {"success": False, "engine": "cloud", "text": "", "message": "云端识别无结果"}
        return {"success": True, "engine": "cloud", "text": text, "message": "云端识别"}
    except Exception as e:
        logger.warning(f"云端 ASR 失败: {e}")
        return {"success": False, "engine": "cloud", "text": "", "message": f"云端识别失败: {e}"}


# ============= 对外接口 =============

async def transcribe(audio_path: str = "", audio_base64: str = "",
                     engine: str = "auto", language: str = "zh") -> Dict[str, Any]:
    """语音识别；engine: auto / local / cloud / browser"""
    cfg = teaching_settings("voice")
    engine = (engine or cfg.get("asr_engine") or "auto").strip().lower()
    language = language or cfg.get("asr_language") or "zh"
    model_size = str(cfg.get("local_model") or "small")
    cloud_model = str(cfg.get("cloud_model") or "qwen-audio-asr")

    if engine == "browser":
        return {"success": False, "engine": "browser", "text": "",
                "message": "请在前端使用浏览器语音识别（Web Speech API）"}

    path = _resolve_audio(audio_path, audio_base64)
    if not path and engine in ("auto", "local", "cloud"):
        return {"success": False, "engine": engine, "text": "", "message": "请提供音频文件或 base64 音频"}

    errors = []
    if engine in ("auto", "local"):
        if _local_available():
            res = _local_transcribe(path, language, model_size)
            if res.get("success"):
                return res
            errors.append(res.get("message"))
        else:
            errors.append("未安装本地 ASR（faster-whisper）")
        if engine == "local":
            return {"success": False, "engine": "local", "text": "", "message": "；".join(errors)}

    if engine in ("auto", "cloud"):
        if _cloud_available():
            res = await _cloud_transcribe(path, language, cloud_model)
            if res.get("success"):
                return res
            errors.append(res.get("message"))
        else:
            errors.append("未配置云端 ASR")
        if engine == "cloud":
            return {"success": False, "engine": "cloud", "text": "", "message": "；".join(errors)}

    return {"success": False, "engine": engine, "text": "",
            "message": "语音识别不可用：" + "；".join(e for e in errors if e)}