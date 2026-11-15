"""
教辅子系统 LLM 调用与 JSON 解析公共工具

统一封装：
- one_shot()        一次性 LLM 调用（失败返回 None，不抛异常）
- parse_json()      从模型回复中稳健解析 JSON（对象/数组/代码块包裹）
- parse_object() / parse_array()  便捷取对象/数组
- vision_chat()     多模态（图片）调用，走 OpenAI 兼容的 content 数组

所有函数在 LLM 不可用（未配置模型 / 断网）时返回 None，由调用方决定降级策略。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.Edu")


def chat_manager():
    """延迟获取全局 ChatManager（避免循环依赖）"""
    try:
        from zhiyan_web.core import chat_manager as cm
        return cm
    except Exception:
        return None


async def one_shot(
    system_prompt: str,
    user_content: str,
    temperature: float = 0.3,
    max_tokens: int = 2500,
) -> Optional[str]:
    """一次性 LLM 调用；失败/未配置模型返回 None"""
    cm = chat_manager()
    if cm is None:
        return None
    try:
        raw = await cm.one_shot_chat(
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:
        logger.warning(f"LLM 调用异常: {e}")
        return None
    if not raw or str(raw).startswith("错误："):
        logger.warning(f"LLM 不可用: {str(raw)[:120]}")
        return None
    return str(raw)


async def vision_chat(
    system_prompt: str,
    user_content: str,
    image_data_url: str = "",
    model: str = "",
    api_url: str = "",
    api_key: str = "",
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> Optional[str]:
    """多模态（图片）一次性调用。

    通过 OpenAI 兼容的 /chat/completions 直接发起请求（content 为 parts 数组），
    因为现有 LLMProvider 只支持纯文本 content。失败返回 None。
    """
    if not image_data_url:
        return await one_shot(system_prompt, user_content, temperature=temperature, max_tokens=max_tokens)

    cfg = _vision_cfg()
    model = model or cfg.get("model") or "qwen-vl-max"
    api_url = (api_url or cfg.get("api_url") or "").rstrip("/")
    api_key = api_key or cfg.get("api_key") or _fallback_api_key()
    if not api_url or not api_key:
        logger.warning("多模态调用缺少 api_url / api_key")
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_content},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{api_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            logger.warning(f"多模态返回为空: {str(data)[:200]}")
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # 部分实现返回 parts 数组
            content = "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        return str(content or "").strip() or None
    except Exception as e:
        logger.warning(f"多模态调用失败: {e}")
        return None


def _vision_cfg() -> Dict[str, Any]:
    from .models import teaching_settings
    return teaching_settings("vision")


def _fallback_api_key() -> str:
    """vision.api_key 为空时复用顶层 qwen / 通用 api_key"""
    try:
        from zhiyan_web.config import get_settings
        s = get_settings()
        return str(s.get("qwen_api_key") or s.get("api_key") or "")
    except Exception:
        return ""


def strip_code_fence(text: str) -> str:
    """去掉 ```json ... ``` 包裹"""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json|JSON)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def parse_json(text: str) -> Optional[Any]:
    """稳健解析 JSON：整体解析 → 去掉代码块 → 截取最外层 {} / []"""
    if not text:
        return None
    s = strip_code_fence(str(text))
    try:
        return json.loads(s)
    except Exception:
        pass

    # 依次尝试截取对象 / 数组
    for left_ch, right_ch in (("{", "}"), ("[", "]")):
        left, right = s.find(left_ch), s.rfind(right_ch)
        if left != -1 and right > left:
            try:
                return json.loads(s[left:right + 1])
            except Exception:
                continue
    return None


def parse_object(text: str) -> Optional[Dict[str, Any]]:
    """解析为 dict（数组或非 dict 返回 None）"""
    data = parse_json(text)
    return data if isinstance(data, dict) else None


def parse_array(text: str) -> Optional[List[Any]]:
    """解析为 list（dict 时尝试从常见键取值）"""
    data = parse_json(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("questions", "items", "data", "result", "list", "slides", "pages"):
            if isinstance(data.get(key), list):
                return data[key]
    return None