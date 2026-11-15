"""
教学幻灯片服务（编排 + 存取）

流程: AI 生成大纲 → 写入 edu_slides → 导出 pptx / html / seewo 文件

对外接口:
- generate_slides(request)   生成一份教学幻灯片
- list_slides() / get_slide() / update_slide() / delete_slide()
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from zhiyan_web.db.database import execute_delete, execute_insert, execute_query, execute_update

from ..models import EDU_SLIDES_DIR, teaching_settings
from . import generator, html_render, pptx_writer, seewo

logger = logging.getLogger("ZhiYanWeb.Edu")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", name or "").strip("_") or "lesson"


def _row_to_slide(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    try:
        item["outline"] = json.loads(row.get("outline") or "{}")
    except (json.JSONDecodeError, TypeError):
        item["outline"] = {}
    item["slide_count"] = int(row.get("slide_count") or 0)
    return item


async def generate_slides(request: Dict[str, Any]) -> Dict[str, Any]:
    """生成教学幻灯片（AI 大纲 + 导出文件 + 入库）"""
    cfg = teaching_settings("slides")
    topic = str(request.get("topic") or "").strip()
    if not topic:
        return {"success": False, "message": "请填写教学主题", "data": None}

    subject = str(request.get("subject") or cfg.get("default_subject") or "").strip()
    grade = str(request.get("grade") or cfg.get("default_grade") or "").strip()
    try:
        slide_count = int(request.get("slide_count") or cfg.get("default_slide_count") or 12)
    except Exception:
        slide_count = 12
    theme = str(request.get("theme") or cfg.get("theme") or "default")
    export_pptx = bool(request.get("export_pptx", cfg.get("export_pptx", True)))
    export_html = bool(request.get("export_html", cfg.get("export_html", True)))

    gen = await generator.generate_outline(
        topic=topic, subject=subject, grade=grade,
        objectives=str(request.get("objectives") or ""),
        outline=str(request.get("outline") or ""),
        content=str(request.get("content") or ""),
        slide_count=slide_count,
    )
    if not gen.get("success") or not gen.get("outline"):
        return {"success": False, "message": gen.get("message") or "生成失败", "data": None}

    outline = gen["outline"]
    outline["subject"] = subject
    outline["grade"] = grade
    outline["theme"] = theme

    stamp = str(int(time.time()))
    base = f"{stamp}_{_safe_name(outline.get('title') or topic)}"
    pptx_path = ""
    html_path = ""
    seewo_path = ""
    messages: List[str] = [str(gen.get("message") or "")]

    if export_html:
        res = html_render.render_html(outline, os.path.join(EDU_SLIDES_DIR, base + ".html"), title=outline.get("title") or topic)
        if res.get("success"):
            html_path = res["path"]
        messages.append(str(res.get("message")))

    if export_pptx:
        res = pptx_writer.write_pptx(outline, os.path.join(EDU_SLIDES_DIR, base + ".pptx"))
        if res.get("success"):
            pptx_path = res["path"]
        messages.append(str(res.get("message")))

    if cfg.get("seewo_enabled"):
        res = seewo.build_seewo(outline, EDU_SLIDES_DIR, html_path)
        if res.get("success"):
            seewo_path = res["path"]
        messages.append(str(res.get("message")))

    now = _now()
    slide_id = execute_insert(
        "INSERT INTO edu_slides "
        "(title, subject, grade, topic, slide_count, outline, pptx_path, html_path, seewo_path, status, source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(outline.get("title") or topic),
            subject,
            grade,
            topic,
            len(outline.get("slides") or []),
            json.dumps(outline, ensure_ascii=False),
            pptx_path,
            html_path,
            seewo_path,
            "ok",
            "ai",
            now,
            now,
        ),
    )
    if not slide_id:
        return {"success": False, "message": "幻灯片已生成，但记录入库失败", "data": {"outline": outline, "pptx_path": pptx_path, "html_path": html_path}}

    return {
        "success": True,
        "id": slide_id,
        "message": "；".join(m for m in messages if m),
        "data": get_slide(slide_id),
    }


def list_slides(limit: int = 100) -> List[Dict[str, Any]]:
    rows = execute_query(
        "SELECT id, title, subject, grade, topic, slide_count, pptx_path, html_path, seewo_path, status, source, created_at "
        "FROM edu_slides ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )
    return [dict(r) for r in rows]


def get_slide(slide_id: int) -> Optional[Dict[str, Any]]:
    row = execute_query("SELECT * FROM edu_slides WHERE id = ?", (int(slide_id),), fetch_one=True)
    return _row_to_slide(row) if row else None


def update_slide(slide_id: int, patch: Dict[str, Any]) -> bool:
    fields: List[str] = []
    params: List[Any] = []
    for key in ("title", "subject", "grade", "topic"):
        if patch.get(key) is not None:
            fields.append(f"{key} = ?")
            params.append(str(patch[key]))
    if patch.get("outline") is not None:
        outline = patch["outline"]
        fields.append("outline = ?")
        params.append(json.dumps(outline, ensure_ascii=False) if isinstance(outline, (dict, list)) else str(outline))
        if isinstance(outline, dict):
            fields.append("slide_count = ?")
            params.append(len(outline.get("slides") or []))
    if not fields:
        return False
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(int(slide_id))
    return execute_update(f"UPDATE edu_slides SET {', '.join(fields)} WHERE id = ?", tuple(params)) > 0


def delete_slide(slide_id: int, remove_files: bool = False) -> Dict[str, Any]:
    row = get_slide(slide_id)
    if not row:
        return {"success": False, "message": "幻灯片不存在"}
    if remove_files:
        for key in ("pptx_path", "html_path", "seewo_path"):
            p = row.get(key)
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as e:
                    logger.debug(f"删除文件失败 {p}: {e}")
    affected = execute_delete("DELETE FROM edu_slides WHERE id = ?", (int(slide_id),))
    return {"success": affected > 0, "message": "已删除" if affected > 0 else "删除失败"}


def regenerate_files(slide_id: int) -> Dict[str, Any]:
    """按当前 outline 重新导出 pptx / html（大纲手工编辑后可用）"""
    row = get_slide(slide_id)
    if not row:
        return {"success": False, "message": "幻灯片不存在"}
    outline = row.get("outline") or {}
    if not outline.get("slides"):
        return {"success": False, "message": "大纲为空，无法导出"}
    stamp = str(int(time.time()))
    base = f"{stamp}_{_safe_name(str(outline.get('title') or row.get('title')))}"
    html_path = row.get("html_path") or ""
    pptx_path = row.get("pptx_path") or ""
    res_html = html_render.render_html(outline, os.path.join(EDU_SLIDES_DIR, base + ".html"))
    if res_html.get("success"):
        html_path = res_html["path"]
    res_pptx = pptx_writer.write_pptx(outline, os.path.join(EDU_SLIDES_DIR, base + ".pptx"))
    if res_pptx.get("success"):
        pptx_path = res_pptx["path"]
    execute_update(
        "UPDATE edu_slides SET pptx_path = ?, html_path = ?, updated_at = ? WHERE id = ?",
        (pptx_path, html_path, _now(), int(slide_id)),
    )
    return {
        "success": True,
        "message": "已重新导出：" + "；".join(
            m for m in (res_html.get("message"), res_pptx.get("message")) if m
        ),
        "data": get_slide(slide_id),
    }