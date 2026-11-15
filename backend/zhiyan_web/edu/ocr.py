"""
OCR 双通道服务

- 本地离线通道：RapidOCR（onnxruntime，离线、无需联网；未安装自动跳过）
- 多模态通道：视觉大模型（如阿里千问 qwen-vl）识别，作为本地 OCR 的兜底/补充

对外接口:
- ocr_image(...)        通用文字识别（图片路径 / base64 / 屏幕截图）
- ocr_question(...)     图片题目识别（返回结构化题目 JSON）
- ocr_screen(...)       截取屏幕后识别（悬浮球 / 快捷目录用）
- available_engines()   当前可用引擎
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from .llm_utils import parse_object, vision_chat
from .models import EDU_FILES_DIR, teaching_settings

logger = logging.getLogger("ZhiYanWeb.Edu")


# ============= 引擎可用性 =============

def _local_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _vision_available() -> bool:
    from .llm_utils import _vision_cfg, _fallback_api_key
    cfg = _vision_cfg()
    return bool((cfg.get("api_key") or _fallback_api_key()) and cfg.get("api_url"))


def available_engines() -> Dict[str, bool]:
    return {"local": _local_available(), "vision": _vision_available()}


# ============= 图片载入 =============

def _save_base64(image_base64: str) -> str:
    """把 base64 图片落盘，返回路径"""
    data = image_base64 or ""
    if "," in data[:64] and data.strip().startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data)
    except Exception as e:
        raise ValueError(f"图片 base64 解析失败: {e}")
    os.makedirs(EDU_FILES_DIR, exist_ok=True)
    path = os.path.join(EDU_FILES_DIR, f"ocr_{int(time.time() * 1000)}.png")
    with open(path, "wb") as f:
        f.write(raw)
    return path


def _to_data_url(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "bmp": "bmp", "webp": "webp"}.get(ext, "png")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/{mime};base64,{b64}"


def _resolve_path(image_path: str, image_base64: str) -> str:
    if image_path and os.path.exists(image_path):
        return image_path
    if image_base64:
        return _save_base64(image_base64)
    return ""


# ============= 本地 OCR =============

_LOCAL_ENGINE = None


def _local_ocr(path: str) -> Dict[str, Any]:
    """RapidOCR 本地识别，返回 {success, text, items}"""
    global _LOCAL_ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as e:
        return {"success": False, "text": "", "items": [], "message": f"未安装 rapidocr-onnxruntime: {e}"}
    try:
        if _LOCAL_ENGINE is None:
            _LOCAL_ENGINE = RapidOCR()
        result, _ = _LOCAL_ENGINE(path)
        items: List[Dict[str, Any]] = []
        lines: List[str] = []
        for entry in (result or []):
            # RapidOCR 返回 [box, text, score]
            try:
                box, text, score = entry[0], entry[1], entry[2]
            except Exception:
                continue
            lines.append(str(text))
            items.append({"text": str(text), "score": float(score or 0), "box": box})
        return {"success": True, "text": "\n".join(lines), "items": items, "message": ""}
    except Exception as e:
        logger.warning(f"本地 OCR 失败: {e}")
        return {"success": False, "text": "", "items": [], "message": f"本地 OCR 失败: {e}"}


_VISION_OCR_PROMPT = """你是一个 OCR 文字识别助手。请识别图片中的全部文字，按阅读顺序输出纯文本。
要求：
1. 只输出识别到的文字，不要解释、不要 markdown 代码块。
2. 数学公式用 LaTeX 行内形式（$...$）。
3. 若图片中没有文字，输出空字符串。
"""


def _vision_ocr(path: str, prompt: str = "") -> Dict[str, Any]:
    data_url = _to_data_url(path)
    text = _run_vision_sync(prompt or _VISION_OCR_PROMPT, "请识别这张图片中的文字。", data_url)
    if text is None:
        return {"success": False, "text": "", "items": [], "message": "多模态识别失败（未配置 Key 或网络异常）"}
    return {"success": True, "text": text, "items": [], "message": "由多模态模型识别"}


def _run_vision_sync(system_prompt: str, user_content: str, data_url: str) -> Optional[str]:
    """在同步上下文里跑异步 vision_chat（若已有事件循环则用线程池）"""
    import asyncio
    coro = vision_chat(system_prompt, user_content, image_data_url=data_url)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


async def _vision_ocr_async(path: str, prompt: str = "") -> Dict[str, Any]:
    data_url = _to_data_url(path)
    text = await vision_chat(prompt or _VISION_OCR_PROMPT, "请识别这张图片中的文字。", image_data_url=data_url)
    if text is None:
        return {"success": False, "text": "", "items": [], "message": "多模态识别失败（未配置 Key 或网络异常）"}
    return {"success": True, "text": text, "items": [], "message": "由多模态模型识别"}


# ============= 对外接口 =============

async def ocr_image(image_path: str = "", image_base64: str = "", backend: str = "auto",
                    lang: str = "ch", prompt: str = "") -> Dict[str, Any]:
    """通用 OCR：本地优先，失败/未安装时回退多模态"""
    cfg = teaching_settings("ocr")
    backend = backend or "auto"
    if backend == "auto" and not cfg.get("local_enabled", True):
        backend = "vision"

    path = _resolve_path(image_path, image_base64)
    if not path:
        return {"success": False, "text": "", "items": [], "engine": "", "message": "请提供图片路径或 base64"}

    errors: List[str] = []

    if backend in ("auto", "local"):
        if _local_available():
            res = _local_ocr(path)
            if res.get("success") and res.get("text"):
                return {"success": True, "text": res["text"], "items": res["items"], "engine": "local", "message": "本地 OCR"}
            errors.append(res.get("message") or "本地 OCR 无结果")
        else:
            errors.append("未安装本地 OCR")
        if backend == "local":
            return {"success": False, "text": "", "items": [], "engine": "local",
                    "message": "；".join(errors) or "本地 OCR 不可用"}

    if backend in ("auto", "vision") and cfg.get("vision_enabled", True):
        if _vision_available():
            res = await _vision_ocr_async(path, prompt=prompt)
            if res.get("success") and res.get("text"):
                return {"success": True, "text": res["text"], "items": res["items"], "engine": "vision", "message": "多模态识别"}
            errors.append(res.get("message") or "多模态无结果")
        else:
            errors.append("未配置多模态视觉模型")

    return {"success": False, "text": "", "items": [], "engine": "",
            "message": "OCR 不可用：" + "；".join(errors)}


_QUESTION_PROMPT = """你是一个题目识别助手。请识别图片中的试题，并输出结构化 JSON。
要求：
1. 只输出 JSON 数组，不要解释文字，不要 markdown 代码块。
2. 每个元素字段：
   - stem: 题干（完整题面）
   - options: 选项数组（选择题按 A/B/C/D 顺序；非选择题给 []）
   - answer: 参考答案（图片中有的就填，没有留空字符串）
   - analysis: 解析（图片中有就填，没有留空）
   - qtype: choice/fill/judge/answer
   - knowledge_points: 知识点数组
   - difficulty: 1-5 整数
