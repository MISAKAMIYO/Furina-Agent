"""
翻译路由模块
提供文本翻译和多语言支持

端点:
- GET /api/translate/languages - 获取支持的语言列表
- POST /api/translate - 翻译文本
"""
from __future__ import annotations

import json
import hashlib
import logging
import urllib.parse
from typing import Optional

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("ZhiYanWeb")

router = APIRouter(prefix="/api/translate", tags=["translate"])

# 支持的语言列表
SUPPORTED_LANGUAGES = [
    {"code": "auto", "name": "自动检测"},
    {"code": "zh-CN", "name": "中文（简体）"},
    {"code": "zh-TW", "name": "中文（繁体）"},
    {"code": "en", "name": "英语"},
    {"code": "ja", "name": "日语"},
    {"code": "ko", "name": "韩语"},
    {"code": "fr", "name": "法语"},
    {"code": "de", "name": "德语"},
    {"code": "es", "name": "西班牙语"},
    {"code": "pt", "name": "葡萄牙语"},
    {"code": "ru", "name": "俄语"},
    {"code": "ar", "name": "阿拉伯语"},
    {"code": "it", "name": "意大利语"},
    {"code": "nl", "name": "荷兰语"},
    {"code": "th", "name": "泰语"},
    {"code": "vi", "name": "越南语"},
]


class TranslateRequest(BaseModel):
    text: str
    source_lang: str = "auto"
    target_lang: str = "zh-CN"
    engine: str = "google"


@router.get("/languages")
async def get_languages():
    """获取支持的语言列表"""
    return {"success": True, "data": SUPPORTED_LANGUAGES, "languages": SUPPORTED_LANGUAGES}


# 各引擎的故障转移链：优先用户选择的引擎，失败自动降级到稳定的引擎
_ENGINE_FALLBACK = {
    "google": ["google", "bing", "youdao", "deepl"],
    "bing": ["bing", "google", "youdao", "deepl"],
    "youdao": ["youdao", "google", "bing", "deepl"],
    "deepl": ["deepl", "google", "bing", "youdao"],
}


@router.post("")
async def translate_text(request: TranslateRequest):
    """翻译文本（多引擎自动故障转移）"""
    if not request.text.strip():
        return {"success": False, "message": "请输入需要翻译的文本"}

    engine_order = _ENGINE_FALLBACK.get(request.engine, ["google", "bing", "youdao"])
    errors = []
    for engine in engine_order:
        try:
            if engine == "google":
                result = await _translate_google(request.text, request.source_lang, request.target_lang)
            elif engine == "bing":
                result = await _translate_bing(request.text, request.source_lang, request.target_lang)
            elif engine == "youdao":
                result = await _translate_youdao(request.text, request.source_lang, request.target_lang)
            elif engine == "deepl":
                result = await _translate_deepl(request.text, request.source_lang, request.target_lang)
            else:
                result = await _translate_google(request.text, request.source_lang, request.target_lang)

            if result:
                msg = "翻译成功" if engine == request.engine else f"翻译成功（已自动切换至 {engine}）"
                return {"success": True, "translation": result, "engine": engine, "message": msg}
            errors.append(f"{engine}: 返回空结果")
        except Exception as e:
            logger.warning(f"[翻译] {engine} 失败: {e}")
            errors.append(f"{engine}: {str(e)}")

    logger.error(f"全部翻译引擎失败: {'; '.join(errors)}")
    return {"success": False, "message": "翻译失败：" + "; ".join(errors)}


async def _translate_google(text: str, source: str, target: str) -> Optional[str]:
    """使用 Google 翻译"""
    # Google 翻译使用简化的语言代码
    lang_map = {
        "zh-CN": "zh-CN", "zh-TW": "zh-TW", "en": "en", "ja": "ja",
        "ko": "ko", "fr": "fr", "de": "de", "es": "es", "pt": "pt",
        "ru": "ru", "ar": "ar", "it": "it", "nl": "nl", "th": "th", "vi": "vi",
    }
    src = lang_map.get(source, source if source != "auto" else "auto")
    tgt = lang_map.get(target, target)
    
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": src,
        "tl": tgt,
        "dt": "t",
        "q": text,
    }
    
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        })
        data = resp.json()
        # Google 返回格式: [[["翻译文本", "原文", ...]], ...]
        if data and isinstance(data, list) and len(data) > 0:
            sentences = []
            for item in data[0]:
                if item and isinstance(item, list) and len(item) > 0:
                    sentences.append(item[0])
            return " ".join(sentences)
    return None


