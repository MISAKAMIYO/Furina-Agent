"""
随机组卷服务

职责:
- 按题量 / 时长 / 学科 / 题型 / 难度 / 知识点约束从题库抽题
- 总分均分到各题（除不尽的余数分给前几题），生成可直接打印的 HTML 试卷
- 试卷快照入库（edu_exams），历史卷不受题库后续变动影响

对外主要接口:
- generate_exam(request)     生成一份试卷（可顺带保存）
- list_exams() / get_exam() / delete_exam()
- export_exam_html(exam_id)  导出 HTML 试卷（含参考答案版开关）
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from html import escape as _esc
from typing import Any, Dict, List, Optional

from zhiyan_web.db.database import execute_delete, execute_insert, execute_query

from . import bank, mistake
from .models import normalize_difficulty, normalize_qtype

logger = logging.getLogger("ZhiYanWeb.Edu")

_QTYPE_LABEL = {"choice": "选择题", "fill": "填空题", "judge": "判断题", "answer": "解答题"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        parsed = json.loads(value)
        return parsed
    except (json.JSONDecodeError, TypeError):
        return default


# ============= 抽题 =============

def _pool(
    subject: str = "",
    qtypes: Optional[List[str]] = None,
    course_id: Optional[int] = None,
    knowledge_points: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """按基础条件取候选题目（知识点在此处只做优先排序，不做硬过滤）"""
    result = bank.list_questions(
        subject=subject,
        course_id=course_id,
        limit=500,
        offset=0,
    )
    items = result.get("items") or []
    wanted_types = {normalize_qtype(t) for t in (qtypes or []) if str(t).strip()}
    if wanted_types:
        items = [q for q in items if normalize_qtype(q.get("qtype")) in wanted_types]

    points = [str(p).strip() for p in (knowledge_points or []) if str(p).strip()]
    if points:
        def hit(q: Dict[str, Any]) -> int:
            qp = set(str(x) for x in (q.get("knowledge_points") or []))
            return len(qp & set(points))
        items.sort(key=lambda q: hit(q), reverse=True)
    return items


def _pick_by_difficulty(
    pool: List[Dict[str, Any]],
    need: int,
    target: Optional[int],
    mix: Optional[Dict[str, int]],
    rng: random.Random,
) -> List[Dict[str, Any]]:
    """按难度配比抽题；配比不足时从剩余题目补齐"""
    picked: List[Dict[str, Any]] = []
    used_ids = set()

    if mix:
        want: Dict[int, int] = {}
        for key, value in mix.items():
            try:
                level = normalize_difficulty(key)
                count = int(value)
            except Exception:
                continue
            if count > 0:
                want[level] = want.get(level, 0) + count
        for level in sorted(want.keys()):
            candidates = [q for q in pool if int(q.get("difficulty") or 3) == level and q["id"] not in used_ids]
            rng.shuffle(candidates)
            for q in candidates[: want[level]]:
                picked.append(q)
                used_ids.add(q["id"])
    elif target:
        level = normalize_difficulty(target)
        # 按与目标难度的距离分组，近的先抽，同距离内随机
        buckets: Dict[int, List[Dict[str, Any]]] = {}
        for q in pool:
            distance = abs(int(q.get("difficulty") or 3) - level)
            buckets.setdefault(distance, []).append(q)
        for distance in sorted(buckets.keys()):
            group = buckets[distance][:]
            rng.shuffle(group)
            for q in group:
                if len(picked) >= need:
                    break
                picked.append(q)
                used_ids.add(q["id"])
            if len(picked) >= need:
                break

    # 补齐
    rest = [q for q in pool if q["id"] not in used_ids]
    rng.shuffle(rest)
    for q in rest:
        if len(picked) >= need:
            break
        picked.append(q)
        used_ids.add(q["id"])

    return picked[:need]


def _allocate_scores(count: int, total: int) -> List[int]:
    """把总分均分到各题，余数依次加到前几题"""
    if count <= 0:
        return []
    total = max(1, int(total))
    base = total // count
    extra = total - base * count
    return [base + (1 if i < extra else 0) for i in range(count)]


def generate_exam(request: Dict[str, Any]) -> Dict[str, Any]:
    """生成一份试卷

    返回 {"success", "message", "paper": {...}}；paper 内 questions 带每题分值。
    """
    subject = str(request.get("subject") or "").strip()
    grade = str(request.get("grade") or "").strip()
    try:
        need = int(request.get("question_count") or 10)
    except Exception:
        need = 10
    need = max(1, min(need, 100))
    try:
        duration = int(request.get("duration") or 60)
    except Exception:
        duration = 60
    duration = max(5, min(duration, 300))
    try:
        total_score = int(request.get("total_score") or 100)
    except Exception:
        total_score = 100
    total_score = max(1, min(total_score, 1000))

    qtypes = [str(t) for t in (request.get("qtypes") or [])]
    knowledge_points = [str(p) for p in (request.get("knowledge_points") or [])]
    course_id = request.get("course_id")
    mix = request.get("difficulty_mix") or None
    if isinstance(mix, str):
        mix = _load(mix, None)

    seed = request.get("seed")
    rng = random.Random(int(seed) if seed not in (None, "") else None)

    pool = _pool(subject=subject, qtypes=qtypes, course_id=course_id, knowledge_points=knowledge_points)
    if not pool:
        return {
            "success": False,
            "message": "题库中没有符合条件的题目，请先导入课件或放宽筛选条件。",
            "paper": None,
        }

    # 错题优先：把未掌握的错题（同题库题目）排到候选前面
    if request.get("include_mistakes"):
        open_topics = mistake.list_open_topics(subject=subject, limit=200)
        weak_points = [p["name"] for p in mistake.weak_points(limit=10, subject=subject)]
        weak_ids = {int(m["question_id"]) for m in open_topics if int(m.get("question_id") or 0)}
        if weak_ids:
            pool.sort(key=lambda q: 0 if q["id"] in weak_ids else 1)
        if weak_points and not knowledge_points:
            def weak_hit(q: Dict[str, Any]) -> int:
                qp = set(str(x) for x in (q.get("knowledge_points") or []))
                return len(qp & set(weak_points))
            pool.sort(key=lambda q: (0 if q["id"] in weak_ids else 1, -weak_hit(q)))

    picked = _pick_by_difficulty(pool, need, request.get("difficulty"), mix, rng)
    if not picked:
        return {"success": False, "message": "抽题失败，题库可用题目不足。", "paper": None}

    scores = _allocate_scores(len(picked), total_score)
    questions: List[Dict[str, Any]] = []
    for idx, q in enumerate(picked):
        item = dict(q)
        item["score"] = scores[idx]
        item["order"] = idx + 1
        questions.append(item)

    actual = len(questions)
    title = str(request.get("title") or "").strip() or (
        f"{subject or '综合'}练习卷 {datetime.now().strftime('%m-%d %H:%M')}"
    )
    paper = {
        "title": title,
        "subject": subject,
        "grade": grade,
        "duration": duration,
        "total_score": sum(scores),
        "question_count": actual,
        "questions": questions,
        "config": {
            "qtypes": qtypes,
            "difficulty": request.get("difficulty"),
            "difficulty_mix": mix,
            "knowledge_points": knowledge_points,
            "include_mistakes": bool(request.get("include_mistakes")),
            "course_id": course_id,
        },
        "created_at": _now(),
    }
    if actual < need:
        paper["notice"] = f"题库可用题目不足，已按 {actual} 道（目标 {need} 道）出卷。"

    message = f"已生成《{title}》：{actual} 道题，总分 {paper['total_score']}，建议时长 {duration} 分钟。"
    if paper.get("notice"):
        message += paper["notice"]

    if request.get("save"):
        saved = save_exam(paper)
        if saved.get("success"):
            paper["id"] = saved["id"]
            message += f" 已存入「我的试卷」（#{saved['id']}）。"
    return {"success": True, "message": message, "paper": paper}


# ============= 试卷存取 =============

def save_exam(paper: Dict[str, Any]) -> Dict[str, Any]:
    """把试卷快照写入 edu_exams"""
    title = str(paper.get("title") or "").strip()
    if not title:
        return {"success": False, "message": "试卷标题不能为空"}
    questions = paper.get("questions") or []
    query = (
        "INSERT INTO edu_exams "
        "(title, subject, grade, question_count, total_score, duration, question_ids, config, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    params = (
        title,
        str(paper.get("subject") or ""),
        str(paper.get("grade") or ""),
        len(questions),
        int(paper.get("total_score") or 0),
        int(paper.get("duration") or 0),
        _dump(questions),
        _dump(paper.get("config") or {}),
        paper.get("created_at") or _now(),
    )
    new_id = execute_insert(query, params)
    if not new_id:
        return {"success": False, "message": "试卷保存失败"}
    return {"success": True, "id": new_id, "message": "试卷已保存"}


def list_exams(limit: int = 100) -> List[Dict[str, Any]]:
    rows = execute_query(
        "SELECT id, title, subject, grade, question_count, total_score, duration, created_at "
        "FROM edu_exams ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )
    return [dict(r) for r in rows]


def get_exam(exam_id: int) -> Optional[Dict[str, Any]]:
    row = execute_query("SELECT * FROM edu_exams WHERE id = ?", (int(exam_id),), fetch_one=True)
    if not row:
        return None
    item = dict(row)
    item["questions"] = _load(row.get("question_ids"), [])
    item["config"] = _load(row.get("config"), {})
    item.pop("question_ids", None)
    return item


def delete_exam(exam_id: int) -> bool:
    return execute_delete("DELETE FROM edu_exams WHERE id = ?", (int(exam_id),)) > 0


# ============= HTML 导出 =============

_CSS = """
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; color: #1f2733; max-width: 820px;
       margin: 0 auto; padding: 28px 32px 60px; line-height: 1.75; }
