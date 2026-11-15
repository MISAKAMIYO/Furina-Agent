"""
快捷目录

把课件（PPTX/PDF/DOCX/XLSX/TXT）解析成「纲要 + 每页元素记录」，供悬浮球做快捷跳转：
- AI 总结课件纲要，并记录每一页的标题与元素（要点）
- 悬浮球截屏 OCR 识别当前页 → 与目录匹配 → 得到当前页码 → 计算差值 → 向 WPS/希沃发送翻页键

对外接口:
- build_catalog(request)      构建目录
- list_catalogs()/get_catalog()/delete_catalog()
- match_text(catalog, text)   用 OCR 文本匹配页码
- jump_by_delta(delta)        向当前前台窗口发送 PageDown/PageUp 实现跳转
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from zhiyan_web.db.database import execute_delete, execute_insert, execute_query

from .llm_utils import one_shot, parse_array
from .models import EDU_CATALOG_DIR, teaching_settings
from .parsers.document import extract_pages

logger = logging.getLogger("ZhiYanWeb.Edu")

_PAGES_PROMPT = """你是课件结构分析助手。下面是一份课件的逐页文本，请为每一页生成目录条目，输出 JSON 数组。

要求：
1. 只输出 JSON 数组，不要解释文字，不要 markdown 代码块。
2. 数组第 i 个元素对应第 i 页，字段：
   - page: 页码（整数，从 1 开始）
   - title: 该页标题（简短，8-20 字；纯图片/无文字页写「图片页」）
   - elements: 该页关键元素数组（要点/图表/公式/例题等，2-5 条）
3. 严格与输入页数一一对应，不要增删页；不要编造没有的内容。
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm(text: str) -> str:
    return re.sub(r"[\s\u3000·．.、,，;；:：()（）\[\]【】\"'“”‘’<>《》]+", "", (text or "")).lower()


def _safe_name(name: str) -> str:
    """把标题转成安全的文件名片段（保留中英文与数字）"""
    return re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", str(name or "")).strip("_") or "catalog"


