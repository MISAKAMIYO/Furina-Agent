"""
解题与讲解

基于 LLM 一次性调用（不写入对话历史），输入题目 → 输出解题步骤、参考答案、详细讲解、知识点与易错点。

对外接口:
- solve_question(question | text, subject) -> dict
- format_question(question) -> str          把题目结构拼成可读题面
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.Edu")

_SYSTEM_PROMPT = """你是一位耐心、严谨的老师，擅长把题目讲清楚。请针对用户给出的题目给出完整解答。

要求：
1. 只输出 JSON，不要任何解释文字，不要 markdown 代码块。
2. JSON 字段：
   - answer: 参考答案（选择题给出选项字母与内容，解答题给出最终结论）
   - steps: 解题步骤数组，每项是一句完整的步骤说明，按顺序给出，3-8 步
   - analysis: 详细讲解（讲清思路、用到的公式/定理、为什么这样做）
   - knowledge_points: 涉及知识点数组，如 ["二次函数","顶点式"]
   - mistakes: 常见易错点与提醒
3. 数学公式用纯文本或 LaTeX 行内形式（$...$）书写，不要使用复杂排版。
4. 如果题目信息不足，就在 analysis 中说明缺少什么条件，不要编造。
"""


def _chat_manager():
    from zhiyan_web.core import chat_manager
    return chat_manager


def format_question(question: Dict[str, Any]) -> str:
    """把题目结构拼成可读题面"""
    if not question:
        return ""
    parts: List[str] = []
    stem = str(question.get("stem") or "").strip()
    if stem:
        parts.append(stem)
    options = question.get("options") or []
    if isinstance(options, list) and options:
        labels = "ABCDEFGH"
        for idx, opt in enumerate(options):
            text = str(opt).strip()
            if not text:
                continue
            # 已自带 A. 前缀就不重复加
            if re.match(r"^[A-HＡ-Ｈ][.、．)）]", text):
                parts.append(text)
            else:
                parts.append(f"{labels[idx] if idx < len(labels) else '?'}. {text}")
    return "\n".join(parts)


async def solve_question(
    question: Optional[Dict[str, Any]] = None,
    text: str = "",
    subject: str = "",
) -> Dict[str, Any]:
    """解题讲解

    参数二选一:
        question: 题库中的题目结构（含 stem/options/qtype/subject）
        text:     直接给出的题目文本

    返回: {"success", "answer", "steps": [...], "analysis", "knowledge_points": [...], "mistakes", "message"}
    """
    q = question or {}
    stem = format_question(q) if q else (text or "").strip()
    if not stem:
        return {"success": False, "message": "题目内容为空"}

    qtype = str(q.get("qtype") or "")
    points = q.get("knowledge_points") or []
    user_content = (
        f"学科：{subject or q.get('subject') or '未指定'}\n"
        f"题型：{qtype or '未指定'}\n"
        f"已知知识点：{'、'.join(str(p) for p in points) if points else '无'}\n\n"
        f"题目：\n{stem}"
    )

    cm = _chat_manager()
    if cm is None:
        return {"success": False, "message": "LLM 未初始化"}

    try:
        raw = await cm.one_shot_chat(
            system_prompt=_SYSTEM_PROMPT,
            user_content=user_content,
            temperature=0.3,
            max_tokens=2500,
        )
    except Exception as e:
        logger.warning(f"解题调用异常: {e}")
        return {"success": False, "message": f"解题调用失败: {e}"}

    if not raw or raw.startswith("错误："):
        return {"success": False, "message": raw or "LLM 无响应"}

    data = _parse_json_object(raw)
    if data is None:
        # 模型没按 JSON 输出时，直接把原文作为讲解返回（不丢结果）
        return {
            "success": True,
            "answer": "",
            "steps": [],
            "analysis": raw.strip(),
            "knowledge_points": [],
            "mistakes": "",
            "message": "模型未返回结构化结果，已直接给出讲解",
        }

    steps = data.get("steps") or []
    if isinstance(steps, str):
        steps = [s.strip() for s in re.split(r"[\n;；]+", steps) if s.strip()]
    if not isinstance(steps, list):
        steps = []

    kps = data.get("knowledge_points") or []
    if isinstance(kps, str):
        kps = [k.strip() for k in re.split(r"[,，、;；/\s]+", kps) if k.strip()]
    if not isinstance(kps, list):
        kps = []

    return {
        "success": True,
        "answer": str(data.get("answer") or "").strip(),
        "steps": [str(s).strip() for s in steps if str(s).strip()],
        "analysis": str(data.get("analysis") or "").strip(),
        "knowledge_points": [str(k).strip() for k in kps if str(k).strip()],
        "mistakes": str(data.get("mistakes") or "").strip(),
        "message": "解题讲解完成",
    }


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 回复中稳健地解析 JSON 对象"""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    left, right = s.find("{"), s.rfind("}")
    if left == -1 or right <= left:
        return None
    try:
        data = json.loads(s[left:right + 1])
    except Exception:
        return None
    return data if isinstance(data, dict) else None