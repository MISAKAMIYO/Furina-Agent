"""
题目结构化抽取

把课件/作业原文交给 LLM，抽成题库结构（题干/选项/答案/解析/知识点/难度）。
LLM 不可用时回退到「按题号切分」的启发式抽取，保证断网/未配置 Key 时仍可入库。

对外接口: extract_questions(raw_text, subject, grade, max_questions) -> dict
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from zhiyan_web.config import get_settings

from ..models import normalize_difficulty, normalize_qtype

logger = logging.getLogger("ZhiYanWeb.Edu")

_SYSTEM_PROMPT = """你是一位资深教师与题库编辑。请从用户给出的课件或作业原文中，抽取所有可以独立成题的题目，输出结构化 JSON。

要求：
1. 只输出 JSON，不要任何解释文字，不要 markdown 代码块。
2. 输出格式为 JSON 数组，每个元素包含字段：
   - stem: 题干（必填，保留完整题面，含条件与问题）
   - options: 选项数组（选择题按 A/B/C/D 顺序给出完整选项文本；非选择题给空数组 []）
   - answer: 参考答案（原文没有就留空字符串）
   - analysis: 解题过程与详细讲解（原文没有就留空字符串）
   - qtype: 题型，只能是 choice(选择题) / fill(填空题) / judge(判断题) / answer(解答题)
   - knowledge_points: 知识点数组，如 ["一元二次方程","判别式"]
   - difficulty: 难度，1-5 的整数（1 最易，5 最难）
3. 只抽取原文真实出现的题目，不要编造；不要重复抽取同一题。
4. 若原文中没有可成题的内容，输出 []。
"""

_QUESTION_START = re.compile(
    r"^\s*(?:第\s*)?(\d{1,3})\s*[.、．)）]\s*(.+)$|^\s*[（(](\d{1,3})[)）]\s*(.+)$"
)
_OPTION_LINE = re.compile(r"^\s*[A-DＡ-Ｄa-d][.、．)）]\s*\S")


def _chat_manager():
    """延迟获取全局 ChatManager（避免循环依赖）"""
    from zhiyan_web.core import chat_manager
    return chat_manager


async def extract_questions(
    raw_text: str,
    subject: str = "",
    grade: str = "",
    max_questions: int = 50,
) -> Dict[str, Any]:
    """从原文抽取题目

    返回: {"success": bool, "questions": [...], "engine": "llm"/"heuristic", "message": str}
    """
    text = (raw_text or "").strip()
    if not text:
        return {"success": False, "questions": [], "engine": "", "message": "原文为空"}

    cfg = get_settings().get("edu") or {}
    chunk_chars = int(cfg.get("extract_chunk_chars", 6000) or 6000)
    max_questions = max(1, min(int(max_questions or 30), 200))

    chunks = _split_chunks(text, chunk_chars)
    collected: List[Dict[str, Any]] = []
    errors: List[str] = []

    for idx, chunk in enumerate(chunks):
        if len(collected) >= max_questions:
            break
        remain = max_questions - len(collected)
        user_content = (
            f"学科：{subject or '未指定'}；年级：{grade or '未指定'}。\n"
            f"请从以下原文的第 {idx + 1}/{len(chunks)} 段中抽取题目，最多 {remain} 道：\n\n{chunk}"
        )
        items = await _extract_chunk(user_content)
        if items is None:
            errors.append(f"第 {idx + 1} 段抽取失败")
            continue
        collected.extend(items)

    if collected:
        # 去重（题干完全相同视为重复）
        seen = set()
        unique: List[Dict[str, Any]] = []
        for item in collected:
            key = re.sub(r"\s+", "", item.get("stem", ""))[:120]
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return {
            "success": True,
            "questions": unique[:max_questions],
            "engine": "llm",
            "message": f"LLM 抽取 {len(unique)} 道题目",
        }

    heuristic = _heuristic_extract(text, max_questions)
    if heuristic:
        return {
            "success": True,
            "questions": heuristic,
            "engine": "heuristic",
            "message": f"LLM 不可用（{'; '.join(errors) or '未配置模型'}），已按题号启发式抽取 {len(heuristic)} 道（无答案与解析）",
        }
    return {
        "success": False,
        "questions": [],
        "engine": "",
        "message": "; ".join(errors) or "未抽取到题目",
    }


async def _extract_chunk(user_content: str) -> Optional[List[Dict[str, Any]]]:
    """调用 LLM 抽取单段文本，失败返回 None"""
    cm = _chat_manager()
    if cm is None:
        return None
    try:
        raw = await cm.one_shot_chat(
            system_prompt=_SYSTEM_PROMPT,
            user_content=user_content,
            temperature=0.2,
            max_tokens=4000,
        )
    except Exception as e:
        logger.warning(f"LLM 抽取异常: {e}")
        return None

    if not raw or raw.startswith("错误："):
        logger.warning(f"LLM 抽取不可用: {str(raw)[:120]}")
        return None

    items = _parse_json_array(raw)
    if items is None:
        logger.warning("LLM 抽取结果非合法 JSON，回退启发式")
        return None

    normalized: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        norm = _normalize_item(item)
        if norm:
            normalized.append(norm)
    return normalized


def _parse_json_array(text: str) -> Optional[List[Any]]:
    """从 LLM 回复中稳健地解析 JSON 数组"""
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()

    data: Any = None
    try:
        data = json.loads(s)
    except Exception:
        left, right = s.find("["), s.rfind("]")
        if left != -1 and right > left:
            try:
                data = json.loads(s[left:right + 1])
            except Exception:
                data = None

    if isinstance(data, dict):
        for key in ("questions", "data", "items", "result", "list"):
            if isinstance(data.get(key), list):
                return data[key]
        return None
    if isinstance(data, list):
        return data
    return None


def _normalize_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把 LLM 返回的单条题目归一化为入库结构"""
    stem = str(item.get("stem") or item.get("question") or item.get("题目") or "").strip()
    if not stem:
        return None

    options = item.get("options") or item.get("选项") or []
    if isinstance(options, str):
        options = [o.strip() for o in re.split(r"[\n;；]+", options) if o.strip()]
    if not isinstance(options, list):
        options = []
    options = [str(o).strip() for o in options if str(o).strip()]

    points = item.get("knowledge_points") or item.get("知识点") or []
    if isinstance(points, str):
        points = [p.strip() for p in re.split(r"[,，、;；/\s]+", points) if p.strip()]
    if not isinstance(points, list):
        points = []
    points = [str(p).strip() for p in points if str(p).strip()]

    qtype = normalize_qtype(item.get("qtype") or item.get("type") or item.get("题型"))
    if qtype == "answer" and options:
        qtype = "choice"

    return {
        "stem": stem,
        "options": options,
        "answer": str(item.get("answer") or item.get("答案") or "").strip(),
        "analysis": str(item.get("analysis") or item.get("解析") or "").strip(),
        "qtype": qtype,
        "knowledge_points": points,
        "difficulty": normalize_difficulty(item.get("difficulty") or item.get("难度")),
    }