h1 { text-align: center; font-size: 22px; margin: 0 0 6px; }
.meta { text-align: center; color: #5b6b80; font-size: 13px; margin-bottom: 6px; }
.hr { border-bottom: 1px dashed #b9c6d8; margin: 12px 0 18px; }
.q { margin-bottom: 18px; }
.q-head { font-weight: 700; margin-bottom: 4px; }
.q-head .score { float: right; font-weight: 400; color: #5b6b80; font-size: 12px; }
.stem { white-space: pre-wrap; }
.opts { margin: 6px 0 0 20px; }
.opt { margin: 2px 0; }
.ans { margin-top: 6px; color: #1a7f4b; font-size: 13px; }
.analysis { margin-top: 4px; color: #5b6b80; font-size: 13px; white-space: pre-wrap; }
.section-title { font-size: 16px; font-weight: 700; margin: 22px 0 10px; border-left: 4px solid #3d8bfd; padding-left: 8px; }
.tip { color: #9aa9bc; font-size: 12px; text-align: center; margin-top: 30px; }
@media print { body { padding: 0; } .tip { display: none; } }
"""


def _render_question(q: Dict[str, Any], with_answer: bool) -> str:
    qtype = _QTYPE_LABEL.get(normalize_qtype(q.get("qtype")), "题目")
    parts = ['<div class="q">']
    parts.append(
        f'<div class="q-head">{_esc(str(q.get("order") or ""))}. （{qtype}）'
        f'<span class="score">{int(q.get("score") or 0)} 分</span></div>'
    )
    parts.append(f'<div class="stem">{_esc(str(q.get("stem") or "")).replace(chr(10), "<br>")}</div>')

    options = q.get("options") or []
    if isinstance(options, list) and options:
        labels = "ABCDEFGH"
        rows = []
        for idx, opt in enumerate(options):
            text = str(opt).strip()
            if not text:
                continue
            if text[:1].isalpha() and text[1:2] in (".", "、", "．", ")", "）"):
                row = text
            else:
                row = f"{labels[idx] if idx < len(labels) else '?'}. {text}"
            rows.append(f'<div class="opt">{_esc(row)}</div>')
        if rows:
            parts.append('<div class="opts">' + "".join(rows) + "</div>")

    if with_answer:
        answer = str(q.get("answer") or "").strip()
        if answer:
            parts.append(f'<div class="ans">参考答案：{_esc(answer).replace(chr(10), "<br>")}</div>')
        analysis = str(q.get("analysis") or "").strip()
        if analysis:
            parts.append(f'<div class="analysis">解析：{_esc(analysis).replace(chr(10), "<br>")}</div>')
    parts.append("</div>")
    return "".join(parts)


def export_exam_html(exam_id: int, with_answer: bool = False) -> Dict[str, Any]:
    """导出整份试卷为 HTML 字符串（无第三方依赖，浏览器可直接打印为 PDF）"""
    exam = get_exam(exam_id)
    if not exam:
        return {"success": False, "message": "试卷不存在", "html": ""}

    questions = exam.get("questions") or []
    groups: List[str] = []
    ordered_types = ["choice", "fill", "judge", "answer"]
    present = [t for t in ordered_types if any(normalize_qtype(q.get("qtype")) == t for q in questions)]
    for t in present:
        subset = [q for q in questions if normalize_qtype(q.get("qtype")) == t]
        groups.append(f'<div class="section-title">{_QTYPE_LABEL.get(t, "题目")}（共 {len(subset)} 题）</div>')
        for q in subset:
            groups.append(_render_question(q, with_answer))

    head = (
        f"<h1>{_esc(str(exam.get('title') or '练习卷'))}</h1>"
        f'<div class="meta">学科：{_esc(str(exam.get("subject") or "综合"))}　'
        f'满分：{int(exam.get("total_score") or 0)} 分　'
        f'题量：{int(exam.get("question_count") or 0)} 题　'
        f'建议时长：{int(exam.get("duration") or 0)} 分钟</div>'
    )
    if with_answer:
        head += '<div class="meta">（含参考答案与解析）</div>'
    body = "".join(groups)
    tip = '<div class="tip">由 ZhiYan AI 教辅生成 · 可用浏览器打印为 PDF</div>'
    html = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{_esc(str(exam.get('title') or '练习卷'))}</title><style>{_CSS}</style></head>"
        f"<body>{head}<div class=\"hr\"></div>{body}{tip}</body></html>"
    )
    return {"success": True, "html": html, "title": exam.get("title") or "练习卷"}