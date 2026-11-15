"""
授课流程总结

把一份课件/资料：
1. 完整读取（PDF/PPTX/DOCX/XLSX/TXT/MD，复用 parsers.document）
2. 交给 AI 总结授课流程（总体思路 / 分阶段步骤 / 重难点 / 作业建议）
3. 抽取课件中的试题；无答案的由 AI 思考补齐答案与解析
4. 把授课流程保存为本地文件（JSON + Markdown），并入库 edu_flows

对外接口:
- summarize_flow(request) -> dict
- list_flows() / get_flow() / delete_flow() / read_flow_file()
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from zhiyan_web.db.database import execute_delete, execute_insert, execute_query

from .llm_utils import one_shot, parse_object
from .models import EDU_FLOWS_DIR, teaching_settings
from .parsers.document import extract_text
from .parsers.extractor import extract_questions
from .solver import solve_question

logger = logging.getLogger("ZhiYanWeb.Edu")

_FLOW_PROMPT = """你是一位资深教研员，擅长把一份课件/教案提炼成清晰可执行的授课流程。请阅读用户给出的课件原文，输出 JSON。

要求：
1. 只输出 JSON，不要任何解释文字，不要 markdown 代码块。
2. JSON 字段：
   - title: 本课标题
   - overview: 整体授课思路（80-150 字，说明本课如何展开、重点放在哪）
   - duration_minutes: 建议课时（整数，默认 45）
   - steps: 授课步骤数组，按时间顺序，每步字段：
       * stage: 环节名称（如 导入 / 新知探究 / 例题精讲 / 当堂练习 / 小结作业）
       * minutes: 建议用时（整数，各步之和接近 duration_minutes）
       * action: 教师做什么、学生做什么（一句话）
       * key_points: 该环节要落实的知识点数组
       * tips: 教学提示 / 易错提醒
   - key_points: 本课重点数组
   - difficulties: 本课难点数组
   - homework: 作业与课后建议
3. 只依据原文总结，不要编造原文没有的知识点；原文信息不足时按常见教学法合理组织。
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", name or "").strip("_") or "flow"


def _row_to_flow(row: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(row)
    try:
        item["summary"] = json.loads(row.get("summary") or "{}")
    except (json.JSONDecodeError, TypeError):
        item["summary"] = {}
    item["question_count"] = int(row.get("question_count") or 0)
    item["answered_count"] = int(row.get("answered_count") or 0)
    return item


def _resolve_text(request: Dict[str, Any]) -> Dict[str, Any]:
    """解析出待处理文本：text / path / course_id 三选一"""
    text = str(request.get("text") or "").strip()
    if text:
        return {"success": True, "text": text, "title": str(request.get("title") or "手动输入资料"),
                "source_path": "", "course_id": int(request.get("course_id") or 0)}
    path = str(request.get("path") or "").strip()
    course_id = int(request.get("course_id") or 0)
    if not path and course_id:
        from .. import bank
        course = bank.get_course(course_id)
        if not course:
            return {"success": False, "message": "课件不存在"}
        path = course.get("stored_path") or ""
        text = str(course.get("raw_text") or "").strip()
        if text:
            return {"success": True, "text": text, "title": course.get("title") or "",
                    "source_path": path, "course_id": course_id}
    if not path:
        return {"success": False, "message": "请提供课件路径、课件 ID 或直接粘贴文本"}
    parsed = extract_text(path)
    if not parsed.get("success"):
        return {"success": False, "message": parsed.get("message") or "文档解析失败"}
    return {"success": True, "text": parsed["text"],
            "title": str(request.get("title") or os.path.splitext(os.path.basename(path))[0]),
            "source_path": path, "course_id": course_id}


async def summarize_flow(request: Dict[str, Any]) -> Dict[str, Any]:
    """读取课件 → AI 总结授课流程 → 试题补答案 → 落盘保存 → 入库"""
    cfg = teaching_settings("flow")
    resolved = _resolve_text(request)
    if not resolved.get("success"):
        return {"success": False, "message": resolved.get("message"), "data": None}

    text = resolved["text"]
    title = str(request.get("title") or resolved.get("title") or "授课流程")
    subject = str(request.get("subject") or "").strip()
    grade = str(request.get("grade") or "").strip()
    auto_answer = bool(request.get("auto_answer", cfg.get("auto_answer_missing", True)))
    save_file = bool(request.get("save_file", True))
    try:
        max_q = int(request.get("max_questions") or cfg.get("max_questions") or 30)
    except Exception:
        max_q = 30

    messages: List[str] = []

    # 1) AI 总结授课流程
    flow_summary = await _summarize(text, subject, grade)
    if not flow_summary:
        flow_summary = _fallback_summary(text, title)
        messages.append("未配置模型，已按原文结构生成基础授课流程。")
    else:
        messages.append("授课流程总结完成。")

    # 2) 抽取试题 + 无答案补答案
    questions: List[Dict[str, Any]] = []
    try:
        extracted = await extract_questions(text, subject=subject, grade=grade, max_questions=max_q)
        questions = extracted.get("questions") or []
    except Exception as e:
        logger.warning(f"课件题目抽取失败: {e}")

    answered = 0
    if auto_answer:
        for q in questions:
            if str(q.get("answer") or "").strip():
                continue
            try:
                solved = await solve_question(question=q, subject=subject)
                if solved.get("success") and (solved.get("answer") or solved.get("analysis")):
                    q["answer"] = solved.get("answer") or q.get("answer") or ""
                    q["analysis"] = solved.get("analysis") or q.get("analysis") or ""
                    if solved.get("knowledge_points") and not q.get("knowledge_points"):
                        q["knowledge_points"] = solved["knowledge_points"]
                    q["ai_answered"] = True
                    answered += 1
            except Exception as e:
                logger.debug(f"补答案失败: {e}")
    if questions:
        messages.append(f"抽取试题 {len(questions)} 道" + (f"，AI 补答案 {answered} 道。" if answered else "。"))

    # 3) 落盘保存（JSON + Markdown）
    file_path = ""
    if save_file:
        file_path = _save_files(title, subject, grade, flow_summary, questions)
        if file_path:
            messages.append(f"授课流程已保存到本地文件：{file_path}")

    # 4) 入库
    now = _now()
    flow_id = execute_insert(
        "INSERT INTO edu_flows "
        "(course_id, title, subject, grade, summary, file_path, question_count, answered_count, source_path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            int(resolved.get("course_id") or 0),
            title, subject, grade,
            json.dumps({**flow_summary, "questions": questions}, ensure_ascii=False),
            file_path, len(questions), answered, resolved.get("source_path") or "",
            now, now,
        ),
    )

    return {
        "success": True,
        "id": flow_id,
        "message": " ".join(messages),
        "data": {
            "id": flow_id,
            "title": title,
            "summary": flow_summary,
            "questions": questions,
            "file_path": file_path,
            "question_count": len(questions),
            "answered_count": answered,
        },
    }


