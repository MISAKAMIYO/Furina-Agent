"""
网络试题

三种来源:
- api        从第三方题库 API 获取（可在设置中配置 api_url / api_key，通用 JSON 适配）
- ai_generate AI 依据知识点自主生成试题（含答案与解析）
- ai_web      AI 先联网检索公开资料，再据此生成试题（离线/无网时自动回退 ai_generate）

对外接口:
- fetch_questions(request) -> dict
- list_records() / get_record() / delete_record()
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from html import unescape
from typing import Any, Dict, List, Optional

from zhiyan_web.db.database import execute_delete, execute_insert, execute_query

from .llm_utils import one_shot, parse_array
from .models import normalize_difficulty, normalize_qtype, teaching_settings
from .parsers.extractor import _normalize_item

logger = logging.getLogger("ZhiYanWeb.Edu")

_GEN_PROMPT = """你是资深命题教师。请围绕用户给定的知识点/关键词，命制符合要求的试题，输出 JSON 数组。

要求：
1. 只输出 JSON 数组，不要解释文字，不要 markdown 代码块。
2. 每个元素字段：
   - stem: 题干（完整、严谨）
   - options: 选项数组（选择题按 A/B/C/D 顺序；非选择题给 []）
   - answer: 参考答案（必须给出）
   - analysis: 解题过程与详细解析（必须给出）
   - qtype: choice/fill/judge/answer
   - knowledge_points: 知识点数组
   - difficulty: 1-5 整数
3. 题目要符合给定学科与年级，难度贴合要求；不要重复、不要出与主题无关的题。
4. 数学公式用 LaTeX 行内形式（$...$）。
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _record_row(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    for key in ("payload", "saved_ids"):
        try:
            item[key] = json.loads(row.get(key) or "[]")
        except (json.JSONDecodeError, TypeError):
            item[key] = []
    return item


# ============= 主入口 =============

async def fetch_questions(request: Dict[str, Any]) -> Dict[str, Any]:
    """按模式获取网络试题，返回 {success, mode, questions, saved_ids, message}"""
    cfg = teaching_settings("network")
    if not cfg.get("enabled", True):
        return {"success": False, "mode": "", "questions": [], "message": "网络试题功能已在设置中关闭"}

    mode = str(request.get("mode") or cfg.get("provider") or "auto").strip().lower()
    query = str(request.get("query") or "").strip()
    subject = str(request.get("subject") or "").strip()
    grade = str(request.get("grade") or "").strip()
    qtype = str(request.get("qtype") or "").strip()
    difficulty = request.get("difficulty")
    try:
        count = max(1, min(int(request.get("count") or cfg.get("default_count") or 5), 20))
    except Exception:
        count = 5

    if not query and not subject:
        return {"success": False, "mode": mode, "questions": [], "message": "请填写知识点/关键词或学科"}

    if mode == "auto":
        mode = "api" if (request.get("api_url") or cfg.get("api_url")) else "ai_generate"

    questions: List[Dict[str, Any]] = []
    note = ""

    if mode == "api":
        api_url = str(request.get("api_url") or cfg.get("api_url") or "")
        if not api_url:
            mode, note = "ai_generate", "未配置题库 API，已改用 AI 生成。"
        else:
            res = await _api_fetch(api_url, cfg.get("api_key") or "", query, subject, count)
            questions = res.get("questions") or []
            note = res.get("message") or ""
            if not questions:
                mode, note = "ai_generate", (note or "题库 API 无结果") + "已改用 AI 生成。"

    if mode == "ai_generate":
        questions = await _ai_generate(query, subject, grade, count, qtype, difficulty)
    elif mode == "ai_web":
        web_res = await _ai_from_web(query, subject, grade, count, qtype, difficulty)
        questions = web_res.get("questions") or []
        note = web_res.get("message") or ""
        if not questions:
            questions = await _ai_generate(query, subject, grade, count, qtype, difficulty)
            note = (note + " " if note else "") + "联网检索受限，已回退 AI 自主生成。"

    if not questions:
        return {"success": False, "mode": mode, "questions": [], "message": note or "未获取到题目（可能未配置模型或网络异常）"}

    # 入库
    saved_ids: List[int] = []
    if bool(request.get("save_to_bank", cfg.get("save_to_bank", True))):
        from . import bank
        for q in questions:
            qid = bank.add_question({**q, "subject": subject or q.get("subject") or "",
                                     "grade": grade, "source": "web_" + mode})
            if qid:
                saved_ids.append(qid)

    record_id = execute_insert(
        "INSERT INTO edu_web_questions (source_type, query, subject, grade, payload, saved_ids, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (mode, query, subject, grade, json.dumps(questions, ensure_ascii=False),
         json.dumps(saved_ids), _now()),
    )

    msg = f"[{_mode_label(mode)}] 获取到 {len(questions)} 道题"
    if saved_ids:
        msg += f"，已入库 {len(saved_ids)} 道"
    if note:
        msg += f"。{note}"
    return {
        "success": True, "mode": mode, "questions": questions,
        "saved_ids": saved_ids, "record_id": record_id, "message": msg,
    }


def _mode_label(mode: str) -> str:
    return {"api": "网络题库 API", "ai_generate": "AI 生成", "ai_web": "AI 联网获取"}.get(mode, mode)


# ============= 各模式实现 =============

async def _ai_generate(query: str, subject: str, grade: str, count: int,
                       qtype: str, difficulty: Any) -> List[Dict[str, Any]]:
    tips = []
    if qtype:
        tips.append(f"题型限定：{_qtype_label(qtype)}")
    if difficulty:
        tips.append(f"难度：{normalize_difficulty(difficulty)}/5")
    user = (
        f"学科：{subject or '未指定'}；年级：{grade or '未指定'}。\n"
        f"知识点/关键词：{query or '（按学科常规知识点）'}\n"
        f"{'；'.join(tips)}\n请命制 {count} 道题。"
    )
    raw = await one_shot(_GEN_PROMPT, user, temperature=0.7, max_tokens=4000)
    arr = parse_array(raw) if raw else None
    return _normalize_list(arr) if arr else []


async def _ai_from_web(query: str, subject: str, grade: str, count: int,
                       qtype: str, difficulty: Any) -> Dict[str, Any]:
    snippets = await _web_search(f"{subject or ''} {query} 试题 例题".strip())
    if not snippets:
        return {"questions": [], "message": "联网检索未获取到可用资料"}
    context = "\n".join(f"- {s}" for s in snippets[:8])
    tips = []
    if qtype:
        tips.append(f"题型限定：{_qtype_label(qtype)}")
    if difficulty:
        tips.append(f"难度：{normalize_difficulty(difficulty)}/5")
    user = (
        f"学科：{subject or '未指定'}；年级：{grade or '未指定'}。\n"
        f"参考以下网络检索到的公开资料，围绕「{query}」命制 {count} 道题：\n"
        f"{'；'.join(tips)}\n\n资料：\n{context}"
    )
    raw = await one_shot(_GEN_PROMPT, user, temperature=0.6, max_tokens=4000)
    arr = parse_array(raw) if raw else None
    questions = _normalize_list(arr) if arr else []
    return {"questions": questions, "message": f"已参考 {len(snippets)} 条网络资料生成"}


async def _web_search(query: str, limit: int = 8) -> List[str]:
    """轻量联网检索（DuckDuckGo HTML 接口，无需 Key），失败返回空"""
    if not query.strip():
        return []
    url = "https://html.duckduckgo.com/html/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ZhiYanEdu/1.0"}
    try:
        import httpx
        cfg = teaching_settings("network")
        timeout = float(cfg.get("timeout", 15) or 15)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.post(url, data={"q": query}, headers=headers)
            resp.raise_for_status()
            html_text = resp.text
    except Exception as e:
        logger.info(f"联网检索失败: {e}")
        return []

    snippets: List[str] = []
    for m in re.finditer(r'class="result__snippet"[^>]*>(.*?)</a>', html_text, re.S):
        text = re.sub(r"<[^>]+>", "", m.group(1))
        text = unescape(text).strip()
        if text:
            snippets.append(text)
        if len(snippets) >= limit:
            break
    if not snippets:
        # 兜底：抓取结果标题
        for m in re.finditer(r'class="result__a"[^>]*>(.*?)</a>', html_text, re.S):
            text = unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            if text:
                snippets.append(text)
            if len(snippets) >= limit:
                break
    return snippets


async def _api_fetch(api_url: str, api_key: str, query: str, subject: str, count: int) -> Dict[str, Any]:
    """通用题库 API 适配：GET/POST 返回 JSON，兼容常见字段结构"""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    params = {"q": query, "keyword": query, "subject": subject, "count": count, "limit": count}
    try:
        import httpx
        cfg = teaching_settings("network")
        timeout = float(cfg.get("timeout", 15) or 15)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(api_url, params=params, headers=headers)
            if resp.status_code >= 400:
                resp = await client.post(api_url, json=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.info(f"题库 API 请求失败: {e}")
        return {"questions": [], "message": f"题库 API 请求失败: {e}"}

    arr = None
    if isinstance(data, list):
        arr = data
    elif isinstance(data, dict):
        for key in ("questions", "data", "items", "result", "list", "records"):
            if isinstance(data.get(key), list):
                arr = data[key]
                break
    questions = _normalize_list(arr) if arr else []
    return {"questions": questions, "message": f"题库 API 返回 {len(questions)} 道题"}


def _normalize_list(arr: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in arr:
        if not isinstance(item, dict):
            continue
        norm = _normalize_item(item)
        if not norm:
            continue
        key = re.sub(r"\s+", "", norm.get("stem", ""))[:100]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


def _qtype_label(qtype: str) -> str:
    return {"choice": "选择题", "fill": "填空题", "judge": "判断题", "answer": "解答题"}.get(normalize_qtype(qtype), qtype)


# ============= 记录存取 =============

def list_records(limit: int = 50) -> List[Dict[str, Any]]:
    rows = execute_query(
        "SELECT id, source_type, query, subject, grade, saved_ids, created_at "
        "FROM edu_web_questions ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )
    return [_record_row(r) for r in rows]


def get_record(record_id: int) -> Optional[Dict[str, Any]]:
    row = execute_query("SELECT * FROM edu_web_questions WHERE id = ?", (int(record_id),), fetch_one=True)
    return _record_row(row) if row else None


def delete_record(record_id: int) -> bool:
    return execute_delete("DELETE FROM edu_web_questions WHERE id = ?", (int(record_id),)) > 0