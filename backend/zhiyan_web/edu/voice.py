"""
实时语音问答

链路: 语音（ASR 三引擎）→ 连续记忆上下文 → LLM 作答 → TTS 播报 → 写入记忆

对外接口:
- ask(request)        语音/文本问答（含 ASR、记忆、TTS）
- asr_only(request)   只做语音识别
- synthesize(text)    只做 TTS 合成，返回音频文件路径
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List

from . import asr, memory_bridge
from .llm_utils import one_shot
from .models import teaching_settings

logger = logging.getLogger("ZhiYanWeb.Edu")

_SYSTEM_PROMPT = """你是一位耐心、亲切、善于启发的一线老师，正在课堂上回答学生的问题。

要求：
1. 用口语化的中文回答，简洁清楚，先给结论再解释，控制在 200 字以内（除非学生要求详细展开）。
2. 讲清思路与方法，必要时举一个贴近生活的小例子。
3. 数学公式用纯文本或 LaTeX 行内形式（$...$）。
4. 如果问题信息不足，先给出合理假设再回答，或礼貌地请学生补充。
5. 不要使用 markdown 标题与代码块，用自然段与短句，便于语音朗读。
"""


async def ask(request: Dict[str, Any]) -> Dict[str, Any]:
    """语音/文本问答"""
    cfg = teaching_settings("voice")
    session_id = str(request.get("session_id") or "default")
    subject = str(request.get("subject") or "").strip()
    need_speak = bool(request.get("speak", cfg.get("tts_enabled", True)))
    save_memory = bool(request.get("save_memory", True))

    text = str(request.get("text") or "").strip()
    asr_result: Dict[str, Any] = {"engine": "", "text": ""}

    # 1) 无文本时走语音识别
    if not text and (request.get("audio_base64") or request.get("audio_path")):
        asr_result = await asr.transcribe(
            audio_path=str(request.get("audio_path") or ""),
            audio_base64=str(request.get("audio_base64") or ""),
            engine=str(request.get("engine") or "auto"),
            language=str(request.get("language") or cfg.get("asr_language") or "zh"),
        )
        if not asr_result.get("success"):
            return {"success": False, "message": asr_result.get("message") or "语音识别失败",
                    "asr": asr_result, "question": "", "answer": ""}
        text = asr_result.get("text") or ""

    if not text:
        return {"success": False, "message": "请提供提问文本或语音", "question": "", "answer": ""}

    # 2) 连续记忆上下文
    context = memory_bridge.build_context(session_id, query=text, max_history=8)
    system_prompt = _SYSTEM_PROMPT
    if subject:
        system_prompt += f"\n当前学科：{subject}。"
    if context:
        system_prompt += f"\n\n以下是连续记忆（供参考，不要照搬）：\n{context}"

    # 3) LLM 作答
    answer = await one_shot(system_prompt, text, temperature=0.6, max_tokens=1200)
    if not answer:
        return {
            "success": False, "message": "未配置模型或模型无响应，无法作答",
            "question": text, "answer": "", "asr": asr_result,
        }

    # 4) 写入会话历史 + 长期记忆
    memory_bridge.append_message(session_id, "user", text)
    memory_bridge.append_message(session_id, "assistant", answer)
    if save_memory:
        memory_bridge.remember(session_id, f"学生问：{text}\n老师答：{answer}", importance=0.55,
                               tags=[subject] if subject else None)

    # 5) TTS 播报
    audio_path = ""
    if need_speak:
        audio_path = await synthesize(answer)

    return {
        "success": True,
        "question": text,
        "answer": answer,
        "audio_path": audio_path,
        "audio_url": _to_url(audio_path),
        "asr": asr_result,
        "session_id": session_id,
        "message": "作答完成",
    }


async def asr_only(request: Dict[str, Any]) -> Dict[str, Any]:
    """仅语音识别"""
    cfg = teaching_settings("voice")
    return await asr.transcribe(
        audio_path=str(request.get("audio_path") or ""),
        audio_base64=str(request.get("audio_base64") or ""),
        engine=str(request.get("engine") or "auto"),
        language=str(request.get("language") or cfg.get("asr_language") or "zh"),
    )


async def synthesize(text: str, voice: str = "") -> str:
    """TTS 合成，返回音频文件路径（不可用时返回空串）"""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        from zhiyan_web.core import tts_engine
        if tts_engine is None:
            return ""
        # 长文本截断，避免 TTS 过慢
        payload = text[:800]
        try:
            path = await tts_engine.synthesize_stream(payload)
            return str(path or "")
        except AttributeError:
            # 兼容仅有回调式 synthesize 的旧实现
            import asyncio
            loop = asyncio.get_event_loop()
            fut = loop.create_future()

            def _done(p):
                if not fut.done():
                    loop.call_soon_threadsafe(fut.set_result, p)

            def _err(e):
                if not fut.done():
                    loop.call_soon_threadsafe(fut.set_result, "")

            tts_engine.synthesize(payload, on_complete=_done, on_error=_err)
            try:
                return await asyncio.wait_for(fut, timeout=30)
            except Exception:
                return ""
    except Exception as e:
        logger.info(f"TTS 合成不可用: {e}")
        return ""


def _to_url(path: str) -> str:
    """把落盘音频路径转成可访问 URL（/api/edu/teach/voice/audio?path=...）"""
    if not path:
        return ""
    from urllib.parse import quote
    return f"/api/edu/teach/voice/audio?path={quote(str(path))}"


def info() -> Dict[str, Any]:
    cfg = teaching_settings("voice")
    return {
        "asr_engines": asr.available_engines(),
        "asr_engine": cfg.get("asr_engine", "auto"),
        "tts_enabled": bool(cfg.get("tts_enabled", True)),
        "language": cfg.get("asr_language", "zh"),
    }