async def _summarize(text: str, subject: str, grade: str) -> Optional[Dict[str, Any]]:
    user = (
        f"学科：{subject or '未指定'}；年级：{grade or '未指定'}。\n"
        f"请阅读以下课件原文并总结授课流程：\n\n{text[:12000]}"
    )
    raw = await one_shot(_FLOW_PROMPT, user, temperature=0.4, max_tokens=4000)
    data = parse_object(raw) if raw else None
    if not data:
        return None
    steps = data.get("steps") or []
    norm_steps = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        kp = s.get("key_points") or []
        if isinstance(kp, str):
            kp = [x.strip() for x in re.split(r"[,，、;；]+", kp) if x.strip()]
        norm_steps.append({
            "stage": str(s.get("stage") or s.get("环节") or "").strip(),
            "minutes": _to_int(s.get("minutes") or s.get("用时"), 5),
            "action": str(s.get("action") or s.get("活动") or "").strip(),
            "key_points": [str(x).strip() for x in kp] if isinstance(kp, list) else [],
            "tips": str(s.get("tips") or s.get("提示") or "").strip(),
        })
    return {
        "title": str(data.get("title") or "").strip(),
        "overview": str(data.get("overview") or data.get("思路") or "").strip(),
        "duration_minutes": _to_int(data.get("duration_minutes"), 45),
        "steps": norm_steps,
        "key_points": _to_list(data.get("key_points") or data.get("重点")),
        "difficulties": _to_list(data.get("difficulties") or data.get("难点")),
        "homework": str(data.get("homework") or data.get("作业") or "").strip(),
    }