3. 只识别图片中真实出现的题目，不要编造。
"""


async def ocr_question(image_path: str = "", image_base64: str = "", backend: str = "auto") -> Dict[str, Any]:
    """图片题目识别：返回结构化题目数组（优先多模态，本地 OCR 文本作为兜底）"""
    path = _resolve_path(image_path, image_base64)
    if not path:
        return {"success": False, "questions": [], "message": "请提供图片路径或 base64"}

    # 多模态可直接结构化
    if backend in ("auto", "vision") and _vision_available():
        data_url = _to_data_url(path)
        raw = await vision_chat(_QUESTION_PROMPT, "请识别并结构化这张图片中的题目。", image_data_url=data_url)
        data = None
        if raw:
            data = parse_object(raw)
            if data is None:
                from .llm_utils import parse_json
                parsed = parse_json(raw)
                data = parsed if isinstance(parsed, list) else None
        if isinstance(data, list) and data:
            return {"success": True, "questions": data, "engine": "vision", "message": f"多模态识别 {len(data)} 道题"}

    # 回退：先 OCR 文本，再由调用方（extractor）结构化
    txt = await ocr_image(image_path=path, backend="auto")
    if txt.get("success") and txt.get("text"):
        return {"success": True, "questions": [], "text": txt["text"], "engine": txt.get("engine", ""),
                "message": "已识别文字，将由题目抽取器结构化"}
    return {"success": False, "questions": [], "message": txt.get("message") or "图片题目识别失败"}


async def ocr_screen(region: Optional[List[int]] = None, backend: str = "auto") -> Dict[str, Any]:
    """截取当前屏幕并 OCR（悬浮球 / 快捷目录匹配用）。

    region: [left, top, right, bottom]，None 表示整屏。
    """
    try:
        from PIL import ImageGrab
        if region and len(region) == 4:
            img = ImageGrab.grab(bbox=tuple(int(x) for x in region))
        else:
            img = ImageGrab.grab()
        os.makedirs(EDU_FILES_DIR, exist_ok=True)
        path = os.path.join(EDU_FILES_DIR, f"screen_{int(time.time() * 1000)}.png")
        img.save(path)
    except Exception as e:
        return {"success": False, "text": "", "message": f"屏幕截图失败: {e}", "path": ""}
    res = await ocr_image(image_path=path, backend=backend)
    res["path"] = path
    return res