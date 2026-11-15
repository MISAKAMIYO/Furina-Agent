"""
希沃原生格式框架（预留 / 研究）

说明：希沃白板（EasiNote5）的 .seewo / .enbx 为私有压缩格式，无公开规范，无法可靠生成。
本模块只提供「框架」：
- 导出一份与希沃课件结构对齐的中间描述文件 *.seewo.json（页面 / 元素 / 讲稿），
  便于后续研究格式后写转换器，或用第三方工具导入。
- 附带生成一份 HTML 幻灯片副本（希沃可用浏览器/白板「网页课件」方式打开）。

开启方式：settings.json → edu.teaching.slides.seewo_enabled = true

对外接口: build_seewo(outline, out_dir, html_path="") -> dict
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger("ZhiYanWeb.Edu")


def build_seewo(outline: Dict[str, Any], out_dir: str, html_path: str = "") -> Dict[str, Any]:
    """生成希沃格式中间描述文件（框架）。

    返回 {"success", "path", "framework": True, "message"}
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
        base = _safe_name(str(outline.get("title") or "教学课件"))
        desc_path = os.path.join(out_dir, f"{base}.seewo.json")

        pages = []
        for page in (outline.get("slides") or []):
            pages.append({
                "index": int(page.get("index") or (len(pages) + 1)),
                "name": page.get("title") or "",
                "layout": page.get("type") or "content",
                "elements": _elements_of(page),
                "notes": page.get("notes") or "",
            })

        doc = {
            "format": "seewo-framework",
            "version": "0.1",
            "generator": "ZhiYan AI 教辅",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "note": "希沃 .seewo/.enbx 为私有格式，本文件为结构对齐的中间描述，供后续转换/导入研究使用。",
            "meta": {
                "title": outline.get("title") or "",
                "subject": outline.get("subject") or "",
                "grade": outline.get("grade") or "",
                "page_count": len(pages),
            },
            "objectives": outline.get("objectives") or [],
            "outline": outline.get("outline") or [],
            "pages": pages,
            "html_preview": html_path,
        }
        with open(desc_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        return {
            "success": True,
            "framework": True,
            "path": desc_path,
            "message": "已生成希沃格式中间描述文件（框架，原生 .seewo 需后续研究格式转换）",
        }
    except Exception as e:
        logger.error(f"生成希沃框架文件失败: {e}")
        return {"success": False, "framework": True, "path": "", "message": f"生成希沃框架文件失败: {e}"}


def _elements_of(page: Dict[str, Any]) -> list:
    elements = []
    title = page.get("title")
    if title:
        elements.append({"type": "text", "role": "title", "text": title})
    for b in (page.get("bullets") or []):
        elements.append({"type": "text", "role": "body", "text": str(b)})
    ex = page.get("example")
    if isinstance(ex, dict):
        if ex.get("question"):
            elements.append({"type": "text", "role": "question", "text": ex["question"]})
        for i, o in enumerate(ex.get("options") or []):
            elements.append({"type": "text", "role": "option", "text": f"{'ABCDEFGH'[i] if i < 8 else '?'}. {o}"})
        if ex.get("answer"):
            elements.append({"type": "text", "role": "answer", "text": ex["answer"]})
        if ex.get("analysis"):
            elements.append({"type": "text", "role": "analysis", "text": ex["analysis"]})
    return elements


def _safe_name(name: str) -> str:
    import re
    return re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", name).strip("_") or "lesson"