def _fallback_summary(text: str, title: str) -> Dict[str, Any]:
    """LLM 不可用时：按常见教学环节给出骨架，附原文摘要"""
    return {
        "title": title,
        "overview": f"本课围绕《{title}》展开，建议按「导入—探究—精讲—练习—小结」推进，重点引导学生理解并运用核心知识。",
        "duration_minutes": 45,
        "steps": [
            {"stage": "导入", "minutes": 5, "action": "回顾旧知，创设情境，引出本课主题", "key_points": [], "tips": "激发兴趣，控制时间"},
            {"stage": "新知探究", "minutes": 15, "action": "结合课件讲解核心概念与规律", "key_points": [], "tips": "多提问，关注理解"},
            {"stage": "例题精讲", "minutes": 10, "action": "精讲典型例题，示范解题规范", "key_points": [], "tips": "突出思路与易错点"},
            {"stage": "当堂练习", "minutes": 10, "action": "学生独立练习，教师巡视指导", "key_points": [], "tips": "分层练习"},
            {"stage": "小结作业", "minutes": 5, "action": "回顾重点，布置作业", "key_points": [], "tips": "明确作业要求"},
        ],
        "key_points": [],
        "difficulties": [],
        "homework": "完成配套练习，整理本课错题。",
        "raw_excerpt": text[:1500],
        "engine": "fallback",
    }


def _save_files(title: str, subject: str, grade: str, summary: Dict[str, Any],
                questions: List[Dict[str, Any]]) -> str:
    """保存授课流程为 JSON + Markdown，返回 JSON 文件路径"""
    try:
        os.makedirs(EDU_FLOWS_DIR, exist_ok=True)
        base = f"{int(time.time())}_{_safe_name(title)}"
        json_path = os.path.join(EDU_FLOWS_DIR, base + ".json")
        md_path = os.path.join(EDU_FLOWS_DIR, base + ".md")

        payload = {
            "title": title, "subject": subject, "grade": grade,
            "created_at": _now(), "summary": summary, "questions": questions,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        lines = [f"# {title} 授课流程", ""]
        if subject or grade:
            lines.append(f"- 学科：{subject or '—'}　年级：{grade or '—'}")
        lines.append(f"- 生成时间：{payload['created_at']}")
        lines.append("")
        lines.append("## 一、总体思路")
        lines.append(summary.get("overview") or "—")
        lines.append("")
        lines.append(f"## 二、授课流程（建议 {summary.get('duration_minutes', 45)} 分钟）")
        for i, s in enumerate(summary.get("steps") or [], 1):
            lines.append(f"{i}. **{s.get('stage') or '环节'}**（约 {s.get('minutes')} 分钟）：{s.get('action') or ''}")
            if s.get("key_points"):
                lines.append(f"   知识点：{'、'.join(s['key_points'])}")
            if s.get("tips"):
                lines.append(f"   提示：{s['tips']}")
        lines.append("")
        lines.append("## 三、教学重点")
        lines.append("、".join(summary.get("key_points") or []) or "—")
        lines.append("")
        lines.append("## 四、教学难点")
        lines.append("、".join(summary.get("difficulties") or []) or "—")
        lines.append("")
        lines.append("## 五、作业建议")
        lines.append(summary.get("homework") or "—")
        if questions:
            lines.append("")
            lines.append(f"## 六、课件试题（{len(questions)} 道）")
            for i, q in enumerate(questions, 1):
                lines.append(f"{i}. {q.get('stem') or ''}")
                for k, opt in enumerate(q.get("options") or []):
                    lines.append(f"   {'ABCDEFGH'[k] if k < 8 else '?'}. {opt}")
                tag = "（AI 补答）" if q.get("ai_answered") else ""
                lines.append(f"   答案：{q.get('answer') or '（无）'}{tag}")
                if q.get("analysis"):
                    lines.append(f"   解析：{q.get('analysis')}")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return json_path
    except Exception as e:
        logger.error(f"保存授课流程文件失败: {e}")
        return ""


def list_flows(limit: int = 100) -> List[Dict[str, Any]]:
    rows = execute_query(
        "SELECT id, course_id, title, subject, grade, file_path, question_count, answered_count, created_at "
        "FROM edu_flows ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )
    return [dict(r) for r in rows]


def get_flow(flow_id: int) -> Optional[Dict[str, Any]]:
    row = execute_query("SELECT * FROM edu_flows WHERE id = ?", (int(flow_id),), fetch_one=True)
    return _row_to_flow(row) if row else None


def delete_flow(flow_id: int, remove_file: bool = False) -> Dict[str, Any]:
    row = get_flow(flow_id)
    if not row:
        return {"success": False, "message": "授课流程不存在"}
    if remove_file:
        p = row.get("file_path")
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    affected = execute_delete("DELETE FROM edu_flows WHERE id = ?", (int(flow_id),))
    return {"success": affected > 0, "message": "已删除" if affected > 0 else "删除失败"}


def _to_int(value: Any, default: int) -> int:
    try:
        return int(float(str(value).strip() or default))
    except Exception:
        return default


def _to_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,，、;；\n]+", value) if x.strip()]
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []