"""
错题本服务

职责:
- 错题归档：题库题目一键转入、手动新增，按学科/知识点/状态分类
- 复习打卡：做对累加连对次数，连对 2 次判定为「已掌握」；做错回退为「待复习」
- 薄弱知识点统计：供推题与组卷使用

对外主要接口:
- add_mistake() / add_from_question()
- list_mistakes() / get_mistake() / update_mistake() / delete_mistake()
- review_mistake() / stats() / weak_points()
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from zhiyan_web.db.database import execute_delete, execute_insert, execute_query, execute_update

from . import bank
from .models import normalize_difficulty, normalize_qtype

logger = logging.getLogger("ZhiYanWeb.Edu")

# 连对多少次判定为已掌握
MASTER_STREAK = 2

_STATUS_LABEL = {"open": "待复习", "reviewing": "复习中", "mastered": "已掌握"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json_list(value: Any) -> List[str]:
    return bank._json_list(value)


def _dump_list(value: Any) -> str:
    return bank._dump_list(value)


def _row_to_mistake(row: Dict[str, Any]) -> Dict[str, Any]:
    """数据库行 → 错题 dict"""
    item = dict(row)
    item["options"] = _json_list(row.get("options"))
    item["knowledge_points"] = _json_list(row.get("knowledge_points"))
    try:
        item["difficulty"] = int(row.get("difficulty") or 3)
    except Exception:
        item["difficulty"] = 3
    for key in ("review_count", "correct_streak", "question_id"):
        try:
            item[key] = int(row.get(key) or 0)
        except Exception:
            item[key] = 0
    item["status_label"] = _STATUS_LABEL.get(str(row.get("status") or "open"), "待复习")
    return item


# ============= 增 =============

def add_mistake(data: Dict[str, Any]) -> Optional[int]:
    """新增一条错题，返回记录 id"""
    stem = str(data.get("stem") or "").strip()
    if not stem:
        return None
    now = _now()
    qtype = normalize_qtype(data.get("qtype"))
    question_id = int(data.get("question_id") or 0)
    wrong_answer = str(data.get("wrong_answer") or "").strip()
    # 同一道题库题目重复标记时，更新原记录而不是再插一条
    if question_id:
        existed = execute_query(
            "SELECT id FROM edu_mistakes WHERE question_id = ? AND status != 'mastered' ORDER BY id DESC LIMIT 1",
            (question_id,),
            fetch_one=True,
        )
        if existed:
            mid = int(existed["id"])
            fields = ["stem = ?", "wrong_answer = ?", "updated_at = ?"]
            params: List[Any] = [stem, wrong_answer, now]
            note = str(data.get("note") or "").strip()
            if note:
                fields.append("note = ?")
                params.append(note)
            params.append(mid)
            execute_update(f"UPDATE edu_mistakes SET {', '.join(fields)} WHERE id = ?", tuple(params))
            return mid

    query = (
        "INSERT INTO edu_mistakes "
        "(question_id, subject, grade, qtype, stem, options, wrong_answer, correct_answer, analysis, "
        " knowledge_points, difficulty, note, status, review_count, correct_streak, source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, 0, ?, ?, ?)"
    )
    params = (
        question_id,
        str(data.get("subject") or ""),
        str(data.get("grade") or ""),
        qtype,
        stem,
        _dump_list(data.get("options")),
        wrong_answer,
        str(data.get("correct_answer") or "").strip(),
        str(data.get("analysis") or "").strip(),
        _dump_list(data.get("knowledge_points")),
        normalize_difficulty(data.get("difficulty")),
        str(data.get("note") or "").strip(),
        str(data.get("source") or "manual"),
        now,
        now,
    )
    return execute_insert(query, params)


def add_from_question(
    question_id: int,
    wrong_answer: str = "",
    note: str = "",
    source: str = "manual",
) -> Dict[str, Any]:
    """把题库中的一道题转入错题本"""
    question = bank.get_question(int(question_id))
    if not question:
        return {"success": False, "message": "题目不存在"}
    mid = add_mistake({
        "question_id": int(question_id),
        "stem": question.get("stem") or "",
        "options": question.get("options") or [],
        "subject": question.get("subject") or "",
        "grade": question.get("grade") or "",
        "qtype": question.get("qtype") or "answer",
        "wrong_answer": wrong_answer,
        "correct_answer": question.get("answer") or "",
        "analysis": question.get("analysis") or "",
        "knowledge_points": question.get("knowledge_points") or [],
        "difficulty": question.get("difficulty"),
        "note": note,
        "source": source,
    })
    if not mid:
        return {"success": False, "message": "错题记录写入失败"}
    return {"success": True, "id": mid, "message": f"已加入错题本（记录 #{mid}）"}


# ============= 查 =============

def list_mistakes(
    subject: str = "",
    status: str = "",
    keyword: str = "",
    knowledge_point: str = "",
    limit: int = 100,
    offset: int = 0,
) -> Dict[str, Any]:
    """错题检索，返回 {total, items}"""
    where: List[str] = []
    params: List[Any] = []
    if subject:
        where.append("subject = ?")
        params.append(subject)
    if status:
        where.append("status = ?")
        params.append(status)
    if knowledge_point:
        where.append("knowledge_points LIKE ?")
        params.append(f"%{knowledge_point}%")
    if keyword:
        where.append("(stem LIKE ? OR wrong_answer LIKE ? OR correct_answer LIKE ? OR note LIKE ? OR analysis LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like, like, like])

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total_rows = execute_query(f"SELECT COUNT(*) AS c FROM edu_mistakes {clause}", tuple(params))
    total = int(total_rows[0]["c"]) if total_rows else 0
    # 待复习与复习中的排前面
    order = "ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'reviewing' THEN 1 ELSE 2 END, id DESC"
    rows = execute_query(
        f"SELECT * FROM edu_mistakes {clause} {order} LIMIT ? OFFSET ?",
        tuple(params + [int(limit), int(offset)]),
    )
    return {"total": total, "items": [_row_to_mistake(r) for r in rows]}


def get_mistake(mistake_id: int) -> Optional[Dict[str, Any]]:
    row = execute_query("SELECT * FROM edu_mistakes WHERE id = ?", (int(mistake_id),), fetch_one=True)
    return _row_to_mistake(row) if row else None


def list_open_topics(subject: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    """取未掌握的错题（供组卷/推题抽题）"""
    where = ["status != 'mastered'"]
    params: List[Any] = []
    if subject:
        where.append("subject = ?")
        params.append(subject)
    rows = execute_query(
        f"SELECT * FROM edu_mistakes WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
        tuple(params + [int(limit)]),
    )
    return [_row_to_mistake(r) for r in rows]


# ============= 改 / 删 =============

def update_mistake(mistake_id: int, patch: Dict[str, Any]) -> bool:
    """按传入字段更新错题"""
    fields: List[str] = []
    params: List[Any] = []
    for key in ("stem", "wrong_answer", "correct_answer", "analysis", "note", "subject", "grade"):
        if key in patch and patch[key] is not None:
            fields.append(f"{key} = ?")
            params.append(str(patch[key]))
    if patch.get("qtype") is not None:
        fields.append("qtype = ?")
        params.append(normalize_qtype(patch["qtype"]))
    if patch.get("difficulty") is not None:
        fields.append("difficulty = ?")
        params.append(normalize_difficulty(patch["difficulty"]))
    if patch.get("knowledge_points") is not None:
        fields.append("knowledge_points = ?")
        params.append(_dump_list(patch["knowledge_points"]))
    if patch.get("status") in _STATUS_LABEL:
        fields.append("status = ?")
        params.append(patch["status"])
    if not fields:
        return False
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(int(mistake_id))
    affected = execute_update(
        f"UPDATE edu_mistakes SET {', '.join(fields)} WHERE id = ?", tuple(params)
    )
    return affected > 0


def delete_mistake(mistake_id: int) -> bool:
    return execute_delete("DELETE FROM edu_mistakes WHERE id = ?", (int(mistake_id),)) > 0


def review_mistake(mistake_id: int, result: str = "correct", note: str = "") -> Dict[str, Any]:
    """复习打卡

    result: correct（这次做对了）/ wrong（这次又做错了）
    规则: 做对累加连对次数，连对达到 MASTER_STREAK 判为已掌握；做错清零回到待复习。
    """
    item = get_mistake(mistake_id)
    if not item:
        return {"success": False, "message": "错题不存在"}

    ok = str(result or "").strip().lower() in ("correct", "right", "对", "正确", "true", "1", "yes")
    review_count = int(item.get("review_count") or 0) + 1
    now = _now()
    fields = ["review_count = ?", "last_review_at = ?", "updated_at = ?"]
    params: List[Any] = [review_count, now, now]
    note = (note or "").strip()

    if ok:
        streak = int(item.get("correct_streak") or 0) + 1
        status = "mastered" if streak >= MASTER_STREAK else "reviewing"
        fields.append("correct_streak = ?")
        params.append(streak)
        fields.append("status = ?")
        params.append(status)
        if status == "mastered":
            fields.append("mastered_at = ?")
            params.append(now)
    else:
        fields.append("correct_streak = ?")
        params.append(0)
        fields.append("status = ?")
        params.append("open")

    if note:
        merged = (str(item.get("note") or "") + ("；" if item.get("note") else "") + note)
        fields.append("note = ?")
        params.append(merged[:500])

    params.append(int(mistake_id))
    execute_update(f"UPDATE edu_mistakes SET {', '.join(fields)} WHERE id = ?", tuple(params))

    updated = get_mistake(mistake_id) or {}
    status = str(updated.get("status") or "open")
    tip = {
        "mastered": "连续答对，已标记为掌握，恭喜！",
        "reviewing": "答对了，再连对一次就可以标记为掌握。",
        "open": "这次又做错了，建议看一遍解析再练同类题。",
    }.get(status, "已记录本次复习")
    return {
        "success": True,
        "data": updated,
        "message": tip,
        "status": status,
    }


# ============= 统计 =============

def stats() -> Dict[str, Any]:
    """错题本总览统计"""
    total_rows = execute_query("SELECT COUNT(*) AS c FROM edu_mistakes")
    total = int(total_rows[0]["c"]) if total_rows else 0

    by_status_rows = execute_query("SELECT status, COUNT(*) AS c FROM edu_mistakes GROUP BY status")
    by_status = {str(r["status"] or "open"): int(r["c"]) for r in by_status_rows}
    mastered = by_status.get("mastered", 0)

    by_subject_rows = execute_query(
        "SELECT subject, COUNT(*) AS c FROM edu_mistakes WHERE subject != '' GROUP BY subject ORDER BY c DESC LIMIT 20"
    )
    by_subject = [{"name": r["subject"], "count": int(r["c"])} for r in by_subject_rows]

    weak = weak_points(limit=10)
    return {
        "mistake_total": total,
        "open_total": by_status.get("open", 0),
        "reviewing_total": by_status.get("reviewing", 0),
        "mastered_total": mastered,
        "mastery_rate": round(mastered * 100.0 / total, 1) if total else 0.0,
        "review_total": sum(int(r.get("review_count") or 0) for r in
                            execute_query("SELECT review_count FROM edu_mistakes")) if total else 0,
        "by_subject": by_subject,
        "weak_points": weak,
    }


def weak_points(limit: int = 10, subject: str = "") -> List[Dict[str, Any]]:
    """薄弱知识点：按错题中知识点出现次数排序"""
    where = ["status != 'mastered'"]
    params: List[Any] = []
    if subject:
        where.append("subject = ?")
        params.append(subject)
    rows = execute_query(
        f"SELECT knowledge_points, subject FROM edu_mistakes WHERE {' AND '.join(where)}",
        tuple(params),
    )
    counter: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        for point in _json_list(row.get("knowledge_points")):
            slot = counter.setdefault(point, {"name": point, "count": 0, "subject": row.get("subject") or ""})
            slot["count"] += 1
    ranked = sorted(counter.values(), key=lambda x: x["count"], reverse=True)
    return ranked[: int(limit)]


def mastery(subject: str = "") -> List[Dict[str, Any]]:
    """按学科给出错题掌握度"""
    where = ["subject != ''"]
    params: List[Any] = []
    if subject:
        where.append("subject = ?")
        params.append(subject)
    rows = execute_query(
        f"SELECT subject, status, COUNT(*) AS c FROM edu_mistakes WHERE {' AND '.join(where)} "
        "GROUP BY subject, status",
        tuple(params),
    )
    table: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        name = str(row["subject"] or "未分类")
        slot = table.setdefault(name, {"subject": name, "total": 0, "mastered": 0})
        count = int(row["c"])
        slot["total"] += count
        if str(row["status"] or "") == "mastered":
            slot["mastered"] += count
    result = []
    for slot in table.values():
        slot["mastery_rate"] = round(slot["mastered"] * 100.0 / slot["total"], 1) if slot["total"] else 0.0
        result.append(slot)
    result.sort(key=lambda x: x["total"], reverse=True)
    return result