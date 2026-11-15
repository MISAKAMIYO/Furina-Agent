"""
推题与掌握度

职责:
- 由错题本的薄弱知识点反查题库，生成针对性练习（排除已在错题本的原题）
- LLM 可用时附一段学习建议；不可用时退回纯规则建议

对外主要接口:
- recommend_questions()   针对性练习题列表
- mastery()               学科掌握度概览
- async build_practice()  练习题 + 薄弱知识点 + 建议（LLM 可选）
"""
from __future__ import annotations

import json
import logging
import random
import re
from typing import Any, Dict, List, Optional

from . import bank, mistake
from .solver import _parse_json_object

logger = logging.getLogger("ZhiYanWeb.Edu")

_ADVICE_PROMPT = """你是一位学科老师，正在给学生制定针对性的练习计划。
请根据给出的「薄弱知识点」和「候选题列表」，给出简短的学习建议。

只输出 JSON，不要解释文字，不要 markdown 代码块。字段：
- summary: 一句话点评当前薄弱环节（40 字以内）
- advice: 字符串数组，2-4 条具体可执行的建议，每条一句
- focus_points: 最该优先攻克的知识点数组，1-3 个
"""


def _rules_advice(weak: List[Dict[str, Any]], count: int) -> Dict[str, Any]:
    """LLM 不可用时的规则建议"""
    names = [str(w.get("name") or "") for w in weak[:3] if w.get("name")]
    if not names:
        return {
            "summary": "错题本记录还不多，先做一组题看看薄弱环节在哪。",
            "advice": [
                "先完成下面的练习题，做错的题点「加入错题本」。",
                "错题积累几道后再回来看推题，会更贴合你的薄弱点。",
            ],
            "focus_points": [],
        }
    joined = "、".join(names)
    return {
        "summary": f"近期错题集中在：{joined}。",
        "advice": [
            f"先把「{names[0]}」的解析逐句看懂，再独立重做一遍。",
            "做完下面的练习题后，把再次做错的题重新标记进错题本。",
            f"隔一天再复习一次{joined}相关题目，连对两次即可标记为掌握。",
        ],
        "focus_points": names,
    }


def recommend_questions(
    subject: str = "",
    limit: int = 5,
    exclude_question_ids: Optional[List[int]] = None,
    knowledge_points: Optional[List[str]] = None,
    difficulty: Optional[int] = None,
) -> Dict[str, Any]:
    """针对性推题

    取数优先级: 指定知识点 → 错题本薄弱知识点 → 错题原题之外的同科题目
    """
    limit = max(1, min(int(limit or 5), 30))
    exclude = {int(x) for x in (exclude_question_ids or [])}

    # 错题本里已有的题目默认不再推给别人重复练
    open_mistakes = mistake.list_open_topics(subject=subject, limit=300)
    mistake_qids = {int(m.get("question_id") or 0) for m in open_mistakes if int(m.get("question_id") or 0)}
    exclude |= mistake_qids

    weak = mistake.weak_points(limit=10, subject=subject)
    weak_names = [str(w.get("name") or "") for w in weak if w.get("name")]
    points = [str(p).strip() for p in (knowledge_points or []) if str(p).strip()] or weak_names

    pool = bank.list_questions(subject=subject, difficulty=difficulty, limit=500).get("items") or []
    pool = [q for q in pool if int(q.get("id") or 0) not in exclude]
    if not pool:
        return {
            "items": [],
            "weak_points": weak,
            "focus_points": points[:3],
            "reason": "题库中暂时没有可推的新题，可先导入更多课件。",
        }

    reason_parts: List[str] = []
    if points:
        point_set = set(points)

        def hit_count(q: Dict[str, Any]) -> int:
            qp = set(str(x) for x in (q.get("knowledge_points") or []))
            return len(qp & point_set)

        matched = [q for q in pool if hit_count(q) > 0]
        if matched:
            matched.sort(key=lambda q: (-hit_count(q), int(q.get("difficulty") or 3)))
            reason_parts.append("围绕薄弱知识点：" + "、".join(points[:3]))
            pool = matched

    if not reason_parts:
        # 无错题记录：优先给难度适中的题
        pool.sort(key=lambda q: abs(int(q.get("difficulty") or 3) - 3))
        reason_parts.append("按难度均衡推荐")

    rng = random.Random()
    # 前 2/3 按相关度，后 1/3 随机，避免每次都推同一批
    head = pool[: max(limit, int(len(pool) * 2 / 3))]
    rng.shuffle(head)
    items = head[:limit]

    return {
        "items": items,
        "weak_points": weak,
        "focus_points": points[:3],
        "reason": "；".join(reason_parts),
    }


def mastery(subject: str = "") -> Dict[str, Any]:
    """掌握度总览：整体 + 分学科"""
    stats = mistake.stats()
    return {
        "mistake_total": stats.get("mistake_total", 0),
        "mastered_total": stats.get("mastered_total", 0),
        "open_total": stats.get("open_total", 0),
        "mastery_rate": stats.get("mastery_rate", 0.0),
        "review_total": stats.get("review_total", 0),
        "by_subject": mistake.mastery(subject=subject),
        "weak_points": stats.get("weak_points", []),
    }


async def build_practice(
    subject: str = "",
    limit: int = 5,
    knowledge_points: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """生成一份针对性练习包：题目 + 薄弱点 + 学习建议"""
    result = recommend_questions(
        subject=subject, limit=limit, knowledge_points=knowledge_points
    )
    items = result.get("items") or []
    weak = result.get("weak_points") or []
    focus = result.get("focus_points") or []

    advice = _rules_advice(weak, len(items))
    engine = "rule"
    if items:
        try:
            from zhiyan_web.core import chat_manager
            brief = [
                {
                    "stem": str(q.get("stem") or "")[:120],
                    "knowledge_points": q.get("knowledge_points") or [],
                    "difficulty": q.get("difficulty"),
                }
                for q in items
            ]
            user_content = (
                f"学科：{subject or '综合'}\n"
                f"薄弱知识点：{'、'.join(str(w.get('name')) for w in weak[:6]) or '暂无记录'}\n"
                f"候选题：{json.dumps(brief, ensure_ascii=False)}"
            )
            raw = await chat_manager.one_shot_chat(
                system_prompt=_ADVICE_PROMPT,
                user_content=user_content,
                temperature=0.4,
                max_tokens=800,
            )
            parsed = _parse_json_object(raw) if raw and not raw.startswith("错误：") else None
            if parsed:
                merged = dict(advice)
                if parsed.get("summary"):
                    merged["summary"] = str(parsed["summary"]).strip()
                steps = parsed.get("advice") or []
                if isinstance(steps, str):
                    steps = [s.strip() for s in re.split(r"[\n;；]+", steps) if s.strip()]
                if isinstance(steps, list) and steps:
                    merged["advice"] = [str(s).strip() for s in steps if str(s).strip()]
                focus_llm = parsed.get("focus_points") or []
                if isinstance(focus_llm, list) and focus_llm:
                    merged["focus_points"] = [str(k).strip() for k in focus_llm if str(k).strip()]
                advice = merged
                engine = "llm"
        except Exception as e:
            logger.warning(f"生成学习建议失败，改用规则建议: {e}")

    return {
        "success": True,
        "items": items,
        "weak_points": weak,
        "focus_points": advice.get("focus_points") or focus,
        "summary": advice.get("summary", ""),
        "advice": advice.get("advice", []),
        "reason": result.get("reason", ""),
        "engine": engine,
        "message": f"已生成 {len(items)} 道针对性练习。",
    }