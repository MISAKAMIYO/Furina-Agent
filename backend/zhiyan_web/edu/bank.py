"""
题库服务

职责:
- 课件（edu_courses）与题目（edu_questions）的增删改查
- 学科 / 题型 / 难度 / 知识点 统计
- 导入编排：文档解析 → 题目抽取 → 入库 → 统计回填

对外主要接口:
- import_document()   把一份课件/作业导入题库
- list_questions()    题库检索
- stats() / tags()    统计与标签
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from zhiyan_web.db.database import execute_delete, execute_insert, execute_query, execute_update

from .models import EDU_FILES_DIR, normalize_difficulty, normalize_qtype

logger = logging.getLogger("ZhiYanWeb.Edu")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json_list(value: Any) -> List[str]:
    """把数据库里的 JSON 字符串还原为字符串列表"""
    if isinstance(value, list):
        return [str(v) for v in value]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(parsed, list):
        return [str(v) for v in parsed if str(v).strip()]
    return []


def _dump_list(value: Any) -> str:
    if value is None:
        value = []
    if isinstance(value, str):
        value = [v.strip() for v in re.split(r"[,，、;；]+", value) if v.strip()]
    if not isinstance(value, list):
        value = [str(value)]
    return json.dumps([str(v).strip() for v in value if str(v).strip()], ensure_ascii=False)


def _row_to_question(row: Dict[str, Any]) -> Dict[str, Any]:
    """数据库行 → 题目 dict（JSON 字段还原为列表）"""
    item = dict(row)
    item["options"] = _json_list(row.get("options"))
    item["knowledge_points"] = _json_list(row.get("knowledge_points"))
    try:
        item["difficulty"] = int(row.get("difficulty") or 3)
    except Exception:
        item["difficulty"] = 3
    return item


def _row_to_course(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    item.pop("raw_text", None)          # 原文体积大，列表接口不返回
    try:
        item["question_count"] = int(row.get("question_count") or 0)
    except Exception:
        item["question_count"] = 0
    return item


# ============= 课件 =============

def add_course(
    title: str,
    subject: str = "",
    grade: str = "",
    filename: str = "",
    file_type: str = "",
    stored_path: str = "",
    raw_text: str = "",
    status: str = "ok",
    source: str = "local",
) -> Optional[int]:
    """新增课件记录，返回记录 id"""
    now = _now()
    query = (
        "INSERT INTO edu_courses "
        "(title, subject, grade, filename, file_type, stored_path, raw_text, question_count, status, source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)"
    )
    params = (title, subject, grade, filename, file_type, stored_path,
              raw_text or "", status, source, now, now)
    return execute_insert(query, params)


def list_courses(limit: int = 200) -> List[Dict[str, Any]]:
    rows = execute_query(
        "SELECT * FROM edu_courses ORDER BY id DESC LIMIT ?", (int(limit),)
    )
    return [_row_to_course(r) for r in rows]


def get_course(course_id: int) -> Optional[Dict[str, Any]]:
    row = execute_query("SELECT * FROM edu_courses WHERE id = ?", (int(course_id),), fetch_one=True)
    return dict(row) if row else None


def delete_course(course_id: int, delete_questions: bool = True) -> Dict[str, Any]:
    """删除课件；可选同时删除其下题目"""
    course = get_course(course_id)
    if not course:
        return {"success": False, "message": "课件不存在"}
    removed_q = 0
    if delete_questions:
        removed_q = execute_delete("DELETE FROM edu_questions WHERE course_id = ?", (int(course_id),))
    affected = execute_delete("DELETE FROM edu_courses WHERE id = ?", (int(course_id),))
    if affected <= 0:
        return {"success": False, "message": "删除失败"}
    return {"success": True, "message": f"已删除课件及其 {removed_q} 道题目" if delete_questions
            else "已删除课件（题目保留）", "removed_questions": removed_q}


def update_course_stats(course_id: int) -> None:
    """按题目表回填课件的题量与状态"""
    rows = execute_query("SELECT COUNT(*) AS c FROM edu_questions WHERE course_id = ?", (int(course_id),))
    count = int(rows[0]["c"]) if rows else 0
    execute_update(
        "UPDATE edu_courses SET question_count = ?, status = ?, updated_at = ? WHERE id = ?",
        (count, "ok" if count else "empty", _now(), int(course_id)),
    )


def save_upload(data: bytes, filename: str) -> str:
    """把上传的文件落到 data/edu/files/，返回绝对路径"""
    os.makedirs(EDU_FILES_DIR, exist_ok=True)
    safe_name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", os.path.basename(filename or "upload.bin"))
    stored = os.path.join(EDU_FILES_DIR, f"{int(time.time())}_{safe_name}")
    with open(stored, "wb") as f:
        f.write(data or b"")
    return stored


# ============= 题目 =============

def add_question(data: Dict[str, Any]) -> Optional[int]:
    """新增单道题目，返回记录 id"""
    stem = str(data.get("stem") or "").strip()
    if not stem:
        return None
    now = _now()
    query = (
        "INSERT INTO edu_questions "
        "(course_id, subject, grade, qtype, stem, options, answer, analysis, knowledge_points, difficulty, source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    params = (
        int(data.get("course_id") or 0),
        str(data.get("subject") or ""),
        str(data.get("grade") or ""),
        normalize_qtype(data.get("qtype")),
        stem,
        _dump_list(data.get("options")),
        str(data.get("answer") or "").strip(),
        str(data.get("analysis") or "").strip(),
        _dump_list(data.get("knowledge_points")),
        normalize_difficulty(data.get("difficulty")),
        str(data.get("source") or "manual"),
        now,
        now,
    )
    return execute_insert(query, params)


def add_questions(
    items: List[Dict[str, Any]],
    course_id: int = 0,
    subject: str = "",
    grade: str = "",
    source: str = "import",
) -> int:
    """批量新增题目，返回成功条数"""
    added = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        payload.setdefault("course_id", course_id)
        if subject:
            payload.setdefault("subject", subject)
        if grade:
            payload.setdefault("grade", grade)
        payload.setdefault("source", source)
        if add_question(payload):
            added += 1
    return added


def list_questions(
    subject: str = "",
    qtype: str = "",
    difficulty: Optional[int] = None,
    keyword: str = "",
    course_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """题库检索，返回 {total, items}"""
    where: List[str] = []
    params: List[Any] = []
    if subject:
        where.append("subject = ?")
        params.append(subject)
    if qtype:
        where.append("qtype = ?")
        params.append(normalize_qtype(qtype))
    if difficulty:
        where.append("difficulty = ?")
        params.append(normalize_difficulty(difficulty))
    if course_id:
        where.append("course_id = ?")
        params.append(int(course_id))
    if keyword:
        where.append("(stem LIKE ? OR answer LIKE ? OR analysis LIKE ? OR knowledge_points LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like, like])

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total_rows = execute_query(f"SELECT COUNT(*) AS c FROM edu_questions {clause}", tuple(params))
    total = int(total_rows[0]["c"]) if total_rows else 0
    rows = execute_query(
        f"SELECT * FROM edu_questions {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params + [int(limit), int(offset)]),
    )
    return {"total": total, "items": [_row_to_question(r) for r in rows]}


def get_question(question_id: int) -> Optional[Dict[str, Any]]:
    row = execute_query("SELECT * FROM edu_questions WHERE id = ?", (int(question_id),), fetch_one=True)
    return _row_to_question(row) if row else None


def update_question(question_id: int, patch: Dict[str, Any]) -> bool:
    """按传入字段更新题目"""
    fields: List[str] = []
    params: List[Any] = []
    simple = ("stem", "answer", "analysis", "subject", "grade")
    for key in simple:
        if key in patch and patch[key] is not None:
            fields.append(f"{key} = ?")
            params.append(str(patch[key]))
    if patch.get("qtype") is not None:
        fields.append("qtype = ?")
        params.append(normalize_qtype(patch["qtype"]))
    if patch.get("difficulty") is not None:
        fields.append("difficulty = ?")
        params.append(normalize_difficulty(patch["difficulty"]))
    if patch.get("options") is not None:
        fields.append("options = ?")
        params.append(_dump_list(patch["options"]))
    if patch.get("knowledge_points") is not None:
        fields.append("knowledge_points = ?")
        params.append(_dump_list(patch["knowledge_points"]))
    if not fields:
        return False
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(int(question_id))
    affected = execute_update(
        f"UPDATE edu_questions SET {', '.join(fields)} WHERE id = ?", tuple(params)
    )
    return affected > 0


def delete_question(question_id: int) -> bool:
    return execute_delete("DELETE FROM edu_questions WHERE id = ?", (int(question_id),)) > 0


# ============= 统计 / 标签 =============

def subjects() -> List[str]:
    rows = execute_query(
        "SELECT subject, COUNT(*) AS c FROM edu_questions WHERE subject != '' GROUP BY subject ORDER BY c DESC"
    )
    return [r["subject"] for r in rows]


def stats() -> Dict[str, Any]:
    """题库总览统计"""
    total_rows = execute_query("SELECT COUNT(*) AS c FROM edu_questions")
    course_rows = execute_query("SELECT COUNT(*) AS c FROM edu_courses")
    by_type = execute_query(
        "SELECT qtype, COUNT(*) AS c FROM edu_questions GROUP BY qtype ORDER BY c DESC"
    )
    by_subject = execute_query(
        "SELECT subject, COUNT(*) AS c FROM edu_questions WHERE subject != '' GROUP BY subject ORDER BY c DESC LIMIT 20"
    )
    by_difficulty = execute_query(
        "SELECT difficulty, COUNT(*) AS c FROM edu_questions GROUP BY difficulty ORDER BY difficulty"
    )
    with_answer = execute_query(
        "SELECT COUNT(*) AS c FROM edu_questions WHERE answer != ''"
    )
    return {
        "question_total": int(total_rows[0]["c"]) if total_rows else 0,
        "course_total": int(course_rows[0]["c"]) if course_rows else 0,
        "with_answer": int(with_answer[0]["c"]) if with_answer else 0,
        "by_type": [{"name": r["qtype"], "count": int(r["c"])} for r in by_type],
        "by_subject": [{"name": r["subject"], "count": int(r["c"])} for r in by_subject],
        "by_difficulty": [{"name": int(r["difficulty"] or 3), "count": int(r["c"])} for r in by_difficulty],
        "knowledge_points": len(tags(limit=1000)),
    }


def tags(limit: int = 60) -> List[Dict[str, Any]]:
    """知识点标签云（按出现次数排序）"""
    rows = execute_query("SELECT knowledge_points FROM edu_questions")
    counter: Counter = Counter()
    for row in rows:
        counter.update(_json_list(row.get("knowledge_points")))
    return [{"name": name, "count": count} for name, count in counter.most_common(int(limit))]


# ============= 导入编排 =============

async def import_document(
    path: str,
    subject: str = "",
    grade: str = "",
    title: str = "",
    max_questions: int = 30,
    source: str = "local",
) -> Dict[str, Any]:
    """把一份课件/作业导入题库

    流程: 文档解析 → 建课件记录 → LLM 题目抽取 → 批量入库 → 回填统计
    """
    from .parsers.document import extract_text
    from .parsers.extractor import extract_questions

    # 补齐配置默认值（学科/年级/单次题量上限）
    from zhiyan_web.config import get_settings
    cfg = get_settings().get("edu") or {}
    subject = subject or str(cfg.get("default_subject") or "")
    grade = grade or str(cfg.get("default_grade") or "")
    try:
        limit = int(cfg.get("max_questions_per_import", 30) or 30)
    except Exception:
        limit = 30
    max_questions = max(1, min(int(max_questions or limit), limit))

    parsed = extract_text(path)
    if not parsed.get("success"):
        return {"success": False, "message": parsed.get("message") or "文档解析失败",
                "course_id": None, "question_count": 0}

    text = parsed["text"]
    course_title = title or os.path.splitext(os.path.basename(path))[0]
    course_id = add_course(
        title=course_title,
        subject=subject,
        grade=grade,
        filename=os.path.basename(path),
        file_type=parsed.get("file_type", ""),
        stored_path=path,
        raw_text=text[:200000],
        status="parsed",
        source=source,
    )
    if not course_id:
        return {"success": False, "message": "课件记录写入失败", "course_id": None, "question_count": 0}

    extracted = await extract_questions(text, subject=subject, grade=grade, max_questions=max_questions)
    questions = extracted.get("questions") or []
    added = add_questions(questions, course_id=course_id, subject=subject, grade=grade, source="import") if questions else 0
    update_course_stats(course_id)

    return {
        "success": True,
        "course_id": course_id,
        "question_count": added,
        "engine": extracted.get("engine", ""),
        "message": f"《{course_title}》导入完成：解析出 {len(questions)} 道题，入库 {added} 道。{extracted.get('message', '')}".strip(),
    }