def _row_to_catalog(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    try:
        item["outline"] = json.loads(row.get("outline") or "[]")
    except (json.JSONDecodeError, TypeError):
        item["outline"] = []
    item["page_count"] = int(row.get("page_count") or 0)
    return item


# ============= 构建 =============

async def build_catalog(request: Dict[str, Any]) -> Dict[str, Any]:
    """构建快捷目录"""
    text = str(request.get("text") or "").strip()
    path = str(request.get("path") or "").strip()
    course_id = int(request.get("course_id") or 0)
    with_ocr = bool(request.get("with_ocr"))

    title = str(request.get("title") or "").strip()
    subject = str(request.get("subject") or "").strip()
    file_type = ""

    if text and not path:
        blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()] or [text]
        pages = [{"page": i + 1, "text": b} for i, b in enumerate(blocks)]
    else:
        if not path and course_id:
            from .. import bank
            course = bank.get_course(course_id)
            if not course:
                return {"success": False, "message": "课件不存在", "data": None}
            path = course.get("stored_path") or ""
            title = title or course.get("title") or ""
            subject = subject or course.get("subject") or ""
        if not path:
            return {"success": False, "message": "请提供课件路径、课件 ID 或文本", "data": None}
        parsed = extract_pages(path)
        if not parsed.get("success"):
            return {"success": False, "message": parsed.get("message") or "解析失败", "data": None}
        pages = parsed["pages"]
        file_type = parsed.get("file_type", "")
        title = title or os.path.splitext(os.path.basename(path))[0]

    # 扫描件：有 OCR 需求且本地 OCR 可用时，逐页补文字（仅 PDF 可渲染）
    if with_ocr and path:
        pages = await _ocr_fill_pages(path, pages)

    outline = await _summarize_pages(pages, subject=subject)

    now = _now()
    catalog_id = execute_insert(
        "INSERT INTO edu_catalogs (course_id, title, subject, page_count, outline, source_path, file_type, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (course_id, title, subject, len(outline), json.dumps(outline, ensure_ascii=False),
         path or "", file_type, now, now),
    )
    # 落盘一份 JSON，便于外部程序 / 悬浮球离线读取（文件名带目录 id，便于删除时定位）
    try:
        os.makedirs(EDU_CATALOG_DIR, exist_ok=True)
        file_path = os.path.join(EDU_CATALOG_DIR, f"{catalog_id}_{_safe_name(title)}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump({"id": catalog_id, "title": title, "subject": subject, "outline": outline},
                      f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.debug(f"目录落盘失败: {e}")

    return {
        "success": True,
        "id": catalog_id,
        "message": f"已生成快捷目录：{len(outline)} 页",
        "data": get_catalog(catalog_id),
    }


async def _ocr_fill_pages(path: str, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """对无文字的页用 OCR 补文字（PDF 用 PyMuPDF 渲染成图片）"""
    empty_idx = [i for i, p in enumerate(pages) if len((p.get("text") or "").strip()) < 5]
    if not empty_idx or os.path.splitext(path)[1].lower() != ".pdf":
        return pages
    try:
        from .ocr import ocr_image
        import pymupdf
    except Exception:
        return pages
    try:
        doc = pymupdf.open(path)
    except Exception:
        return pages
    try:
        for i in empty_idx:
            try:
                page = doc[i]
                pix = page.get_pixmap(dpi=140)
                tmp = os.path.join(EDU_CATALOG_DIR, f"_page_{int(time.time() * 1000)}.png")
                os.makedirs(EDU_CATALOG_DIR, exist_ok=True)
                pix.save(tmp)
                res = await ocr_image(image_path=tmp, backend="auto")
                if res.get("success") and res.get("text"):
                    pages[i]["text"] = res["text"]
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"第 {i + 1} 页 OCR 失败: {e}")
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return pages


async def _summarize_pages(pages: List[Dict[str, Any]], subject: str = "") -> List[Dict[str, Any]]:
    """AI 总结每页标题与元素；失败回退启发式"""
    # 分片：每片最多 20 页
    outline: List[Dict[str, Any]] = []
    chunk_size = 20
    for start in range(0, len(pages), chunk_size):
        chunk = pages[start:start + chunk_size]
        lines = []
        for p in chunk:
            body = (p.get("text") or "").strip().replace("\n", " ")[:400]
            lines.append(f"第 {p['page']} 页：{body}")
        user = f"学科：{subject or '未指定'}。以下是课件逐页文本：\n\n" + "\n".join(lines)
        raw = await one_shot(_PAGES_PROMPT, user, temperature=0.2, max_tokens=3000)
        arr = parse_array(raw) if raw else None
        got = _align_pages(chunk, arr) if arr else None
        outline.extend(got if got else [_heuristic_page(p) for p in chunk])
    return outline


def _align_pages(chunk: List[Dict[str, Any]], arr: List[Any]) -> Optional[List[Dict[str, Any]]]:
    """把 LLM 返回按页码对齐到输入页"""
    by_page: Dict[int, Dict[str, Any]] = {}
    for item in arr:
        if not isinstance(item, dict):
            continue
        try:
            page = int(item.get("page") or 0)
        except Exception:
            continue
        if page <= 0:
            continue
        els = item.get("elements") or []
        if isinstance(els, str):
            els = [x.strip() for x in re.split(r"[,，、;；\n]+", els) if x.strip()]
        by_page[page] = {
            "page": page,
            "title": str(item.get("title") or "").strip(),
            "elements": [str(x).strip() for x in els] if isinstance(els, list) else [],
        }
    if not by_page:
        return None
    out = []
    for p in chunk:
        item = by_page.get(p["page"]) or _heuristic_page(p)
        item["text"] = (p.get("text") or "")[:600]
        out.append(item)
    return out


def _heuristic_page(page: Dict[str, Any]) -> Dict[str, Any]:
    """启发式：首行作标题，其余行作元素"""
    text = (page.get("text") or "").strip()
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    title = lines[0][:40] if lines else "空白页"
    elements = [ln[:60] for ln in lines[1:7]]
    return {"page": page.get("page"), "title": title, "elements": elements, "text": text[:600]}


# ============= 匹配 =============

def score_page(query_norm: str, item: Dict[str, Any]) -> float:
    """计算 OCR 文本与某一页的匹配度（0-1）"""
    parts = [str(item.get("title") or "")] + [str(x) for x in (item.get("elements") or [])] + [str(item.get("text") or "")]
    page_norm = _norm("".join(parts))
    if not query_norm or not page_norm:
        return 0.0
    # 1) 子串包含：OCR 文本片段出现在页文本中，给较高分
    ratio = difflib.SequenceMatcher(None, query_norm, page_norm).ratio()
    contain = 0.0
    qlen = len(query_norm)
    if qlen >= 6:
        # 取若干片段判断是否被页文本包含
        for size in (min(qlen, 40), min(qlen, 24), min(qlen, 12)):
            if size <= 0:
                continue
            seg = query_norm[:size]
            if seg in page_norm:
                contain = max(contain, size / max(qlen, 1) * 0.9)
    return max(ratio, contain)


def match_text(catalog: Dict[str, Any], text: str, threshold: Optional[float] = None) -> Dict[str, Any]:
    """用 OCR 文本匹配页码，返回 {page, score, title, matched}"""
    cfg = teaching_settings("catalog")
    if threshold is None:
        try:
            threshold = float(cfg.get("match_threshold", 0.55))
        except Exception:
            threshold = 0.55
    query_norm = _norm(text)
    outline = catalog.get("outline") or []
    best = None
    best_score = 0.0
    for item in outline:
        s = score_page(query_norm, item)
        if s > best_score:
            best_score = s
            best = item
    if not best:
        return {"page": 0, "score": 0.0, "title": "", "matched": False}
    return {
        "page": int(best.get("page") or 0),
        "score": round(best_score, 3),
        "title": best.get("title") or "",
        "matched": best_score >= float(threshold),
    }


# ============= 翻页（向 WPS / 希沃发送按键） =============

def jump_by_delta(delta: int) -> Dict[str, Any]:
    """向当前前台窗口发送 delta 次 PageDown（正）/ PageUp（负），实现无快捷键课件的跳转"""
    try:
        import pyautogui
    except Exception as e:
        return {"success": False, "message": f"缺少 pyautogui，无法发送翻页键: {e}"}
    try:
        n = int(delta)
    except Exception:
        return {"success": False, "message": "翻页步数无效"}
    if n == 0:
        return {"success": True, "message": "已在目标页", "steps": 0}
    key = "pagedown" if n > 0 else "pageup"
    try:
        pyautogui.press(key, presses=abs(n), interval=0.05)
        return {"success": True, "message": f"已发送 {abs(n)} 次 {key}", "steps": abs(n)}
    except Exception as e:
        return {"success": False, "message": f"发送翻页键失败: {e}"}


# ============= 存取 =============

def list_catalogs(limit: int = 100) -> List[Dict[str, Any]]:
    rows = execute_query(
        "SELECT id, course_id, title, subject, page_count, source_path, file_type, created_at "
        "FROM edu_catalogs ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )
    return [dict(r) for r in rows]


def get_catalog(catalog_id: int) -> Optional[Dict[str, Any]]:
    row = execute_query("SELECT * FROM edu_catalogs WHERE id = ?", (int(catalog_id),), fetch_one=True)
    return _row_to_catalog(row) if row else None


def delete_catalog(catalog_id: int, remove_file: bool = True) -> Dict[str, Any]:
    item = get_catalog(catalog_id)
    if not item:
        return {"success": False, "message": "目录不存在"}
    if remove_file and os.path.isdir(EDU_CATALOG_DIR):
        # 落盘文件名以目录 id 开头，按前缀精确清理，避免误删其它目录
        prefix = f"{int(catalog_id)}_"
        for name in os.listdir(EDU_CATALOG_DIR):
            if name.startswith(prefix) and name.endswith(".json"):
                try:
                    os.remove(os.path.join(EDU_CATALOG_DIR, name))
                except Exception as e:
                    logger.debug(f"删除目录文件失败 {name}: {e}")
    affected = execute_delete("DELETE FROM edu_catalogs WHERE id = ?", (int(catalog_id),))
    return {"success": affected > 0, "message": "已删除" if affected > 0 else "删除失败"}