def _split_chunks(text: str, size: int) -> List[str]:
    """按段落切分长文本，单段超长时硬切"""
    size = max(1000, int(size or 6000))
    if len(text) <= size:
        return [text]

    chunks: List[str] = []
    buf = ""
    for para in text.split("\n"):
        if len(buf) + len(para) + 1 > size and buf:
            chunks.append(buf)
            buf = ""
        if len(para) > size:
            for i in range(0, len(para), size):
                chunks.append(para[i:i + size])
            continue
        buf = f"{buf}\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def _heuristic_extract(text: str, max_questions: int) -> List[Dict[str, Any]]:
    """启发式抽取：按「1. / (1) / 第1题」等题号切分

    仅用于 LLM 不可用时的兜底，不含答案与解析。
    """
    lines = [ln.rstrip() for ln in text.split("\n")]
    blocks: List[List[str]] = []
    current: List[str] = []

    for line in lines:
        stripped = line.strip()
        is_start = bool(_QUESTION_START.match(stripped)) and not _OPTION_LINE.match(stripped)
        if is_start:
            if current:
                blocks.append(current)
            current = [stripped]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)

    questions: List[Dict[str, Any]] = []
    for block in blocks:
        body = "\n".join(block).strip()
        if len(body) < 8:
            continue
        raw_lines = [ln.strip() for ln in block]
        stem_lines: List[str] = []
        options: List[str] = []
        for ln in raw_lines:
            if _OPTION_LINE.match(ln):
                options.append(ln)
            else:
                stem_lines.append(ln)
        stem = "\n".join(stem_lines).strip()
        if not stem:
            continue
        # 去掉行首题号，题干更干净
        stem = _QUESTION_START.sub(lambda m: (m.group(2) or m.group(4) or "").strip(), stem, count=1)
        if len(stem) < 6:
            continue
        if options:
            qtype = "choice"
        elif re.search(r"[_＿]{2,}|（\s*）|\(\s*\)", stem):
            qtype = "fill"
        elif re.search(r"(正确|错误|对错|判断)", stem):
            qtype = "judge"
        else:
            qtype = "answer"
        questions.append({
            "stem": stem,
            "options": options,
            "answer": "",
            "analysis": "",
            "qtype": qtype,
            "knowledge_points": [],
            "difficulty": 3,
        })
        if len(questions) >= max_questions:
            break
    return questions