async def _translate_bing(text: str, source: str, target: str) -> Optional[str]:
    """使用 Bing 翻译"""
    lang_map = {
        "zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "en": "en", "ja": "ja",
        "ko": "ko", "fr": "fr", "de": "de", "es": "es", "pt": "pt",
        "ru": "ru", "ar": "ar", "it": "it", "nl": "nl", "th": "th", "vi": "vi",
    }
    src = lang_map.get(source, source if source != "auto" else "auto-detect")
    tgt = lang_map.get(target, target)
    
    url = "https://api.cognitive.microsofttranslator.com/translate"
    params = {
        "api-version": "3.0",
        "from": src,
        "to": tgt,
    }
    
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, params=params, json=[{"Text": text}], headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        })
        # 401 = 未配置订阅密钥（免费版不可用），返回 None 交由上层降级
        if resp.status_code != 200:
            logger.warning(f"Bing 翻译返回 {resp.status_code}，降级处理")
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        if data and len(data) > 0:
            translations = data[0].get("translations", [])
            if translations:
                return translations[0].get("text")
    return None


async def _translate_youdao(text: str, source: str, target: str) -> Optional[str]:
    """使用有道翻译"""
    lang_map = {
        "zh-CN": "zh-CHS", "zh-TW": "zh-CHT", "en": "en", "ja": "ja",
        "ko": "ko", "fr": "fr", "de": "de", "es": "es", "pt": "pt",
        "ru": "ru", "ar": "ar", "it": "it", "nl": "nl", "th": "th", "vi": "vi",
    }
    src = lang_map.get(source, source if source != "auto" else "AUTO")
    tgt = lang_map.get(target, target)
    
    salt = "12345"
    sign_str = f"fanyideskweb{text}{salt}Ygy_4c=r#e#4EX^NUGUc5"
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    
    url = "https://dict.youdao.com/webtranslate"
    data = {
        "i": text,
        "from": src,
        "to": tgt,
        "domain": "0",
        "dictResult": "true",
        "keyid": "webfanyi",
        "sign": sign,
        "client": "fanyideskweb",
        "product": "webfanyi",
        "appVersion": "1.0.0",
        "vendor": "web",
        "pointParam": "client,mysticTime,product",
        "mysticTime": "12345",
        "keyfrom": "fanyi.web",
    }
    
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=data, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://fanyi.youdao.com/',
            'Origin': 'https://fanyi.youdao.com',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Cookie': 'OUTFOX_SEARCH_USER_ID=0@0.0.0.0;',
        })
        if resp.status_code != 200:
            return None
        # 有道可能返回 HTML/空响应/加密结构，统一做容错解析
        try:
            result = resp.json()
        except Exception:
            return None
        if not isinstance(result, dict):
            return None
        if result.get("code") not in (0, None):
            return None
        if result.get("errorCode") not in (0, None, ""):
            return None
        translate_result = result.get("translateResult") or []
        if translate_result and len(translate_result) > 0:
            sentences = [item.get("tgt", "") for item in translate_result[0] if isinstance(item, dict) and item.get("tgt")]
            if sentences:
                return " ".join(sentences)
        # 部分端点结构为 webtranslate.data
        web = result.get("webtranslate") or {}
        ensure = web.get("data") or ""
        if ensure:
            return ensure
    return None


async def _translate_deepl(text: str, source: str, target: str) -> Optional[str]:
    """使用 DeepL 翻译（免费版）"""
    lang_map = {
        "zh-CN": "ZH", "zh-TW": "ZH", "en": "EN", "ja": "JA",
        "ko": "KO", "fr": "FR", "de": "DE", "es": "ES", "pt": "PT",
        "ru": "RU", "it": "IT", "nl": "NL",
    }
    tgt = lang_map.get(target, "ZH")
    
    url = "https://api-free.deepl.com/v2/translate"
    data = {
        "text": text,
        "target_lang": tgt,
        "source_lang": lang_map.get(source, "") if source != "auto" else "",
    }
    
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(url, data=data, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
            })
            if resp.status_code == 200:
                result = resp.json()
                translations = result.get("translations", [])
                if translations:
                    return translations[0].get("text")
        except Exception:
            pass
    
    # DeepL 免费版不可用，降级到 Google
    logger.warning("DeepL 不可用，降级到 Google 翻译")
    return await _translate_google(text, source, target)